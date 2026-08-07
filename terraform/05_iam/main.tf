provider "aws" {
    region = "us-west-2"
}

resource "aws_iam_user" "myUser" {
    name = "terraform-user"
}

resource "aws_iam_policy" "customPolicy" {
    name = "GlacierEFSEC2"

    policy = <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": [
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateVpc",
                "ec2:StopInstances",
                "ec2:CreateVolume",
                "glacier:ListVaults",
                "glacier:CompleteMultipartUpload",
                "elasticfilesystem:ClientMount"
            ],
            "Resource": "*"
        }
    ]
}
EOF
}

resource "aws_iam_policy_attachment" "policyBind" {
    name = "attachment"
    users = [aws_iam_user.myUser.name]
    policy_arn = aws_iam_policy.customPolicy.arn
}