# 08_ui — the React build on CloudFront, with 07_api as its /api/* origin.
#
# Build the UI BEFORE terraform: fileset() and filemd5() are evaluated at plan
# time, so a missing ui/dist fails with "call to function fileset failed" — which
# does not sound like "you forgot to build the UI".
#
#   make ui-build                              -> ui/dist

# --- from 07_api -------------------------------------------------------------
#   terraform output -raw api_domain
#   terraform output -raw api_stage
#
# api_domain is the bare host: no https://, no trailing slash, no stage.

api_domain = "abcdef1234.execute-api.us-west-2.amazonaws.com"
api_stage  = "v1"

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# PriceClass_100 is North America + Europe, and the cheapest. PriceClass_All adds
# the rest of the world and costs more per GB.
# price_class = "PriceClass_100"      # default: PriceClass_100

# How long CloudFront waits between bytes from the API. The supervisor goes quiet
# for longer than the 30s default while a delegation runs, which cuts the stream
# mid-answer. 60 is the maximum without a quota increase.
# origin_read_timeout = 60            # default: 60

# Where the vite build is, relative to this module.
# ui_dist_dir = "../../ui/dist"       # default: ../../ui/dist
