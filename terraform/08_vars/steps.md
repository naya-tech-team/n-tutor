
```bash

terraform fmt         # fix indentation/spacing
terraform validate    # check syntax & types (no cloud calls)
terraform console      # REPL to test expressions
terraform refresh      # re-sync state with real infra (or: plan -refresh-only)

# verbose logs when something is really stuck:
export TF_LOG=DEBUG          # TRACE, DEBUG, INFO, WARN, ERROR
export TF_LOG_PATH=tf.log    # send logs to a file

terraform apply -var="inputname=MyVPC"

# Or

export TF_VAR_inputname=MyVPC     # any TF_VAR_* env var becomes a variable
terraform apply
```