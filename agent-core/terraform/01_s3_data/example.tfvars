# 01_s3_data — the bucket and the three seed objects.
#
# No required variables. `terraform apply` works as-is once
# `uv run scripts/seed_s3.py` has written the JSON.

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev

# Where seed_s3.py writes, relative to THIS module. Both sides have to agree:
# app/_shared/config.py:seed_dir points here too.
# seed_dir = "seed"
