provider "aws" {
    region = "us-west-2"
}

module "ec2module" {
    source  = "./ec2"          # local path; can also be a registry or git URL
    ec2name = "Name From Module"
}

output "module_output" {
    value = module.ec2module.instance_id
}