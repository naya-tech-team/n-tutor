provider "aws" {
    region = "us-west-2"
}

resource "aws_instance" "ec2" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"
    count = 3

    tags = {
        Name = "Terraform-EC2-${count.index + 1}"
    }
}
