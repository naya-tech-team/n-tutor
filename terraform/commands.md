
```bash
terraform init                          # initialise providers/modules — run first, and after any module change
terraform validate                      # syntax and reference check, no AWS calls
terraform fmt                           # canonical formatting
terraform plan                          # preview changes
terraform plan -var-file="test.tfvars"  # preview with a specific values file
terraform apply                         # create/update infrastructure
terraform apply -auto-approve           # skip the confirmation prompt
terraform destroy                       # tear everything down
terraform output                        # show all outputs from state
terraform state list                    # list resources tracked in state
terraform import <address> <aws_id>     # adopt an existing resource
```