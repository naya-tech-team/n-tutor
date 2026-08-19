# 02_lambda — hr-data-fn, the function the Gateway publishes as MCP tools.

# --- from 01_s3_data ---------------------------------------------------------
#   terraform output -raw bucket   /   -raw bucket_arn

bucket     = "hr-skills-123456789012-us-west-2"
bucket_arn = "arn:aws:s3:::hr-skills-123456789012-us-west-2"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# The zip built by `uv run scripts/package.py hr_data_fn`, relative to this module.
# package = "../../dist/hr_data_fn.zip"
