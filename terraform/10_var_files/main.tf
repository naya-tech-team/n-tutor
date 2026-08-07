provider "aws" {
    region = "us-west-2"
}

variable "number_of_servers" {
    type = number
}

variable "env" {
    type = string
}

resource "aws_instance" "ec2" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"
    count = var.env == "prod" ? var.number_of_servers : 0
}
