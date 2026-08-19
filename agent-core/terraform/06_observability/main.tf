# 06 — logs and traces.
#
# Runtime creates its own log group. Gateway and Memory DO NOT — and until you
# wire the delivery below, the busiest component in the system is the only silent
# one. That asymmetry is the single most surprising thing in AgentCore
# observability, so it gets its own terraform step.
#
# Transaction Search is the prerequisite for all of it, and it is a switch for
# the whole account and region rather than for this stack. `enable_transaction_search`
# below throws it, so this is one apply and no CLI step — but read that variable
# before you run it, because it is the one thing in this repo that reaches
# outside its own stack.
#
# With it on, the GenAI Observability dashboard shows one trace per requisition
# across all five runtimes, because every hop carries the same
# X-Amzn-Bedrock-AgentCore-Runtime-Session-Id.

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

variable "gateway_arn" {
    type        = string
    description = "from 03_gateway"
}

variable "memory_arn" {
    type        = string
    description = "from 04_memory"
}

variable "retention_days" {
    type    = number
    default = 30
}

variable "enable_transaction_search" {
    type        = bool
    description = <<-EOT
        Point X-Ray trace segments at CloudWatch Logs for this whole account and
        region — not just for this stack. Required for the traces delivery below;
        without it CreateDelivery fails with "X-Ray Delivery Destination is
        supported with CloudWatch Logs as a Trace Segment Destination".

        Set false in a shared account you do not own. You still get application
        logs; the three traces resources are simply not created, rather than
        created and broken.

        It bills: spans are ingested as CloudWatch Logs. And `terraform destroy`
        sets the account back to X-Ray-only, which will silently stop traces for
        anything ELSE in the account that was relying on it.
    EOT
    default     = true
}

variable "iam_propagation_delay" {
    type        = string
    description = "How long to wait after writing the CloudWatch Logs resource policy before X-Ray is asked to use it. Raise it if the AccessDeniedException on aws/spans still appears. Same knob as 03_gateway."
    default     = "30s"
}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # The console's own default naming, kept so console-created and
    # terraform-created deliveries do not end up in two different places.
    vended = {
        gateway = { arn = var.gateway_arn, id = "hr-gateway" }
        memory  = { arn = var.memory_arn, id = "hr_hiring_desk" }
    }

    # Gates all three traces resources on one flag, so `false` leaves nothing
    # half-built. Logs are unaffected: they do not go near X-Ray.
    traced = var.enable_transaction_search ? local.vended : {}
}

resource "aws_cloudwatch_log_group" "vended" {
    for_each = local.vended

    name              = "/aws/vendedlogs/bedrock-agentcore/${each.key}/APPLICATION_LOGS/${each.value.id}"
    retention_in_days = var.retention_days
    tags              = local.common_tags
}

# Delivery is three resources, not one: a source (what emits), a destination
# (where it lands), and the delivery that joins them.

resource "aws_cloudwatch_log_delivery_source" "logs" {
    for_each = local.vended

    name         = "${each.key}-logs-source"
    log_type     = "APPLICATION_LOGS"
    resource_arn = each.value.arn
}

resource "aws_cloudwatch_log_delivery_destination" "logs" {
    for_each = local.vended

    name                      = "${each.key}-logs-destination"
    delivery_destination_type = "CWL"
    output_format             = "json"

    delivery_destination_configuration {
        destination_resource_arn = aws_cloudwatch_log_group.vended[each.key].arn
    }

    tags = local.common_tags
}

resource "aws_cloudwatch_log_delivery" "logs" {
    for_each = local.vended

    delivery_source_name     = aws_cloudwatch_log_delivery_source.logs[each.key].name
    delivery_destination_arn = aws_cloudwatch_log_delivery_destination.logs[each.key].arn
    tags                     = local.common_tags
}

# --- Transaction Search -----------------------------------------------------
#
# CloudWatch Transaction Search, as two resources. The docs and the console make
# it look like one switch:
#
#   aws xray update-trace-segment-destination --destination CloudWatchLogs
#
# It is two. Clicking "enable" in the console also writes a CloudWatch Logs
# resource policy behind your back, and the API call does not: it just fails.
#
#   AccessDeniedException: XRay does not have permission to call PutLogEvents
#   on the aws/spans Log Group
#
# The CLI is not an option on a machine behind a TLS-inspecting proxy either — it
# fails with CERTIFICATE_VERIFY_FAILED where terraform, whose Go TLS reads the
# macOS keychain, works fine. So both halves live here.
#
# Scope is the thing to notice: every other resource in this repo belongs to this
# stack, and these two belong to the account. See the variable's description.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Half one: let X-Ray write spans.
#
# This is a RESOURCE policy, attached to the log groups rather than to a role —
# which is why there is no role anywhere here, and why searching IAM for the
# permission that is missing turns up nothing.
#
# aws/spans is where Transaction Search puts trace segments. The
# application-signals group is granted alongside it because AWS documents the
# pair together and Application Signals shares the same grant; leaving it out
# works right up until you turn Application Signals on.
data "aws_iam_policy_document" "xray_spans" {
    count = var.enable_transaction_search ? 1 : 0

    statement {
        sid     = "TransactionSearchXRayAccess"
        effect  = "Allow"
        actions = ["logs:PutLogEvents"]

        principals {
            type        = "Service"
            identifiers = ["xray.amazonaws.com"]
        }

        # Neither group is created here. The service makes aws/spans on first
        # write, and a policy may name a log group that does not exist yet.
        resources = [
            "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:aws/spans:*",
            "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/application-signals/data:*",
        ]

        # The confused-deputy pair. Unlike the gateway trust policy in 03, these
        # can be set on the first apply: both values are known before anything
        # exists, so there is no chicken-and-egg to opt out of.
        condition {
            test     = "ArnLike"
            variable = "aws:SourceArn"
            values   = ["arn:aws:xray:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"]
        }

        condition {
            test     = "StringEquals"
            variable = "aws:SourceAccount"
            values   = [data.aws_caller_identity.current.account_id]
        }
    }
}

resource "aws_cloudwatch_log_resource_policy" "xray_spans" {
    count = var.enable_transaction_search ? 1 : 0

    policy_name     = "TransactionSearchXRayAccess"
    policy_document = data.aws_iam_policy_document.xray_spans[0].json
}

# Resource policies propagate the same way trust policies do, and X-Ray checks
# this one synchronously during the call below. Same failure mode as the gateway
# role in 03, same fix. See `iam_propagation_delay` there.
resource "time_sleep" "policy_propagation" {
    count = var.enable_transaction_search ? 1 : 0

    depends_on      = [aws_cloudwatch_log_resource_policy.xray_spans]
    create_duration = var.iam_propagation_delay
}

# Half two: the switch itself. Fails with the 403 above unless half one landed
# first, and nothing in its arguments says so.
resource "aws_xray_trace_segment_destination" "cwl" {
    count = var.enable_transaction_search ? 1 : 0

    depends_on = [time_sleep.policy_propagation]

    destination = "CloudWatchLogs"
}

# --- Traces -----------------------------------------------------------------
#
# Same three-part shape as logs, but the destination is X-Ray, and X-Ray is not a
# resource you point at: `delivery_destination_type = "XRAY"` and NO
# delivery_destination_configuration block. Passing the gateway's own ARN there —
# the obvious first guess — creates a destination that accepts the apply and
# delivers nothing.

resource "aws_cloudwatch_log_delivery_source" "traces" {
    for_each = local.traced

    name         = "${each.key}-traces-source"
    log_type     = "TRACES"
    resource_arn = each.value.arn
}

resource "aws_cloudwatch_log_delivery_destination" "traces" {
    for_each = local.traced

    name                      = "${each.key}-traces-destination"
    delivery_destination_type = "XRAY"

    tags = local.common_tags
}

# The piece that was missing entirely. A source and a destination are two halves
# of nothing until a delivery joins them — and the symptom is not an error, it is
# an empty GenAI Observability dashboard.
#
# This is also the resource that fails if Transaction Search is off. The source
# and the destination above are both created happily first, which is why the
# error arrives last and names neither of them:
#
#   ValidationException: X-Ray Delivery Destination is supported with CloudWatch
#   Logs as a Trace Segment Destination
#
# Nothing in the arguments below refers to the switch, so depends_on is what
# orders them.
resource "aws_cloudwatch_log_delivery" "traces" {
    for_each = local.traced

    depends_on = [aws_xray_trace_segment_destination.cwl]

    delivery_source_name     = aws_cloudwatch_log_delivery_source.traces[each.key].name
    delivery_destination_arn = aws_cloudwatch_log_delivery_destination.traces[each.key].arn
    tags                     = local.common_tags
}

output "log_groups" {
    value = { for k, g in aws_cloudwatch_log_group.vended : k => g.name }
}

# Deliberately NOT named `runtime_log_groups`. 05 has a real output by that name
# — a map of runtime to log group — and two outputs sharing a name across modules
# is how you end up reading a signpost as data. It is also what the name-matching
# in `check_terraform_chain.py` resolves against, so a collision can make that
# audit answer about the wrong module.
output "runtime_logs_note" {
    # Not here, and not created by terraform anywhere: the runtimes' groups are
    # made by the service, because their names contain a service-generated id.
    value       = "runtime logs are `terraform output runtime_log_groups` on 05_runtimes"
    description = "This module wires the GATEWAY and MEMORY deliveries only."
}

output "transaction_search" {
    value = var.enable_transaction_search ? "on — traces land in CloudWatch Logs, GenAI Observability will populate" : "OFF — logs only. No traces delivery was created (enable_transaction_search = false)."
}
