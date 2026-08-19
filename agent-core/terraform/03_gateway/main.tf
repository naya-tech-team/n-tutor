# 03 — Cognito and hr-gateway, with exactly one target.
#
# The Gateway is a protocol adapter, not a router. A Lambda cannot speak MCP, so
# it needs one. hr_skills_mcp already can, and it is reached directly instead —
# there is deliberately no `hrskills` target here. See architecture.md,
# "Why two MCP connections and not one".
#
# The tool schemas below are the tax for that translation: the same shapes exist
# in app/lambda_fn/handler.py, and nothing checks the two against each other.
#
#   terraform apply -var bucket=... -var lambda_arn=...

terraform {
    required_providers {
        aws = {
            source  = "hashicorp/aws"
            version = ">= 6.58"
        }
        time = {
            source  = "hashicorp/time"
            version = ">= 0.9"
        }
    }
}

variable "region" {
    type    = string
    default = "us-west-2"
}

variable "env" {
    type    = string
    default = "dev"
}

variable "lambda_arn" {
    type        = string
    description = "terraform output -raw lambda_arn, from 02_lambda"
}

variable "username" {
    type        = string
    description = "The one machine user that mints bearer tokens for this stack."
    default     = "hr-agent"
}

variable "password" {
    type        = string
    description = "Password for that user. Min 8 chars. Put it in a *.tfvars that is not example.tfvars — the root .gitignore ignores those."
    sensitive   = true
}

variable "gateway_name" {
    type        = string
    description = "Names the gateway and its role, and scopes the trust condition."
    default     = "hr-gateway"
}

variable "gateway_authorizer_type" {
    type        = string
    description = <<-EOT
        How callers prove who they are to the gateway. AWS_IAM (SigV4) or
        CUSTOM_JWT (a Cognito bearer token).

        AWS_IAM by default, and not merely as a preference: the only caller is
        talent_screening, which builds its MCP client at container start-up — when
        no request and so no token exists — and no container can obtain a Cognito
        token anyway, because AgentCore consumes the inbound Authorization header
        at its edge. CUSTOM_JWT here means a machine password in the container.

        **Immutable.** Changing this replaces the gateway and all of its targets,
        and the replacement has a different gateway_url.
    EOT
    default     = "AWS_IAM"

    validation {
        condition     = contains(["AWS_IAM", "CUSTOM_JWT"], var.gateway_authorizer_type)
        error_message = "gateway_authorizer_type must be AWS_IAM or CUSTOM_JWT."
    }
}

variable "iam_propagation_delay" {
    type        = string
    description = "How long to wait after creating the gateway role before CreateGatewayTarget assumes it. Raise it if the AssumeRole error still appears."
    default     = "30s"
}

variable "restrict_trust_to_gateway" {
    type        = bool
    description = "Add the aws:SourceAccount / aws:SourceArn conditions to the role trust policy. Leave false for the first apply — the gateway ARN does not exist yet — then set true and re-apply."
    default     = false
}

# The region the provider actually deployed into. Using var.region here instead
# would let a discovery URL name a different region than the pool lives in —
# and the resulting 401 says nothing about regions.
data "aws_region" "current" {}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }
}

# --- Identity: who may call the tools ---------------------------------------

resource "aws_cognito_user_pool" "hr" {
    name = "hr-agents-${var.env}"
    password_policy {
        minimum_length = 8
    }
    tags = local.common_tags
}

resource "aws_cognito_user_pool_client" "hr" {
    name                = "hr-agents-client"
    user_pool_id        = aws_cognito_user_pool.hr.id
    generate_secret     = false
    explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

# Without this, every runtime and the gateway sit behind CUSTOM_JWT with no way
# to mint a token: the stack deploys and nothing is callable. `password` (rather
# than `temporary_password`) is what makes it usable without an interactive
# password-change challenge on first auth.
resource "aws_cognito_user" "agent" {
    user_pool_id = aws_cognito_user_pool.hr.id
    username     = var.username
    password     = var.password

    message_action = "SUPPRESS" # no welcome email to a machine account
}

# --- The gateway's own identity, used to reach the Lambda -------------------

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "assume" {
    statement {
        sid     = "GatewayAssumeRolePolicy"
        effect  = "Allow"
        actions = ["sts:AssumeRole"]
        principals {
            type        = "Service"
            identifiers = ["bedrock-agentcore.amazonaws.com"]
        }

        # AWS documents these as a best practice AND says to omit them on the
        # first create, because you cannot know the gateway ARN before the role
        # that creates it exists. Off by default for exactly that reason.
        #
        # Turn them on for a second apply once the gateway is up. Both values are
        # derived, never typed: a hardcoded region here is a known way to produce
        # "Gateway service is not authorized to perform AssumeRole" — the
        # condition silently stops matching and the trust policy does nothing.
        dynamic "condition" {
            for_each = var.restrict_trust_to_gateway ? [1] : []
            content {
                test     = "StringEquals"
                variable = "aws:SourceAccount"
                values   = [data.aws_caller_identity.current.account_id]
            }
        }

        dynamic "condition" {
            for_each = var.restrict_trust_to_gateway ? [1] : []
            content {
                test     = "ArnLike"
                variable = "aws:SourceArn"
                values   = ["arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:gateway/${var.gateway_name}-*"]
            }
        }
    }
}

data "aws_iam_policy_document" "invoke" {
    statement {
        effect    = "Allow"
        actions   = ["lambda:InvokeFunction"]
        resources = [var.lambda_arn]
    }
}

resource "aws_iam_role" "gateway" {
    name               = "${var.gateway_name}-role"
    assume_role_policy = data.aws_iam_policy_document.assume.json
    tags               = local.common_tags
}

resource "aws_iam_role_policy" "invoke" {
    role   = aws_iam_role.gateway.id
    policy = data.aws_iam_policy_document.invoke.json
}

# IAM is eventually consistent, and this is where that bites.
#
# CreateGateway only stores the role ARN, so it succeeds immediately.
# CreateGatewayTarget actually *assumes* the role to reach the Lambda — and if
# the trust policy has not propagated yet, it fails with:
#
#   ValidationException: Gateway service is not authorized to perform
#   AssumeRole on Gateway role. Update trust policy and retry
#
# The message points at the trust policy, which is why this wastes an afternoon:
# the policy is correct, it just does not exist everywhere yet. "and retry" is
# the real instruction. This waits instead.
resource "time_sleep" "iam_propagation" {
    depends_on = [aws_iam_role.gateway, aws_iam_role_policy.invoke]

    create_duration = var.iam_propagation_delay
}

# --- The gateway ------------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "hr" {
    name     = var.gateway_name
    role_arn = aws_iam_role.gateway.arn

    # Not decorative. Nothing else makes the gateway wait for the role's trust
    # policy to exist everywhere, because it only references the role's ARN —
    # which terraform knows the instant the role is created.
    depends_on = [time_sleep.iam_propagation]

    # AWS_IAM, so the caller signs with SigV4 — the same way it reaches every
    # runtime but the supervisor.
    #
    # CUSTOM_JWT here looks natural and is a dead end. The only caller is
    # talent_screening, which builds this MCP client **at container start-up**,
    # when no request and therefore no token exists; and nothing in a container
    # can obtain a Cognito token anyway, because AgentCore consumes the caller's
    # Authorization header at its edge and never passes it through. A JWT gateway
    # would mean either a machine password in the container or restructuring the
    # toolset to open a connection per request.
    #
    # SigV4 removes the problem rather than working around it: there is no token
    # to be missing at start-up, because each request is signed as it is sent and
    # botocore refreshes the role's credentials on its own.
    #
    # **`authorizer_type` is immutable.** Changing it replaces the gateway and
    # every target under it, and the new gateway has a new URL — which is why
    # `gateway_url` is an output that 05_runtimes consumes rather than anything
    # written down by hand.
    authorizer_type = var.gateway_authorizer_type

    dynamic "authorizer_configuration" {
        for_each = var.gateway_authorizer_type == "CUSTOM_JWT" ? [1] : []
        content {
            custom_jwt_authorizer {
                discovery_url   = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.hr.id}/.well-known/openid-configuration"
                allowed_clients = [aws_cognito_user_pool_client.hr.id]
            }
        }
    }

    protocol_type = "MCP"
    protocol_configuration {
        mcp {
            instructions = "HR skills matching: employee records, open requisitions, bench availability and shortlist decisions."

            # Four tools do not need semantic search. It is set anyway because it
            # can ONLY be enabled at creation — turning it on later means
            # recreating the gateway and every target under it.
            search_type = "SEMANTIC"
        }
    }

    tags = local.common_tags
}

# --- The one target ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "hrdata" {
    name               = "hrdata"
    gateway_identifier = aws_bedrockagentcore_gateway.hr.gateway_id
    description        = "Employee, requisition and shortlist data in S3, via Lambda."

    # This is the call that actually assumes the gateway role.
    depends_on = [time_sleep.iam_propagation]

    credential_provider_configuration {
        gateway_iam_role {}
    }

    target_configuration {
        mcp {
            lambda {
                lambda_arn = var.lambda_arn

                tool_schema {
                    # The agent sees these as hrdata___find_by_skill and friends.
                    # Three underscores; the handler strips the prefix itself.
                    inline_payload {
                        name        = "find_by_skill"
                        description = "Find employees with a skill at or above a level. Accepts aliases: 'pyspark' resolves to 'Apache Spark'."
                        input_schema {
                            type = "object"
                            property {
                                name        = "skill"
                                type        = "string"
                                description = "Skill name or alias, e.g. 'Python' or 'pyspark'"
                                required    = true
                            }
                            property {
                                name        = "min_level"
                                type        = "integer"
                                description = "Minimum proficiency 1-5. Default 3."
                            }
                            property {
                                name        = "available_only"
                                type        = "boolean"
                                description = "Only people on the bench. Default true."
                            }
                        }
                    }

                    inline_payload {
                        name        = "get_requisition"
                        description = "Get one open requisition and the skills it requires, with min_level, mandatory and weight."
                        input_schema {
                            type = "object"
                            property {
                                name        = "job_id"
                                type        = "string"
                                description = "Requisition id, e.g. 'J2001'"
                                required    = true
                            }
                        }
                    }

                    inline_payload {
                        name        = "list_bench"
                        description = "Everyone currently unallocated, optionally filtered to one location."
                        input_schema {
                            type = "object"
                            property {
                                name        = "location"
                                type        = "string"
                                description = "Optional city, e.g. 'Bengaluru'"
                            }
                        }
                    }

                    inline_payload {
                        name        = "record_shortlist"
                        description = "Record a shortlist decision for a requisition. Refuses candidates whose verdict is 'blocked'."
                        input_schema {
                            type = "object"
                            property {
                                name     = "job_id"
                                type     = "string"
                                required = true
                            }
                            property {
                                name     = "employee_id"
                                type     = "string"
                                required = true
                            }
                            property {
                                name        = "score"
                                type        = "integer"
                                description = "The score from hr_skills_mcp. Do not invent one."
                            }
                            property {
                                name        = "verdict"
                                type        = "string"
                                description = "strong, possible, weak or blocked — exactly as scoring returned it."
                            }
                        }
                    }

                    inline_payload {
                        name        = "get_shortlist"
                        description = "Who has been shortlisted for a requisition so far."
                        input_schema {
                            type = "object"
                            property {
                                name     = "job_id"
                                type     = "string"
                                required = true
                            }
                        }
                    }
                }
            }
        }
    }
}

output "gateway_url" {
    value       = aws_bedrockagentcore_gateway.hr.gateway_url
    description = "Set this as GATEWAY_URL in agent-core/.env"
}

output "gateway_id" {
    value = aws_bedrockagentcore_gateway.hr.gateway_id
}

output "gateway_arn" {
    value       = aws_bedrockagentcore_gateway.hr.gateway_arn
    description = "Pass to 06_observability as -var gateway_arn=..."
}

output "bearer_token_command" {
    description = "Only needed to curl the supervisor by hand, or if you set gateway_authorizer_type = CUSTOM_JWT."
    value = <<-EOT
        export BEARER_TOKEN=$(aws cognito-idp initiate-auth \
          --client-id ${aws_cognito_user_pool_client.hr.id} \
          --auth-flow USER_PASSWORD_AUTH \
          --auth-parameters USERNAME=${var.username},PASSWORD=<the password you set> \
          --region ${data.aws_region.current.region} \
          --query 'AuthenticationResult.IdToken' --output text)

        IdToken, NOT AccessToken. The supervisor authorizes on `allowed_audience`,
        which matches an ID token's `aud` claim; an access token carries
        `client_id` instead and is rejected with a bare 401. The two tokens look
        identical in a terminal, which is what makes this worth stating.

        The supervisor is the only consumer. The other four runtimes and — by
        default — this gateway take SigV4, so this token is for the front door and
        for nothing between services.
    EOT
}

output "cognito_discovery_url" {
    value = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.hr.id}/.well-known/openid-configuration"
}

output "cognito_client_id" {
    value = aws_cognito_user_pool_client.hr.id
}

output "cognito_user_pool_id" {
    value       = aws_cognito_user_pool.hr.id
    description = "07_api's proxy needs this for the pool's JWKS URL, and its Cognito authorizer needs the ARN derived from it."
}
