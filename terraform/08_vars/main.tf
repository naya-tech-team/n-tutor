provider "aws" {
    region = "us-west-2"
}

# Read-only lookup of the current AWS account (a data source).
# Who am I? Returns the account ID of whatever credentials are in use — the
# reliable way to build globally-unique names without hardcoding an account.
data "aws_caller_identity" "current" {}

# Where am I? Note: in AWS provider 6.x the attribute is `.region`.
# The older `.name` still resolves but is deprecated.
data "aws_region" "current" {}

# Which availability zones can I actually use *in this account*? This differs
# per account, which is why hardcoding "us-west-2a" eventually breaks.
data "aws_availability_zones" "available" {
  state = "available"
}

# The current Amazon Linux 2023 AMI, straight from the parameter AWS maintains.
# This is the fix for the hardcoded AMI in the basic scenarios: that ID is
# region-locked and goes stale, this one never does.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  common_tags = { Project = "ai-agent-platform", Env = var.env }
  suffix      = "${var.env}-${data.aws_caller_identity.current.account_id}"
  name = "acme-${var.env}"          # computed once, reused
}

resource "aws_s3_bucket" "team" {
  for_each = toset(var.teams)

  bucket   = "acme-${each.key}-docs-${local.suffix}"
  depends_on = [aws_vpc.myvpc]   # <-- explicit dependency

  tags     = merge(local.common_tags, { Team = each.key })
}

resource "aws_vpc" "myvpc" {
  cidr_block = "10.0.0.0/16"

  tags = {
    Name = var.inputname
  }
}

output "bucket_names" {
  value = [for b in aws_s3_bucket.team : b.bucket]
}

output "bucket_arns" {
  value = { for team, b in aws_s3_bucket.team : team => b.arn }
}

output "vpcid" {
  value = aws_vpc.myvpc.id
}

# output "data_output" {
#   value = [
#     data.aws_caller_identity.current.account_id, 
#   data.aws_region.current.name, 
#   data.aws_availability_zones.available.names,
#   data.aws_ssm_parameter.al2023.value, 
#   data.aws_ssm_parameter.al2023.type]
# }

