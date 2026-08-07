provider "aws" {
    region = "us-west-2"
}

variable "db_password" {      # no default -> Terraform prompts at apply time
  type        = string
  sensitive   = true          # keeps it out of CLI output and logs
  description = "Master password for the RDS instance"
}

resource "aws_db_instance" "myRDS" {
    db_name = "myoppsdb"
    identifier = "my-opps-rds"
    instance_class = "db.t3.micro"
    engine = "mariadb"
    engine_version = "11.4"
    username = "bob"
    password = var.db_password
    port = 3306
    allocated_storage = 20
    skip_final_snapshot = true

    lifecycle {
        # make the new one before killing the old (no downtime)
        create_before_destroy = false    
        
        # refuse to delete this (guard a prod database)
        prevent_destroy       = false    
        
        # don't fight edits made outside Terraform
        ignore_changes        = [tags]  
    }
}