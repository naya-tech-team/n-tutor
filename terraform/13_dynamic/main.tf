provider "aws" {
    region = "us-west-2"
}

variable "ingressrules" {
    type = list(number)
    default = [80,443]
}

variable "egressrules" {
    type = list(number)
    default = [80,443,25,3306,53,8080]
}

resource "aws_instance" "ec2" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"
    security_groups = [aws_security_group.webtraffic.name]
}

resource "aws_security_group" "webtraffic" {
    name = "Allow HTTPS"

    dynamic "ingress" {
        for_each = var.ingressrules
        content {
            from_port   = ingress.value
            to_port     = ingress.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }

    dynamic "egress" {
        for_each = var.egressrules
        content {
            from_port   = egress.value
            to_port     = egress.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }
}