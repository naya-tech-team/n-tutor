
provider "aws" {
    region = "us-west-2"
}

variable "type" {
    type = string
}

resource "aws_instance" "ec2" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = var.type     # <-- undeclared variable
}