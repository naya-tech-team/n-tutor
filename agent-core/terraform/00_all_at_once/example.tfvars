# 00_all_at_once — the whole stack in one apply.
#
# Every variable this module declares is listed. Commented lines show the
# default; uncomment to change it.
#
# Copy to a name that is NOT example.tfvars before filling in the password —
# the root .gitignore ignores *.tfvars but explicitly un-ignores this one.

# --- you must set these ------------------------------------------------------

# Cognito password for the machine user that mints bearer tokens. Min 8 chars.
password = "change-me-min-8-chars"

# No safe default — model ids are account- and region-specific.
#
# Nova rather than Claude because Anthropic models on Bedrock need a one-off
# use-case form per account, and until it is approved every call fails with:
#
#   ResourceNotFoundException: Model use case details have not been submitted
#   for this account.
#
# Amazon's own models have no such gate. Nova Lite is the small one; this
# pipeline is tool-call heavy across five agents, so if delegations start
# wandering, `us.amazon.nova-pro-v1:0` is a one-line change.
#
# To list what YOUR account actually has, without the CLI (which fails behind a
# TLS-inspecting proxy), use the provider — `terraform console`:
#
#   data.aws_bedrock_inference_profiles.all.inference_profile_summaries[*].inference_profile_id
bedrock_model_id = "us.amazon.nova-lite-v1:0"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev — tags, and the Cognito pool name

# The one setting here that changes the ACCOUNT rather than this stack: it points
# X-Ray trace segments at CloudWatch Logs for the whole account and region.
# Required for the traces delivery in 06_observability. Set false in a shared
# account and you get logs without traces, instead of a failed apply.
# enable_transaction_search = true    # default: true

# CloudFront edge coverage for the chat UI. PriceClass_100 is North America +
# Europe and the cheapest; PriceClass_All adds the rest of the world.
# price_class = "PriceClass_100"      # default: PriceClass_100
