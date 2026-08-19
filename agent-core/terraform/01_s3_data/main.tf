# 01 — S3, the system of record.
#
# The bucket holds three things that used to be Python literals, plus the two
# things the deployed system produces: shortlists and the code artifacts.
#
# skills.json is the one that is easy to forget. The alias table is DATA, not
# code — leave it behind and find_by_skill("pyspark") stops finding people whose
# records say "Apache Spark".
#
#   terraform init && terraform apply
#   terraform output -raw bucket   # -> put this in agent-core/.env as S3_BUCKET

terraform {
    # The rest of this repo lets the provider float. These directories pin it,
    # because aws_bedrockagentcore_* resources do not exist before 6.51 and the
    # failure is a confusing "invalid resource type" rather than a version error.
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

variable "seed_dir" {
    type        = string
    description = "Where `uv run scripts/seed_s3.py` wrote the JSON, relative to this module."
    default     = "seed"
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # In provider 6.x the attribute is `.region`. The older `.name` still
    # resolves but is deprecated.
    bucket_name = "hr-skills-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"

    # path.module, not a bare relative path. `filemd5("seed/…")` resolves against
    # the process working directory, so it would break the moment this module is
    # called from 00_all_at_once one level up. path.module is always this
    # directory, in both modes. It cannot go in the variable default — Terraform
    # requires those to be literals — so the join happens here.
    seed_root = "${path.module}/${var.seed_dir}"

    seed_objects = {
        "employees/employees.json"       = "${local.seed_root}/employees/employees.json"
        "requisitions/requisitions.json" = "${local.seed_root}/requisitions/requisitions.json"
        "skills/skills.json"             = "${local.seed_root}/skills/skills.json"
    }
}

resource "aws_s3_bucket" "hr" {
    bucket = local.bucket_name
    tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "hr" {
    bucket                  = aws_s3_bucket.hr.id
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "hr" {
    bucket = aws_s3_bucket.hr.id
    versioning_configuration {
        # An employee record is a thing people argue about. Versioning is what
        # lets you answer "what did it say when we scored her?"
        status = "Enabled"
    }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "hr" {
    bucket = aws_s3_bucket.hr.id
    rule {
        apply_server_side_encryption_by_default {
            sse_algorithm = "AES256"
        }
    }
}

resource "aws_s3_object" "seed" {
    for_each = local.seed_objects

    bucket = aws_s3_bucket.hr.id
    key    = each.key
    source = each.value

    # Without this, re-running seed_s3.py and re-applying is a no-op: terraform
    # compares only the path, which has not changed.
    etag = filemd5(each.value)

    tags = local.common_tags
}

output "bucket" {
    value       = aws_s3_bucket.hr.id
    description = "Set this as S3_BUCKET in agent-core/.env"
}

output "bucket_arn" {
    value = aws_s3_bucket.hr.arn
}
