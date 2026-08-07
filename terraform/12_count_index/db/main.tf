variable "server_names" {
    type = list(string)
}

resource "aws_instance" "db" {
    ami = "ami-0b76d82b547c3c077"
    instance_type = "t3.micro"
    count = length(var.server_names)          # 3 instances

    tags = {
        Name = var.server_names[count.index]  # "mariadb", "mysql", "mssql"
    }
}

output "PrivateIP" {
    value = [aws_instance.db.*.private_ip]    # splat: the attribute from every instance
}