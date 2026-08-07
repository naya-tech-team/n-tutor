provider "aws" {
    region = "us-west-2"
}

resource "aws_s3_bucket" "docs" {
  for_each = toset(["orders", "support", "billing"])
  bucket   = "acme-${each.key}-docs"
}

module "ec2" {
    source   = "./ec2"
    for_each = toset(["dev", "test", "prod"])
}