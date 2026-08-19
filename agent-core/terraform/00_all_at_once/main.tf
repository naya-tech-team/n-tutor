# 00 — the whole stack, in one apply.
#
# The eight numbered directories are the same eight modules, called here as children.
# Nothing is copied by hand: `module.gateway.gateway_url` feeding
# `module.runtimes.gateway_url` IS the dependency, so terraform works out the
# order itself and applies them in it.
#
# The numbered directories still work standalone — they declare no provider, so
# terraform builds a default one from AWS_REGION / ~/.aws/config. Use them when
# you want to stop after a layer and look at what you made; use this when you
# just want the system.
#
#   uv run scripts/package.py && uv run scripts/seed_s3.py
#   terraform init && terraform apply -var-file=my.tfvars
#
# or, from the repo root: `make deploy`.
#
# To stop after one layer from here instead:
#   terraform apply -target=module.s3

terraform {
    required_providers {
        aws = {
            source  = "hashicorp/aws"
            version = ">= 6.58"
        }
    }
}

# The one provider configuration for the whole stack. This is why the children
# no longer declare their own: a child module with a provider block is legacy
# behaviour that terraform accepts, warns about, and gets destroy ordering wrong.
provider "aws" {
    region = var.region
}

variable "region" {
    type    = string
    default = "us-west-2"
}

variable "env" {
    type    = string
    default = "dev"
}

variable "password" {
    type        = string
    description = "Cognito password for the machine user that mints bearer tokens. Min 8 chars."
    sensitive   = true
}

variable "bedrock_model_id" {
    type        = string
    description = <<-EOT
        No default — model ids are account- and region-specific.

        Anthropic models on Bedrock need a one-off use-case form per account, and
        until it is approved every call fails with "Model use case details have
        not been submitted for this account". Amazon's own models do not, which is
        why the examples use Nova.

        To see what this account has, when the CLI is unavailable:
          data.aws_bedrock_inference_profiles.all.inference_profile_summaries
    EOT
}

variable "price_class" {
    type        = string
    description = "CloudFront edge coverage for the chat UI. PriceClass_100 is North America + Europe and the cheapest; PriceClass_All adds the rest of the world."
    default     = "PriceClass_100"
}

variable "enable_transaction_search" {
    type        = bool
    description = <<-EOT
        Point X-Ray trace segments at CloudWatch Logs for the whole account and
        region. Required for the traces delivery in 06; set false in a shared
        account you do not own and you get logs without traces, rather than a
        failed apply. See 06_observability/main.tf — it bills, and `destroy`
        turns it back off account-wide.
    EOT
    default     = true
}

# --- The eight layers -------------------------------------------------------
# Read the arguments, not the order: the references are the graph.

module "s3" {
    source = "../01_s3_data"
    region = var.region
    env    = var.env
}

module "lambda" {
    source     = "../02_lambda"
    region     = var.region
    env        = var.env
    bucket     = module.s3.bucket
    bucket_arn = module.s3.bucket_arn
}

module "gateway" {
    source     = "../03_gateway"
    region     = var.region
    env        = var.env
    lambda_arn = module.lambda.lambda_arn
    password   = var.password
}

# Memory depends on nothing but its own IAM role, and the supervisor needs its
# id as an environment variable — which is why it is 04 and the runtimes are 05.
module "memory" {
    source = "../04_memory"
    region = var.region
    env    = var.env
}

module "runtimes" {
    source                = "../05_runtimes"
    region                = var.region
    env                   = var.env
    bucket                = module.s3.bucket
    bucket_arn            = module.s3.bucket_arn
    gateway_url           = module.gateway.gateway_url
    gateway_arn           = module.gateway.gateway_arn
    cognito_discovery_url = module.gateway.cognito_discovery_url
    cognito_client_id     = module.gateway.cognito_client_id
    memory_id             = module.memory.memory_id
    bedrock_model_id      = var.bedrock_model_id
}

module "observability" {
    source      = "../06_observability"
    region      = var.region
    env         = var.env
    gateway_arn = module.gateway.gateway_arn
    memory_arn  = module.memory.memory_arn

    # Passed through rather than left to the child's default, because it is the
    # one setting in this stack that changes the whole account. It should be
    # visible in the tfvars you actually edit.
    enable_transaction_search = var.enable_transaction_search
}

# The supervisor as an HTTP API. Depends on 03 for identity and 05 for the
# runtime it invokes, which is why it comes after both.
module "api" {
    source               = "../07_api"
    region               = var.region
    env                  = var.env
    supervisor_arn       = module.runtimes.supervisor_arn
    cognito_user_pool_id = module.gateway.cognito_user_pool_id
    cognito_client_id    = module.gateway.cognito_client_id
}

# The browser's half. Last, because CloudFront's /api/* origin is the API above —
# and that reference is the only thing that orders the two.
module "ui" {
    source      = "../08_ui"
    region      = var.region
    env         = var.env
    api_domain  = module.api.api_domain
    api_stage   = module.api.api_stage
    price_class = var.price_class
}

# --- Everything you need afterwards, in one `terraform output` --------------

output "bucket" {
    value = module.s3.bucket
}

output "gateway_url" {
    value = module.gateway.gateway_url
}

output "memory_id" {
    value = module.memory.memory_id
}

output "runtime_arns" {
    value = module.runtimes.runtime_arns
}

output "supervisor_arn" {
    value       = module.runtimes.supervisor_arn
    description = "The only runtime reachable from outside."
}

output "bearer_token_command" {
    value       = module.gateway.bearer_token_command
    description = "Mint the token clients/a2a_call.py reads from BEARER_TOKEN."
}

output "invoke_command" {
    value = module.runtimes.invoke_command
}

output "env_file" {
    description = "Paste into agent-core/.env to point local code at the deployed stack."
    value       = <<-EOT
        S3_BUCKET=${module.s3.bucket}
        GATEWAY_URL=${module.gateway.gateway_url}
        MEMORY_ID=${module.memory.memory_id}
        SKILLS_MCP_ARN=${module.runtimes.runtime_arns["hr_skills_mcp"]}
        SCREENING_ARN=${module.runtimes.runtime_arns["talent_screening"]}
        OUTREACH_ARN=${module.runtimes.runtime_arns["recruiting_outreach"]}
        COMPLIANCE_ARN=${module.runtimes.runtime_arns["people_compliance"]}
    EOT
}

output "transaction_search" {
    value = module.observability.transaction_search
}

output "chat_url" {
    value       = module.ui.chat_url
    description = "The chat UI. Sign in with `username` and the password you set."
}

output "api_url" {
    value       = module.api.api_url
    description = "The supervisor as an API, for callers that are not the browser."
}

output "distribution_id" {
    value       = module.ui.distribution_id
    description = "aws cloudfront create-invalidation --distribution-id <this> --paths '/*'"
}
