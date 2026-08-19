# 05_runtimes — the five runtimes, in three dependency tiers.

# --- from 01_s3_data ---------------------------------------------------------

bucket     = "hr-skills-123456789012-us-west-2"
bucket_arn = "arn:aws:s3:::hr-skills-123456789012-us-west-2"

# --- from 03_gateway ---------------------------------------------------------

gateway_url           = "https://hr-gateway-abc123.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"
cognito_discovery_url = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEf/.well-known/openid-configuration"
cognito_client_id     = "1a2b3c4d5e6f7g8h9i0j"

# --- from 04_memory ----------------------------------------------------------

memory_id = "hr_hiring_desk-AbCdEf1234"

# --- you must set this -------------------------------------------------------

# No safe default — model ids are account- and region-specific.
#
# Nova, not Claude: Anthropic models on Bedrock need a use-case form approved per
# account, and until then every call is a ResourceNotFoundException. Amazon's own
# models have no such gate. `us.amazon.nova-pro-v1:0` if Lite starts wandering —
# this pipeline is tool-call heavy across five agents.
bedrock_model_id = "us.amazon.nova-lite-v1:0"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# Where `uv run scripts/package.py` writes the six zips, relative to this module.
# dist_dir = "../../dist"


# Scopes the screener's InvokeGateway permission. From 03_gateway.
gateway_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/hr-gateway-abc123"
