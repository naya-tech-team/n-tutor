provider "aws" {
    region = "us-west-2"
}

resource "aws_instance" "db" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"
}

resource "aws_instance" "web" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"

    depends_on = [aws_instance.db]   # <-- explicit: db must exist first
}