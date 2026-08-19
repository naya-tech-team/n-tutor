# 07 — the supervisor, exposed: a streaming Lambda behind a streaming API Gateway.
#
# This layer has no browser in it. It answers one question — how does an HTTP
# client reach an AgentCore runtime? — and the chat UI in 08 is only its first
# caller. curl, another service or a CI job use the same two routes.
#
#     POST /api/login    open, necessarily: it is where you get the token
#     POST /api/chat     Cognito authorizer, then the proxy, then the supervisor
#
# Two constraints put a Lambda in the middle rather than letting anything call the
# runtime directly:
#
#   A browser cannot call InvokeAgentRuntime. It is a SigV4 AWS API call and AWS
#   endpoints send no CORS headers, so even a Cognito identity pool handing the
#   page real credentials fails at the preflight.
#
#   That proxy cannot be Python. Lambda response streaming works on Node.js
#   managed runtimes and custom runtimes only. See ui/proxy/index.mjs.
#
# And until November 2025 API Gateway could not have fronted it at all: it
# buffered every Lambda response and fixed the integration timeout at 29 seconds,
# while a full run is three remote delegations and one to two minutes.
# `response_transfer_mode = "STREAM"` is what changed that.
#
# Note it is the *streaming* that matters, not the timeout — a BUFFERED
# integration now allows 300 seconds, which is long enough to finish, and would
# still show the caller nothing until the last delegation returned.
#
#   terraform apply -var supervisor_arn=... -var cognito_user_pool_id=... \
#                   -var cognito_client_id=...

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

variable "supervisor_arn" {
    type        = string
    description = "terraform output -raw supervisor_arn, from 05_runtimes. The ONLY runtime this proxy may invoke."
}

variable "cognito_user_pool_id" {
    type        = string
    description = "terraform output -raw cognito_user_pool_id, from 03_gateway"
}

variable "cognito_client_id" {
    type        = string
    description = "terraform output -raw cognito_client_id, from 03_gateway"
}

variable "proxy_zip" {
    type        = string
    description = "The chat proxy zip, relative to this module. Built by `uv run scripts/package.py chat_proxy`."
    default     = "../../dist/chat_proxy.zip"
}

variable "retention_days" {
    type    = number
    default = 30
}

variable "stage_name" {
    type        = string
    description = "API Gateway stage. It becomes a path segment on the execute-api URL, which is why 08_ui sets CloudFront's origin_path to it — so the browser never sees it."
    default     = "v1"
}

variable "proxy_timeout_seconds" {
    type        = number
    description = <<-EOT
        Ceiling on one answer, applied to the Lambda and to the API Gateway
        integration together. A full run is three remote delegations, so 300 is
        generous rather than tight.

        The provider allows 900 with response_transfer_mode = STREAM and 300 with
        BUFFERED. Both are far past the 29 seconds REST integrations were fixed at
        before November 2025 — which is the era this API could not have existed in.
    EOT
    default     = 300
}

variable "throttle_rate" {
    type        = number
    description = "Steady-state requests per second across the API. Each chat request runs an agent for a minute or two and spends Bedrock tokens, so this is a cost control, not a capacity one."
    default     = 5
}

variable "throttle_burst" {
    type    = number
    default = 10
}

variable "manage_apigw_account_logging" {
    type        = bool
    description = <<-EOT
        Set the API Gateway CloudWatch Logs role for this whole account and
        region — not just for this API. Without it, creating a stage that logs
        fails with:

          BadRequestException: CloudWatch Logs role ARN must be set in account
          settings to enable logging

        It is one setting per region, shared by every REST API in it, and the
        provider does not document what `terraform destroy` does to it. **Set
        this false in an account you do not own.** You then get the API with no
        access logs and no execution logs, rather than a failed apply — and the
        stage's `$context.authorizer.error` field, which is the thing that
        explains a 401, goes with them.

        If the setting already exists in your account, leaving this true makes
        terraform take ownership of it.
    EOT
    default     = true
}

variable "iam_propagation_delay" {
    type        = string
    description = "How long to wait after creating the CloudWatch Logs role before API Gateway is asked to use it. Same knob, same reason, as 03_gateway and 06_observability."
    default     = "30s"
}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # path.module cannot appear in a variable default — defaults must be literals
    # — so the variable holds the relative part and this joins it. path.module is
    # this directory in both modes, which is what makes the module work standalone
    # AND as a child of 00_all_at_once.
    zip = "${path.module}/${var.proxy_zip}"

    # The pool ARN, rebuilt from its id. 03_gateway exports the id because that is
    # what the proxy needs for the JWKS URL; the authorizer wants the ARN, and
    # deriving it here beats adding a second output that can disagree.
    user_pool_arn = "arn:aws:cognito-idp:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:userpool/${var.cognito_user_pool_id}"

    # NOT the ordinary lambda proxy URI. Streaming integrations invoke through
    # InvokeWithResponseStream, which is a different API version *and* a different
    # action — `2021-11-15` and `/response-streaming-invocations` rather than
    # `2015-03-31` and `/invocations`.
    #
    # Pairing STREAM with the ordinary URI is not a graceful degradation to
    # buffered: API Gateway returns a 500.
    streaming_uri = join("", [
        "arn:aws:apigateway:${data.aws_region.current.region}:lambda:path/2021-11-15/functions/",
        aws_lambda_function.proxy.arn,
        "/response-streaming-invocations",
    ])
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# --- the proxy --------------------------------------------------------------

data "aws_iam_policy_document" "proxy_assume" {
    statement {
        effect  = "Allow"
        actions = ["sts:AssumeRole"]
        principals {
            type        = "Service"
            identifiers = ["lambda.amazonaws.com"]
        }
    }
}

# Logs, and nothing else.
#
# There is deliberately no `bedrock-agentcore:InvokeAgentRuntime` here. The
# runtimes use CUSTOM_JWT inbound auth, so the proxy reaches them over plain HTTPS
# with the caller's bearer token — not with SigV4 — and an IAM permission it never
# exercises would be a misleading grant rather than a harmless one.
#
# If you ever switch the runtimes to SigV4 inbound auth, this is the statement to
# add back, scoped to `[supervisor_arn, "${supervisor_arn}/*"]`. The second ARN is
# the easy one to miss: invoking without a qualifier targets the DEFAULT
# *endpoint*, whose ARN is the runtime ARN plus a suffix.
data "aws_iam_policy_document" "proxy" {
    statement {
        sid       = "Logs"
        effect    = "Allow"
        actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        resources = ["${aws_cloudwatch_log_group.proxy.arn}:*"]
    }
}

resource "aws_iam_role" "proxy" {
    name               = "hr-chat-proxy-role-${var.env}"
    assume_role_policy = data.aws_iam_policy_document.proxy_assume.json
    tags               = local.common_tags
}

resource "aws_iam_role_policy" "proxy" {
    role   = aws_iam_role.proxy.id
    policy = data.aws_iam_policy_document.proxy.json
}

# Created here rather than left to the service, so the retention is ours. A
# Lambda-created group defaults to never expiring.
resource "aws_cloudwatch_log_group" "proxy" {
    name              = "/aws/lambda/hr-chat-proxy-${var.env}"
    retention_in_days = var.retention_days
    tags              = local.common_tags
}

resource "aws_lambda_function" "proxy" {
    function_name = "hr-chat-proxy-${var.env}"
    role          = aws_iam_role.proxy.arn
    handler       = "index.handler"
    runtime       = "nodejs22.x"
    architectures = ["arm64"]

    filename         = local.zip
    source_code_hash = filebase64sha256(local.zip)

    # The supervisor's three delegations run inside one invocation, so this is the
    # real ceiling on an answer. The API Gateway integration below is set from the
    # same variable: a lower timeout there would cut off a function still working.
    timeout     = var.proxy_timeout_seconds
    memory_size = 512

    environment {
        variables = {
            AGENT_RUNTIME_ARN    = var.supervisor_arn
            COGNITO_USER_POOL_ID = var.cognito_user_pool_id
            COGNITO_CLIENT_ID    = var.cognito_client_id
        }
    }

    depends_on = [aws_cloudwatch_log_group.proxy]
    tags       = local.common_tags
}

# --- the API ----------------------------------------------------------------
#
# What API Gateway buys over exposing the Lambda's own function URL: throttling
# and burst limits, access logs, a custom domain if you want one, WAF, and — the
# reason it is worth a layer of its own — a Cognito authorizer that rejects
# unauthenticated traffic *before* a Lambda is invoked.
#
# What it costs: the endpoint is publicly resolvable. A function URL can be
# OAC-locked to one CloudFront distribution; `execute-api` cannot, because
# CloudFront OAC has no apigateway origin type. The authorizer is the gate now,
# which is the trade you make when you expose an API on purpose.

resource "aws_api_gateway_rest_api" "chat" {
    name        = "hr-chat-${var.env}"
    description = "The hiring supervisor, exposed. /api/login is open; /api/chat needs a Cognito ID token."

    endpoint_configuration {
        types = ["REGIONAL"]
    }

    tags = local.common_tags
}

# An **ID** token, not an access token.
#
# A COGNITO_USER_POOLS authorizer has two modes and the docs describe them in one
# sentence each: identity claims (the ID token) or custom scopes (the access
# token). Leaving `authorization_scopes` unset selects the first, so an access
# token here is the configuration that half-works and fails per-request. Using
# access tokens instead would mean a Cognito resource server, a custom scope, and
# that scope allowed on the app client — three resources to avoid one word.
resource "aws_api_gateway_authorizer" "cognito" {
    name          = "hr-cognito-${var.env}"
    rest_api_id   = aws_api_gateway_rest_api.chat.id
    type          = "COGNITO_USER_POOLS"
    provider_arns = [local.user_pool_arn]

    identity_source = "method.request.header.Authorization"
}

# /api, then /api/chat and /api/login. The paths match what the React calls and
# what 08_ui routes, so neither has to know this module's shape.
resource "aws_api_gateway_resource" "api" {
    rest_api_id = aws_api_gateway_rest_api.chat.id
    parent_id   = aws_api_gateway_rest_api.chat.root_resource_id
    path_part   = "api"
}

resource "aws_api_gateway_resource" "route" {
    for_each = toset(["chat", "login"])

    rest_api_id = aws_api_gateway_rest_api.chat.id
    parent_id   = aws_api_gateway_resource.api.id
    path_part   = each.value
}

resource "aws_api_gateway_method" "chat" {
    rest_api_id   = aws_api_gateway_rest_api.chat.id
    resource_id   = aws_api_gateway_resource.route["chat"].id
    http_method   = "POST"
    authorization = "COGNITO_USER_POOLS"
    authorizer_id = aws_api_gateway_authorizer.cognito.id
}

# Open, necessarily: this is where you GET the token, so it cannot require one.
# It is also the only unauthenticated compute in the stack, which is why the
# throttle below is worth setting rather than leaving to the account default.
resource "aws_api_gateway_method" "login" {
    rest_api_id   = aws_api_gateway_rest_api.chat.id
    resource_id   = aws_api_gateway_resource.route["login"].id
    http_method   = "POST"
    authorization = "NONE"
}

resource "aws_api_gateway_integration" "route" {
    # Both methods take the identical integration — one Lambda, which routes on
    # the path itself. They are separate resources above only because their
    # `authorization` differs.
    for_each = {
        chat  = aws_api_gateway_method.chat
        login = aws_api_gateway_method.login
    }

    rest_api_id = aws_api_gateway_rest_api.chat.id
    resource_id = each.value.resource_id
    http_method = each.value.http_method

    type = "AWS_PROXY"
    # Always POST, whatever the client used. This is the method API Gateway uses
    # to call Lambda, not the method it accepts.
    integration_http_method = "POST"
    uri                     = local.streaming_uri

    # The whole reason this API can front the supervisor at all.
    response_transfer_mode = "STREAM"

    # Matched to the Lambda's own timeout, from the same variable. A value below
    # it would cut a slow answer off while the function was still working, and the
    # client would see a truncated stream rather than an error.
    # Ceiling is 900,000 with STREAM, 300,000 with BUFFERED.
    timeout_milliseconds = var.proxy_timeout_seconds * 1000
}

resource "aws_lambda_permission" "apigw" {
    statement_id  = "AllowAPIGatewayInvoke"
    action        = "lambda:InvokeFunction"
    function_name = aws_lambda_function.proxy.function_name
    principal     = "apigateway.amazonaws.com"

    # Any method, any path on this API — and nothing else.
    source_arn = "${aws_api_gateway_rest_api.chat.execution_arn}/*/*"
}

# A deployment is a snapshot, and terraform cannot see inside it. Without these
# triggers you change a method, apply cleanly, and serve the previous version —
# the single most common way an API Gateway change appears not to have worked.
resource "aws_api_gateway_deployment" "chat" {
    rest_api_id = aws_api_gateway_rest_api.chat.id

    triggers = {
        redeploy = sha1(jsonencode([
            aws_api_gateway_resource.api,
            aws_api_gateway_resource.route,
            aws_api_gateway_method.chat,
            aws_api_gateway_method.login,
            aws_api_gateway_integration.route,
            aws_api_gateway_authorizer.cognito,
        ]))
    }

    lifecycle {
        create_before_destroy = true
    }
}

# --- the account-wide logging prerequisite -----------------------------------
#
# API Gateway does not write to CloudWatch Logs with the stage's own permissions.
# It assumes a role recorded in **account settings**, one per region, shared by
# every REST API in it — and a stage that logs cannot be created until that role
# exists:
#
#   BadRequestException: CloudWatch Logs role ARN must be set in account settings
#   to enable logging
#
# The error names the account, which is unusually helpful; what it does not say
# is that the fix is a resource nothing in your API refers to.
#
# This is the second account-scoped thing in this stack, after Transaction Search
# in 06. Same shape, same warning: see `manage_apigw_account_logging`.

data "aws_iam_policy_document" "apigw_assume" {
    count = var.manage_apigw_account_logging ? 1 : 0

    statement {
        effect  = "Allow"
        actions = ["sts:AssumeRole"]
        principals {
            type        = "Service"
            identifiers = ["apigateway.amazonaws.com"]
        }
    }
}

resource "aws_iam_role" "apigw_cloudwatch" {
    count = var.manage_apigw_account_logging ? 1 : 0

    name               = "hr-apigw-cloudwatch-${var.env}"
    assume_role_policy = data.aws_iam_policy_document.apigw_assume[0].json
    tags               = local.common_tags
}

# The AWS-managed policy for exactly this. Writing the permissions out by hand is
# a way to get them subtly wrong — it needs CreateLogGroup and DescribeLogGroups,
# not just PutLogEvents.
resource "aws_iam_role_policy_attachment" "apigw_cloudwatch" {
    count = var.manage_apigw_account_logging ? 1 : 0

    role       = aws_iam_role.apigw_cloudwatch[0].name
    policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

# Third time in this stack. API Gateway validates that it can assume the role at
# the moment you set it, and a role created seconds earlier has not propagated —
# so this fails with a message about the role's permissions rather than about
# timing. See 03_gateway and 06_observability for the same fix.
resource "time_sleep" "apigw_account" {
    count = var.manage_apigw_account_logging ? 1 : 0

    depends_on      = [aws_iam_role_policy_attachment.apigw_cloudwatch]
    create_duration = var.iam_propagation_delay
}

resource "aws_api_gateway_account" "this" {
    count = var.manage_apigw_account_logging ? 1 : 0

    cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch[0].arn

    depends_on = [time_sleep.apigw_account]
}

resource "aws_cloudwatch_log_group" "api" {
    name              = "/aws/apigateway/hr-chat-${var.env}"
    retention_in_days = var.retention_days
    tags              = local.common_tags
}

resource "aws_api_gateway_stage" "chat" {
    rest_api_id   = aws_api_gateway_rest_api.chat.id
    deployment_id = aws_api_gateway_deployment.chat.id
    stage_name    = var.stage_name

    # Nothing in the arguments below refers to the account setting, so depends_on
    # is what orders them. Creating a logging stage before the role is recorded is
    # the BadRequestException above.
    depends_on = [aws_api_gateway_account.this]

    # Gated on the same flag as the account setting, and it has to be: asking for
    # access logs without the role is precisely what fails. `false` gives you a
    # working API with no logs instead of no API.
    dynamic "access_log_settings" {
        for_each = var.manage_apigw_account_logging ? [1] : []
        content {
            destination_arn = aws_cloudwatch_log_group.api.arn
            format = jsonencode({
                requestId      = "$context.requestId"
                ip             = "$context.identity.sourceIp"
                routeKey       = "$context.resourcePath"
                status         = "$context.status"
                responseLength = "$context.responseLength"
                latency        = "$context.responseLatency"
                # The two that actually explain a 401: which one rejected the
                # token, and why. Without them a Cognito authorizer failure is a
                # bare 401 with nothing anywhere to say what was wrong with it.
                authorizerError = "$context.authorizer.error"
                errorMessage    = "$context.error.message"
            })
        }
    }

    tags = local.common_tags
}

resource "aws_api_gateway_method_settings" "throttle" {
    rest_api_id = aws_api_gateway_rest_api.chat.id
    stage_name  = aws_api_gateway_stage.chat.stage_name
    method_path = "*/*"

    settings {
        throttling_rate_limit  = var.throttle_rate
        throttling_burst_limit = var.throttle_burst

        # ERROR, not INFO: full request/response execution logging on a streaming
        # endpoint is a lot of CloudWatch for a chat transcript you already have.
        #
        # OFF when the account role is not ours to set — execution logging needs
        # it just as access logging does, and asking for either without it fails
        # the apply rather than degrading.
        logging_level = var.manage_apigw_account_logging ? "ERROR" : "OFF"
    }
}

# --- outputs ----------------------------------------------------------------
# The first two are what 08_ui needs to point CloudFront here. They are the
# domain and the stage separately rather than the invoke URL, because CloudFront
# wants them in two different arguments and splitting a URL back apart in HCL is
# how you end up with `https://` in a `domain_name`.

output "api_domain" {
    value       = "${aws_api_gateway_rest_api.chat.id}.execute-api.${data.aws_region.current.region}.amazonaws.com"
    description = "Pass to 08_ui as -var api_domain=... — CloudFront's origin domain_name."
}

output "api_stage" {
    value       = aws_api_gateway_stage.chat.stage_name
    description = "Pass to 08_ui as -var api_stage=... — CloudFront's origin_path."
}

output "proxy_log_group" {
    value       = aws_cloudwatch_log_group.proxy.name
    description = "Where a failed invoke explains itself."
}

output "api_log_group" {
    value = aws_cloudwatch_log_group.api.name
    description = <<-EOT
        Where a 401 explains itself — $context.authorizer.error is in the access
        log format. The group is created either way, but it stays EMPTY when
        manage_apigw_account_logging is false: API Gateway cannot write to
        CloudWatch without the account-level role.
    EOT
}

output "api_url" {
    value       = aws_api_gateway_stage.chat.invoke_url
    description = <<-EOT
        The supervisor, exposed directly. The browser does not use this — it goes
        through CloudFront in 08_ui, same origin — but curl and any other client can:

          TOKEN=$(curl -s $API/api/login -d '{"username":"hr-agent","password":"..."}' \
                    -H 'content-type: application/json' | jq -r .token)
          curl -N $API/api/chat -H "Authorization: $TOKEN" \
               -H 'content-type: application/json' \
               -d '{"prompt":"Find the best candidate for J2001"}'

        The bare token, with no "Bearer " prefix. The authorizer is documented
        only as "include the token in the Authorization header", and the bare form
        is the one that reliably passes — a prefix gets you a 401 with nothing in
        it to say why. The proxy strips an optional prefix regardless.
    EOT
}
