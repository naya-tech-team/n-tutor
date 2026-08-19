# 06_observability — vended log groups, Transaction Search, and the traces delivery.

# --- from 03_gateway ---------------------------------------------------------
#   terraform output -raw gateway_arn

gateway_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/hr-gateway-abc123"

# --- from 04_memory ----------------------------------------------------------
#   terraform output -raw memory_arn

memory_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:memory/hr_hiring_desk-AbCdEf1234"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# retention_days = 30     # default: 30

# Throws the X-Ray -> CloudWatch Logs switch for the whole ACCOUNT and region,
# not just this stack. Required for the traces delivery; set false in an account
# you do not own and you get logs without traces instead of a failed apply.
# `destroy` turns it back off account-wide.
# enable_transaction_search = true    # default: true

# Wait between writing the CloudWatch Logs resource policy and asking X-Ray to
# use it. Raise it if you still see "XRay does not have permission to call
# PutLogEvents on the aws/spans Log Group".
# iam_propagation_delay = "30s"       # default: 30s
