# 05 — the five runtimes.
#
# Read the three resource blocks below as three tiers of a dependency graph:
#
#   leaf        hr_skills_mcp, recruiting_outreach, people_compliance — need nobody
#   screening   needs the MCP server's ARN
#   supervisor  needs all three specialists' ARNs
#
# An earlier version put all five in one for_each, which cannot work: a resource
# cannot reference its own for_each siblings, so the peer ARNs had to be set by a
# second apply. Splitting along the tiers lets terraform build the DAG itself and
# the whole thing lands in one `apply`.
#
# **The environment variable names are load-bearing.** They are read by
# app/_shared/config.py, and a typo does not fail — `agent_url()` quietly falls
# back to http://127.0.0.1:9001, which inside a container reaches nothing.
#
#   uv run scripts/package.py                 # build the six zips first
#   terraform apply -var-file=example.tfvars

terraform {
    required_providers {
        aws = {
            source  = "hashicorp/aws"
            version = ">= 6.58"
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

variable "bucket" {
    type        = string
    description = "terraform output -raw bucket, from 01_s3_data"
}

variable "bucket_arn" {
    type        = string
    description = "terraform output -raw bucket_arn, from 01_s3_data"
}

variable "gateway_url" {
    type        = string
    description = "terraform output -raw gateway_url, from 03_gateway"
}

variable "cognito_discovery_url" {
    type        = string
    description = "from 03_gateway"
}

variable "cognito_client_id" {
    type        = string
    description = "from 03_gateway"
}

variable "memory_id" {
    type        = string
    description = "terraform output -raw memory_id, from 04_memory"
}

variable "bedrock_model_id" {
    type        = string
    description = "Account- and region-specific. Anthropic models need a use-case form approved per account first; Amazon's Nova models do not, which is why the examples use one."
}

variable "gateway_arn" {
    type        = string
    description = "terraform output -raw gateway_arn, from 03_gateway. Scopes the screener's bedrock-agentcore:InvokeGateway to the one gateway."
}

variable "dist_dir" {
    type    = string
    default = "../../dist"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # path.module, not a bare relative path — see 01_s3_data for why. Cannot go
    # in the variable default; Terraform requires those to be literals.
    dist = "${path.module}/${var.dist_dir}"

    # One hash per artifact, computed once. It goes into the S3 key, which is what
    # makes a rebuilt zip reach the container — see aws_s3_object.artifact.
    #
    # Evaluated at PLAN time like every other file function here, so `dist/` must
    # already hold the zips. `make artifacts` is what builds them.
    artifact_hash = { for name, _ in local.protocols : name => filemd5("${local.dist}/${name}.zip") }

    protocols = {
        hr_skills_mcp       = "MCP"
        recruiting_outreach = "A2A"
        people_compliance   = "A2A"
        talent_screening    = "A2A"
        hiring_supervisor   = "HTTP"
    }

    # The call graph. Every edge one runtime is allowed to traverse, and nothing
    # else — `data.aws_iam_policy_document.runtime` turns this straight into IAM.
    #
    # Read it as the answer to "who can talk to whom": the supervisor fans out to
    # its three specialists, the screener reaches the MCP server, and the three
    # A2A leaves call nobody at all. `recruiting_outreach` and `people_compliance`
    # are absent on purpose — they receive work and answer, and a leaf that can
    # invoke its own caller is a loop waiting for a prompt that suggests it.
    callees = {
        hiring_supervisor = ["talent_screening", "recruiting_outreach", "people_compliance"]
        talent_screening  = ["hr_skills_mcp"]
    }

    # `agent_runtime_name` is ours; the `-XXXXXXXXXX` suffix is the service's.
    runtime_arn_pattern = {
        for name, _ in local.protocols :
        name => format(
            "arn:aws:bedrock-agentcore:%s:%s:runtime/%s-*",
            data.aws_region.current.region,
            data.aws_caller_identity.current.account_id,
            name,
        )
    }

    # Why every authorizer below says allowed_audience rather than allowed_clients.
    #
    # These are two different checks against two different claims, and a Cognito
    # ID token only carries one of them:
    #
    #   allowed_audience  matches `aud`        -> ID tokens
    #   allowed_clients   matches `client_id`  -> access tokens
    #
    # This stack uses ID tokens end to end, because an API Gateway Cognito
    # authorizer with no authorization_scopes is the identity-claims path and
    # wants exactly that (see 07_api). Setting allowed_clients here therefore
    # rejects the very token the front door just accepted, with:
    #
    #   Authorization method mismatch. The agent is configured for a different
    #   authorization method than what was used in your request.
    #
    # Set one or the other, never both — an ID token has no client_id claim to
    # match, so a config demanding both can never be satisfied.

    # Every runtime gets these. The per-tier blocks below merge in the ARNs of
    # whatever they need to talk to.
    base_env = {
        AGENTCORE        = "true"
        MODEL_PROVIDER   = "bedrock"
        BEDROCK_MODEL_ID = var.bedrock_model_id
        DATA_SOURCE      = "s3"
        S3_BUCKET        = var.bucket
        GATEWAY_URL      = var.gateway_url

        AGENT_OBSERVABILITY_ENABLED        = "true"
        UNIFIED_TRACES_DESTINATION_ENABLED = "true"

        # `aws-opentelemetry-distro` installs ~45 instrumentation packages, and
        # `opentelemetry-instrument` walks every one of them before the server
        # binds. That is spent against a hard budget:
        #
        #   Runtime initialization time exceeded. Please make sure that
        #   initialization completes in 30s.
        #
        # This is the list of things none of these five containers has ever
        # talked to. What stays instrumented is what this system actually uses:
        # botocore (Bedrock), httpx (A2A and MCP), and asgi/starlette/fastapi
        # (the servers themselves).
        #
        # Nothing breaks if you shorten this list — it just costs start-up time.
        OTEL_PYTHON_DISABLED_INSTRUMENTATIONS = join(",", [
            "aio-pika", "aiokafka", "aiopg", "asyncpg", "boto3sqs", "cassandra",
            "celery", "confluent-kafka", "dbapi", "django", "falcon", "flask",
            "grpc", "jinja2", "kafka-python", "mysql", "mysqlclient", "pika",
            "psycopg2", "pymemcache", "pymongo", "pymysql", "pyramid", "redis",
            "remoulade", "sqlalchemy", "sqlite3", "tornado", "tortoiseorm",
            "aws-lambda", "openai-agents-v2",
        ])
    }
}

# --- Artifacts --------------------------------------------------------------

# The content hash is IN THE KEY, and that is the whole point.
#
# A runtime references its code as bucket + prefix. With a fixed key like
# `artifacts/hiring_supervisor.zip`, rebuilding the zip changes the bytes in S3
# and changes **nothing** terraform can see on the runtime — same bucket, same
# prefix, no diff, no update, no new version. The apply succeeds, the object is
# replaced, and the container goes on running the code it started with.
#
# That failure is completely silent. `terraform apply` reports changes (the S3
# object updated), the artifact in the bucket is genuinely new, and every code fix
# you make is live in S3 and dead in production. The only visible symptom is a
# runtime whose `agent_runtime_version` never moves.
#
# Putting the hash in the key makes the dependency real: new bytes mean a new key,
# a new key means the runtime's `prefix` changes, and a changed prefix is a new
# runtime version running the code you just built.
#
# It also fixes something quieter. AgentCore runtime versions are immutable — but
# overwriting the object in place made version 4's "immutable" code change
# underneath it. Content-addressed keys mean a version always points at the bytes
# it was created with.
#
# The cost is that old artifacts accumulate. They are a few tens of MB each and
# every live runtime version still references one, so do not blanket-expire them:
# prune by hand once you know which versions are retired.
# No `etag`. It is the obvious thing to set and it is wrong here, in a way that
# only shows up once the artifacts get big.
#
# These zips are ~34 MB, so the provider uploads them multipart, and S3 answers
# with an etag of the form `<md5-of-the-part-md5s>-<partcount>` — `dbf0d032...-7`.
# That can never equal `filemd5()`, so terraform stores one value and compares
# against the other, and every plan reports five objects to update:
#
#   Plan: 0 to add, 5 to change, 0 to destroy.
#
# forever, re-uploading 170 MB each time. Worse than the bandwidth: it destroys
# the signal. The whole point of the content-addressed key above is that a plan
# tells you whether code actually changed, and a permanent diff means it cannot.
#
# Nothing is lost by dropping it, because the key already carries the hash: new
# content is a new key, which is a new object and a new runtime version. `etag`
# would be the mechanism if the key were fixed — and a fixed key is the bug that
# kept every deployment stuck at version 4.
resource "aws_s3_object" "artifact" {
    for_each = local.protocols

    bucket = var.bucket
    key    = "artifacts/${each.key}-${local.artifact_hash[each.key]}.zip"
    source = "${local.dist}/${each.key}.zip"
    tags   = local.common_tags
}

# --- Execution roles --------------------------------------------------------

data "aws_iam_policy_document" "assume" {
    statement {
        effect  = "Allow"
        actions = ["sts:AssumeRole"]
        principals {
            type        = "Service"
            identifiers = ["bedrock-agentcore.amazonaws.com"]
        }
    }
}

data "aws_iam_policy_document" "runtime" {
    for_each = local.protocols

    statement {
        # Read its own deployment artifact.
        effect    = "Allow"
        actions   = ["s3:GetObject"]
        resources = ["${var.bucket_arn}/artifacts/*"]
    }

    statement {
        effect    = "Allow"
        actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        resources = ["*"]
    }

    statement {
        effect    = "Allow"
        # CreateLogGroup is the one that is easy to leave out, and leaving it out
        # is silent. With only CreateLogStream and PutLogEvents a runtime can
        # write into a log group that already exists and cannot bring one into
        # being — so the console says:
        #
        #   /aws/bedrock-agentcore/runtimes/<id>-DEFAULT does not exist in this
        #   account or region
        #
        # and every other failure in the container becomes undiagnosable, because
        # the place you would read about it is the thing that is missing.
        #
        # This is the ONLY thing that creates these groups. Terraform cannot: the
        # name contains a service-generated runtime id, so it does not exist until
        # the runtime does — see the note further down.
        #
        # Left on `"*"` deliberately. The same unknowable id is in the log group
        # ARN, so scoping would need the `-*` pattern trick used for InvokeAgentRuntime
        # — and if that pattern were ever wrong, the thing that breaks is the place
        # you would read about it breaking. Diagnosability wins here.
        actions = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:DescribeLogGroups",
            "logs:DescribeLogStreams",
            "logs:PutLogEvents",
            "logs:PutResourcePolicy",
        ]
        resources = ["*"]
    }

    # Traces. `AGENT_OBSERVABILITY_ENABLED=true` puts an OTLP exporter in every
    # container, and without these it runs and fails on every batch:
    #
    #   Failed to export span batch code: 403, reason: Forbidden
    #
    # Non-fatal, which is the trap — the agent answers normally and the GenAI
    # Observability dashboard simply stays empty, so the 403 only appears if you
    # go and read the container's own log. Logging permissions do not cover this:
    # spans go to X-Ray, not to CloudWatch Logs.
    statement {
        sid    = "Traces"
        effect = "Allow"
        actions = [
            "xray:PutTraceSegments",
            "xray:PutTelemetryRecords",
            # The sampler calls these on start-up to fetch its rules. Without
            # them the exporter falls back to a local default and logs about it.
            "xray:GetSamplingRules",
            "xray:GetSamplingTargets",
        ]
        resources = ["*"]
    }

    # Metrics, scoped by namespace rather than by resource — PutMetricData takes
    # no resource ARN, so the condition is the only thing that bounds it.
    statement {
        sid       = "Metrics"
        effect    = "Allow"
        actions   = ["cloudwatch:PutMetricData"]
        resources = ["*"]

        condition {
            test     = "StringEquals"
            variable = "cloudwatch:namespace"
            values   = ["bedrock-agentcore"]
        }
    }

    # Read the records. Every runtime, and the reason is one line of Python:
    # `install()` sits at module scope in all five entrypoints, and with
    # DATA_SOURCE=s3 it fetches skills, employees and requisitions on import.
    #
    # This was scoped to hr_skills_mcp alone, on the reasoning that only the
    # scoring engine has any business reading the estate. True of the design,
    # false of the code — the other four crashed before binding a port:
    #
    #   AccessDenied ... not authorized to perform: s3:GetObject on resource:
    #   ".../skills/skills.json" because no identity-based policy allows it
    #
    # An import-time exception means the container never becomes healthy, so the
    # symptom is a start-up failure rather than anything mentioning S3.
    #
    # **GetObject only.** That is the property worth keeping, and the one the
    # original comment was really about: a scoring engine that can edit the
    # employee record is one nobody will trust. Nothing here can write.
    statement {
        effect    = "Allow"
        actions   = ["s3:GetObject"]
        resources = [
            "${var.bucket_arn}/employees/*",
            "${var.bucket_arn}/requisitions/*",
            "${var.bucket_arn}/skills/*",
        ]
    }

    # No shortlist access for any runtime, deliberately. `shortlists/` is written
    # only by hr-data-fn, through the Gateway — hr_skills_mcp's `shortlist` tool
    # ranks candidates in memory and never touches S3. See 02_lambda's role.

    # Who may invoke whom — the call graph, written as IAM.
    #
    # **This is the whole authorization model between services now.** While the
    # inner runtimes were CUSTOM_JWT, a token was the gate and this grant was
    # belt-and-braces; moving them to SigV4 made IAM the only check there is. A
    # `resources = ["*"]` here, which is what this used to say, let the outreach
    # agent invoke the supervisor and the screener invoke anything in the account.
    # The comments claimed "only the supervisor may delegate". The policy did not.
    #
    # Name patterns rather than the runtime ARNs themselves, and that is forced.
    # Referencing `aws_bedrockagentcore_agent_runtime.*.agent_runtime_arn` here
    # would make this policy depend on the runtimes — but the runtimes depend on
    # this policy in the way that matters: `screening_toolset()` wraps
    # `a2a_serve.serve()`, so the screener opens its MCP connections during
    # container start, which happens while CreateAgentRuntime is still waiting for
    # the health check. Invert the order and the container cannot reach
    # hr_skills_mcp, never goes healthy, and the apply fails on a timeout that
    # says nothing about IAM.
    #
    # The id is generated (`talent_screening-qi5b1O3IDE`), so the pattern ends in
    # `-*`. One pattern covers the endpoints too — IAM's `*` spans `/`, so this
    # matches `.../runtime-endpoint/DEFAULT` as well.
    dynamic "statement" {
        for_each = contains(keys(local.callees), each.key) ? [1] : []
        content {
            sid    = "InvokeItsOwnCalleesOnly"
            effect = "Allow"
            actions = [
                "bedrock-agentcore:InvokeAgentRuntime",

                # **GetAgentCard is a separate action, and it is not optional.**
                #
                # An A2A conversation is two calls: fetch the card, then send the
                # message. `InvokeAgentRuntime` covers only the second. Grant it
                # alone and every delegation dies at discovery with
                #
                #   GET .../invocations/.well-known/agent-card.json 403 Forbidden
                #
                # which reads like the remote agent is down. It is not; it is this
                # line missing. `aws iam simulate-principal-policy` says
                # `implicitDeny` for GetAgentCard next to `allowed` for Invoke,
                # which is the fastest way to confirm it.
                #
                # Same trap as InvokeGateway below: one conversation, several
                # actions, and the failure names none of them.
                "bedrock-agentcore:GetAgentCard",
            ]
            resources = [for callee in local.callees[each.key] : local.runtime_arn_pattern[callee]]
        }
    }

    # Memory. Still `*`: scoping it needs `memory_arn`, which 04 outputs and this
    # module does not take. A deliberate follow-up, not an oversight.
    dynamic "statement" {
        for_each = each.key == "hiring_supervisor" ? [1] : []
        content {
            effect = "Allow"
            actions = [
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetEvent",
                "bedrock-agentcore:ListSessions",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord",
            ]
            resources = ["*"]
        }
    }

    # The screener reaches hr_skills_mcp directly, not through the gateway — that
    # edge is in `local.callees` above, with everything else.

    # hr-data-fn goes through the gateway, which is a separate action from
    # invoking a runtime. The gateway runs AWS_IAM inbound auth, so this is what
    # the screener's SigV4 signature is checked against — scoped to the one
    # gateway, because `*` here would be every gateway in the account.
    dynamic "statement" {
        for_each = each.key == "talent_screening" ? [1] : []
        content {
            sid       = "InvokeTheHRGateway"
            effect    = "Allow"
            actions   = ["bedrock-agentcore:InvokeGateway"]
            resources = [var.gateway_arn, "${var.gateway_arn}/*"]
        }
    }
}

resource "aws_iam_role" "runtime" {
    for_each = local.protocols

    name               = "agentcore-${each.key}-role"
    assume_role_policy = data.aws_iam_policy_document.assume.json
    tags               = local.common_tags
}

resource "aws_iam_role_policy" "runtime" {
    for_each = local.protocols

    role   = aws_iam_role.runtime[each.key].id
    policy = data.aws_iam_policy_document.runtime[each.key].json
}

# --- Tier 1: the runtimes that depend on nobody ------------------------------

resource "aws_bedrockagentcore_agent_runtime" "leaf" {
    for_each = toset(["hr_skills_mcp", "recruiting_outreach", "people_compliance"])

    agent_runtime_name = each.key
    role_arn           = aws_iam_role.runtime[each.key].arn

    agent_runtime_artifact {
        code_configuration {
            # Two elements: the ADOT wrapper, then the entrypoint. Drop the first
            # and you lose every span — the agent still works, silently.
            entry_point = ["opentelemetry-instrument", "main.py"]
            runtime     = "PYTHON_3_13"
            code {
                s3 {
                    bucket = var.bucket
                    prefix = aws_s3_object.artifact[each.key].key
                }
            }
        }
    }

    network_configuration {
        network_mode = "PUBLIC"
    }

    protocol_configuration {
        server_protocol = local.protocols[each.key]
    }

    # No authorizer_configuration, deliberately — this runtime uses **SigV4**.
    #
    # It is only ever called by another runtime inside this account, and an AWS
    # principal signing with its execution role is the natural credential for
    # that. CUSTOM_JWT here would need a Cognito token, and nothing in a container
    # can obtain one: AgentCore consumes the caller's Authorization header at its
    # edge and never passes it through, so there is nothing to forward and no way
    # to mint without shipping a password in the environment.
    #
    # The supervisor keeps CUSTOM_JWT. It is the front door, and the thing on the
    # other side of it is a person.

    environment_variables = local.base_env

    tags = local.common_tags
}

# --- Tier 2: the screener needs the MCP server -------------------------------

resource "aws_bedrockagentcore_agent_runtime" "screening" {
    agent_runtime_name = "talent_screening"
    role_arn           = aws_iam_role.runtime["talent_screening"].arn

    agent_runtime_artifact {
        code_configuration {
            entry_point = ["opentelemetry-instrument", "main.py"]
            runtime     = "PYTHON_3_13"
            code {
                s3 {
                    bucket = var.bucket
                    prefix = aws_s3_object.artifact["talent_screening"].key
                }
            }
        }
    }

    network_configuration {
        network_mode = "PUBLIC"
    }

    protocol_configuration {
        server_protocol = local.protocols["talent_screening"]
    }

    # No authorizer_configuration, deliberately — this runtime uses **SigV4**.
    #
    # It is only ever called by another runtime inside this account, and an AWS
    # principal signing with its execution role is the natural credential for
    # that. CUSTOM_JWT here would need a Cognito token, and nothing in a container
    # can obtain one: AgentCore consumes the caller's Authorization header at its
    # edge and never passes it through, so there is nothing to forward and no way
    # to mint without shipping a password in the environment.
    #
    # The supervisor keeps CUSTOM_JWT. It is the front door, and the thing on the
    # other side of it is a person.

    environment_variables = merge(local.base_env, {
        # Read by clients/tools.py. Without it the container raises
        # "AGENTCORE=true needs SKILLS_MCP_ARN and GATEWAY_URL" and never serves.
        SKILLS_MCP_ARN = aws_bedrockagentcore_agent_runtime.leaf["hr_skills_mcp"].agent_runtime_arn
    })

    tags = local.common_tags
}

# --- Tier 3: the supervisor needs all three specialists ----------------------

resource "aws_bedrockagentcore_agent_runtime" "supervisor" {
    agent_runtime_name = "hiring_supervisor"
    role_arn           = aws_iam_role.runtime["hiring_supervisor"].arn

    agent_runtime_artifact {
        code_configuration {
            entry_point = ["opentelemetry-instrument", "main.py"]
            runtime     = "PYTHON_3_13"
            code {
                s3 {
                    bucket = var.bucket
                    prefix = aws_s3_object.artifact["hiring_supervisor"].key
                }
            }
        }
    }

    network_configuration {
        network_mode = "PUBLIC"
    }

    protocol_configuration {
        server_protocol = local.protocols["hiring_supervisor"]
    }

    authorizer_configuration {
        custom_jwt_authorizer {
            discovery_url = var.cognito_discovery_url

            # allowed_audience, NOT allowed_clients — see the note in `locals`.
            allowed_audience = [var.cognito_client_id]
        }
    }

    environment_variables = merge(local.base_env, {
        # Read by clients/a2a_call.py:agent_url(). Miss one and that delegation
        # silently addresses 127.0.0.1 inside this container, which reaches nothing.
        SCREENING_ARN  = aws_bedrockagentcore_agent_runtime.screening.agent_runtime_arn
        OUTREACH_ARN   = aws_bedrockagentcore_agent_runtime.leaf["recruiting_outreach"].agent_runtime_arn
        COMPLIANCE_ARN = aws_bedrockagentcore_agent_runtime.leaf["people_compliance"].agent_runtime_arn

        # actor = recruiter, session = requisition. Set in runtimes/hiring_supervisor.
        MEMORY_ID = var.memory_id
    })

    tags = local.common_tags
}

# --- Endpoints --------------------------------------------------------------
#
# There are none here, deliberately.
#
# CreateAgentRuntime creates a DEFAULT endpoint for you, as part of the same
# call. Declaring one named DEFAULT therefore does not adopt it — it tries to
# create a second endpoint by that name, and every apply fails with:
#
#   ConflictException: An endpoint with the specified name already exists
#
# This one is not eventual consistency and waiting will not help: the endpoint
# genuinely exists, made microseconds earlier by the resource directly above.
#
# Nothing is lost by leaving it out. `invoke-agent-runtime` with a runtime ARN
# and no --qualifier uses DEFAULT, which is what clients/a2a_call.py does and
# what `invoke_command` below prints.
#
# Endpoints are worth declaring when you want a NAMED one — a `prod` alias you
# repoint between runtime versions, so callers keep one ARN across deploys:
#
#   resource "aws_bedrockagentcore_agent_runtime_endpoint" "prod" {
#       name             = "prod"          # any name but DEFAULT
#       agent_runtime_id = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_id
#   }
#
# This stack has one version of each runtime, so that alias would point at the
# only thing it could point at.

# --- Log groups -------------------------------------------------------------
#
# **Not created here. Terraform cannot win this race, by construction.**
#
# The name is `/aws/bedrock-agentcore/runtimes/{agent_runtime_id}-DEFAULT`, and
# `agent_runtime_id` is generated by the service — `hiring_supervisor-Kt7PF58OuC`.
# So the group can only be declared *after* the runtime exists, and creating the
# runtime is what starts the container that creates the group. Terraform arrives
# second, every time:
#
#   ResourceAlreadyExistsException: The specified log group already exists
#
# 07_api has the same fight and wins it, which is what makes the difference worth
# stating: a Lambda's group is `/aws/lambda/{function-name}`, a name known before
# the function exists, so `depends_on` puts terraform first. There is no
# equivalent here, because the id is not knowable in advance.
#
# What actually fixed the original "log group does not exist" was granting the
# execution role `logs:CreateLogGroup` — see the policy above. With that the
# container makes its own group as it starts and writes its start-up failure into
# it, which is the case this was ever meant to cover.
#
# The cost is retention: a service-created group never expires. If that matters,
# it is one idempotent command that needs no terraform state:
#
#   terraform output -json runtime_log_groups | jq -r '.[]' | xargs -I{} \
#     aws logs put-retention-policy --log-group-name {} --retention-in-days 30
locals {
    runtime_ids = merge(
        { for k, r in aws_bedrockagentcore_agent_runtime.leaf : k => r.agent_runtime_id },
        {
            talent_screening  = aws_bedrockagentcore_agent_runtime.screening.agent_runtime_id
            hiring_supervisor = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_id
        },
    )
}

output "runtime_log_groups" {
    value       = { for k, id in local.runtime_ids : k => "/aws/bedrock-agentcore/runtimes/${id}-DEFAULT" }
    description = "Where each container's stdout lands. Read hiring_supervisor's first when an invoke fails. Computed, not managed — the service owns these groups."
}

output "runtime_arns" {
    value = merge(
        { for k, r in aws_bedrockagentcore_agent_runtime.leaf : k => r.agent_runtime_arn },
        {
            talent_screening  = aws_bedrockagentcore_agent_runtime.screening.agent_runtime_arn
            hiring_supervisor = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_arn
        },
    )
}

output "supervisor_arn" {
    value       = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_arn
    description = "The only runtime you invoke from outside."
}

output "invoke_command" {
    description = <<-EOT
        curl, NOT `aws bedrock-agentcore invoke-agent-runtime`.

        The SUPERVISOR uses CUSTOM_JWT inbound auth, and the CLI and every AWS SDK
        sign SigV4 — AWS documents that an OAuth-configured agent cannot be
        invoked through them at all. The CLI fails with "Authorization method
        mismatch", and the console's test button fails the same way.

        Only the supervisor. The other four take SigV4, so for those the CLI is
        not merely allowed but the only option: they have no token to be sent.

        This token buys the front door and nothing else. **It is not forwarded.**
        AgentCore consumes `Authorization` at its edge and never passes it to the
        container, so the supervisor never sees the token you sent — delegation is
        SigV4 with its execution role, and works whether you sent one or not.

        The entrypoint is an async generator, so this streams `data: {...}` frames
        rather than returning one JSON object.
    EOT
    value = <<-EOT
        # 1. An ID token. Not an access token — see 03_gateway's bearer_token_command.
        export BEARER_TOKEN=<terraform output -raw bearer_token_command, then run it>

        # 2. The ARN, URL-encoded whole: colons AND slashes.
        ARN='${aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_arn}'
        ENCODED=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$ARN")

        # 3. Session id: 33 characters MINIMUM, or ValidationException.
        curl -N "https://bedrock-agentcore.${data.aws_region.current.region}.amazonaws.com/runtimes/$ENCODED/invocations" \
          -H "Authorization: Bearer $BEARER_TOKEN" \
          -H "Content-Type: application/json" \
          -H "Accept: text/event-stream" \
          -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: J2001-000000000000000000000000000000" \
          -d '{"prompt":"Find the best candidate for J2001"}'
    EOT
}
