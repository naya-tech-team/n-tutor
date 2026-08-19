# 07_api — the supervisor behind a streaming API Gateway.
#
# Build the proxy zip BEFORE terraform: filebase64sha256() is evaluated at plan
# time, so a missing zip fails the plan rather than the apply.
#
#   uv run scripts/package.py chat_proxy       -> dist/chat_proxy.zip

# --- from 05_runtimes --------------------------------------------------------
#   terraform output -raw supervisor_arn

supervisor_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/hiring_supervisor-AbC123"

# --- from 03_gateway ---------------------------------------------------------
#   terraform output -raw cognito_user_pool_id
#   terraform output -raw cognito_client_id

cognito_user_pool_id = "us-west-2_AbCdEfGhI"
cognito_client_id    = "1a2b3c4d5e6f7g8h9i0j1k2l3m"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# The stage is a path segment on the execute-api URL. 08_ui sets CloudFront's
# origin_path to it, so the browser never sees it.
# stage_name = "v1"                   # default: v1

# Ceiling on one answer, applied to the Lambda and the integration together.
# Max 900 with streaming, 300 buffered.
# proxy_timeout_seconds = 300         # default: 300

# Cost control rather than capacity: every chat request runs an agent for a
# minute or two and spends Bedrock tokens.
# throttle_rate  = 5                  # default: 5   requests/second
# throttle_burst = 10                 # default: 10

# retention_days = 30                 # default: 30 — both log groups

# Sets the API Gateway CloudWatch Logs role for the whole ACCOUNT and region, not
# just this API. Without it, creating a stage that logs fails with "CloudWatch
# Logs role ARN must be set in account settings to enable logging".
#
# Set false in an account you do not own: you get the API with no access logs and
# no execution logs, instead of a failed apply.
# manage_apigw_account_logging = true   # default: true

# Wait after creating that role before API Gateway is asked to use it. Raise it
# if the apply still complains about the role.
# iam_propagation_delay = "30s"       # default: 30s

# Where the proxy zip is, relative to this module.
# proxy_zip = "../../dist/chat_proxy.zip"   # default: ../../dist/chat_proxy.zip
