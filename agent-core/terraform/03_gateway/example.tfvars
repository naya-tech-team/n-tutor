# 03_gateway — Cognito, and hr-gateway with its one Lambda target.

# --- from 02_lambda ----------------------------------------------------------
#   terraform output -raw lambda_arn

lambda_arn = "arn:aws:lambda:us-west-2:123456789012:function:hr-data-fn"

# --- you must set this -------------------------------------------------------

# Without a user there is no way to mint a bearer token, and everything behind
# CUSTOM_JWT — the gateway and all five runtimes — is uncallable. Min 8 chars.
# Copy this file to a name that is NOT example.tfvars before filling it in.
password = "change-me-min-8-chars"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev — also the Cognito pool name suffix

# username = "hr-agent"   # default: hr-agent
# gateway_name = "hr-gateway"        # default: hr-gateway

# IAM is eventually consistent. CreateGatewayTarget assumes the role moments
# after terraform creates it; too short a wait gives you
# "Gateway service is not authorized to perform AssumeRole on Gateway role".
# iam_propagation_delay = "30s"      # default: 30s

# Add aws:SourceAccount / aws:SourceArn to the role trust policy. Leave false
# for the first apply — the gateway ARN does not exist yet — then flip to true
# and re-apply to harden it.
# restrict_trust_to_gateway = false  # default: false

# How callers prove who they are to the gateway: AWS_IAM (SigV4) or CUSTOM_JWT.
# IMMUTABLE — changing it replaces the gateway and every target under it.
# gateway_authorizer_type = "AWS_IAM"   # default: AWS_IAM
