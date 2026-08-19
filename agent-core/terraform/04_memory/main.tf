# 05 — AgentCore Memory, keyed the way the business is keyed.
#
#   actorId   = the recruiter
#   sessionId = the requisition (J2001)
#
# That is lesson 17's rule carried through unchanged: session_id is a business
# key, not plumbing. One conversation per open requisition. Pick these badly and
# everything downstream is wrong in a way that looks like a model problem —
# sessionId = uuid4() gives you a system that forgets J2001 between Tuesday and
# Thursday.
#
# Note the `type` values. Terraform wants SEMANTIC / SUMMARIZATION /
# USER_PREFERENCE; boto3 wants semanticMemoryStrategy / summaryMemoryStrategy /
# userPreferenceMemoryStrategy. You will meet both, often in the same afternoon,
# and searching the docs for one will not find the other.

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

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }
}

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

resource "aws_iam_role" "memory" {
    name               = "hr-memory-role"
    assume_role_policy = data.aws_iam_policy_document.assume.json
    tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "inference" {
    # Long-term extraction is a model call. Without this the memory resource
    # exists, events land, and no memory record is ever produced.
    role       = aws_iam_role.memory.name
    policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy"
}

resource "aws_bedrockagentcore_memory" "hiring_desk" {
    name                      = "hr_hiring_desk"
    memory_execution_role_arn = aws_iam_role.memory.arn

    # A requisition that has been open a quarter is a requisition in trouble;
    # 90 days of raw events is long enough to see that and short enough to bound.
    event_expiry_duration = 90

    tags = local.common_tags
}

# These three strategies each run a MODEL CALL on the service side — long-term
# memory is not storage, it is extraction — and none of them names a model, so
# AgentCore uses its own default.
#
# That matters in an account without Anthropic model access, because the default
# is an Anthropic model and extraction then fails with:
#
#   ResourceNotFoundException: Model use case details have not been submitted
#   for this account
#
# from a component nobody was watching, asynchronously, after the agent has
# already answered.
#
# Pinning it is not one line. `configuration.extraction` requires BOTH `model_id`
# and `append_to_prompt`, so overriding the model means replacing AWS's tuned
# extraction prompt with your own — a real change to what gets remembered, not a
# configuration detail. Worth doing deliberately; not worth doing to silence an
# error you have not confirmed came from here.
#
# To test whether memory is the source at all: unset MEMORY_ID on the supervisor.
# `_session_manager()` returns None and the whole memory path is skipped.
resource "aws_bedrockagentcore_memory_strategy" "facts" {
    name                = "candidate_facts"
    memory_id           = aws_bedrockagentcore_memory.hiring_desk.id
    type                = "SEMANTIC"
    description         = "Facts about candidates and requisitions, e.g. 'E1005 is blocked on Python and SQL for J2001'"
    namespace_templates = ["/requisitions/{sessionId}/facts"]
}

resource "aws_bedrockagentcore_memory_strategy" "preferences" {
    name                = "recruiter_preferences"
    memory_id           = aws_bedrockagentcore_memory.hiring_desk.id
    type                = "USER_PREFERENCE"
    description         = "How this recruiter works, e.g. 'never shortlists below 70%'"
    namespace_templates = ["/recruiters/{actorId}/preferences"]
}

resource "aws_bedrockagentcore_memory_strategy" "summaries" {
    name        = "requisition_summary"
    memory_id   = aws_bedrockagentcore_memory.hiring_desk.id
    type        = "SUMMARIZATION"
    description = "A week of work on one requisition, in a paragraph"
    # Read these for context, never for verdicts. A summariser will paraphrase
    # "blocked on Apache Spark" into "some gaps", and that is how a blocked
    # candidate gets a warm note next Tuesday.
    namespace_templates = ["/summaries/{actorId}/{sessionId}"]
}

output "memory_id" {
    value       = aws_bedrockagentcore_memory.hiring_desk.id
    description = "Set this as MEMORY_ID in agent-core/.env"
}

output "memory_arn" {
    value = aws_bedrockagentcore_memory.hiring_desk.arn
}
