# 04_memory — hr_hiring_desk and its three strategies.
#
# No required variables. It depends on nothing but its own IAM role, which is
# why it applies BEFORE 05_runtimes: the supervisor takes MEMORY_ID as an
# environment variable.

# --- optional ----------------------------------------------------------------

region = "us-west-2"    # default: us-west-2
env    = "dev"          # default: dev
