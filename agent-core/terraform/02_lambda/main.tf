# 02 — hr-data-fn, the function the Gateway will publish as MCP tools.
#
# This is the only thing that writes to S3. The hr_skills_mcp runtime reads the
# same bucket read-only, because a scoring engine that can edit the employee
# record is a scoring engine nobody will trust.
#
#   uv run scripts/package.py hr_data_fn      # build dist/hr_data_fn.zip first
#   terraform init && terraform apply

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

variable "package" {
    type    = string
    default = "../../dist/hr_data_fn.zip"
}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # path.module, not a bare relative path: `filebase64sha256("../../dist/…")`
    # resolves against the process working directory, which changes the moment
    # this module is called from 00_all_at_once. path.module is always this
    # directory. It cannot go in the variable default — those must be literals.
    package = "${path.module}/${var.package}"
}

data "aws_iam_policy_document" "assume" {
    statement {
        effect  = "Allow"
        actions = ["sts:AssumeRole"]
        principals {
            type        = "Service"
            identifiers = ["lambda.amazonaws.com"]
        }
    }
}

data "aws_iam_policy_document" "s3" {
    statement {
        effect    = "Allow"
        actions   = ["s3:GetObject"]
        resources = ["${var.bucket_arn}/*"]
    }
    statement {
        # The write half. Scoped to shortlists/ on purpose — this function has no
        # business rewriting the employee directory either.
        effect    = "Allow"
        actions   = ["s3:PutObject"]
        resources = ["${var.bucket_arn}/shortlists/*"]
    }
}

resource "aws_iam_role" "fn" {
    name               = "hr-data-fn-role"
    assume_role_policy = data.aws_iam_policy_document.assume.json
    tags               = local.common_tags
}

resource "aws_iam_role_policy" "s3" {
    role   = aws_iam_role.fn.id
    policy = data.aws_iam_policy_document.s3.json
}

resource "aws_iam_role_policy_attachment" "logs" {
    role       = aws_iam_role.fn.name
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "hr_data" {
    function_name    = "hr-data-fn"
    role             = aws_iam_role.fn.arn
    filename         = local.package
    source_code_hash = filebase64sha256(local.package)

    handler = "main.lambda_handler" # main.py at the zip root, as package.py builds it
    runtime = "python3.13"

    # AgentCore Runtime is arm64 and package.py builds aarch64 wheels. Leaving
    # this at the x86_64 default imports those wheels into the wrong CPU.
    architectures = ["arm64"]

    # A model waiting on a tool call is a session ticking along. Fail fast.
    timeout     = 30
    memory_size = 512

    environment {
        variables = {
            DATA_SOURCE = "s3"
            S3_BUCKET   = var.bucket
            # No AWS_REGION here: it is reserved, the Lambda runtime sets it, and
            # Settings.aws_region picks it up from the environment either way.
        }
    }

    tags = local.common_tags
}

resource "aws_lambda_permission" "gateway" {
    statement_id  = "AllowAgentCoreGateway"
    action        = "lambda:InvokeFunction"
    function_name = aws_lambda_function.hr_data.function_name
    principal     = "bedrock-agentcore.amazonaws.com"
}

output "lambda_arn" {
    value       = aws_lambda_function.hr_data.arn
    description = "Pass to 03_gateway as -var lambda_arn=..."
}

output "lambda_name" {
    value = aws_lambda_function.hr_data.function_name
}
