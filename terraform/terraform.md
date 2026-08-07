# Terraform Use Cases

A hands-on catalogue of the Terraform examples in this folder. Each directory is a **self-contained root module** demonstrating one concept — run `terraform init` inside the directory you want to try, never at the top level.

All examples target AWS `eu-west-2` (London) and use AMI `ami-032598fcc7e9d1c7a` (Amazon Linux 2) on `t2.micro`.

---

## Table of Contents

| # | Folder | Concept |
|---|--------|---------|
| 1 | [first-resource/](first-resource/) | Your first resource — a VPC |
| 2 | [ec2/](ec2/) | Launching an EC2 instance |
| 3 | [sg/](sg/) | Security groups + implicit dependencies |
| 4 | [eip/](eip/) | Elastic IP + `output` blocks |
| 5 | [iam/](iam/) | IAM user, custom policy, policy attachment |
| 6 | [rds/](rds/) | Managed database instance |
| 7 | [dep/](dep/) | Explicit dependencies with `depends_on` |
| 8 | [variables/](variables/) | Every Terraform variable type |
| 9 | [count-demo/](count-demo/) | Static resource replication with `count` |
| 10 | [vars/](vars/) | `.tfvars` files driving `count` |
| 11 | [feature_switch/](feature_switch/) | Conditional resources (feature toggles) |
| 12 | [count-advc/](count-advc/) | `count.index` + splat expressions in a module |
| 13 | [dynamic/](dynamic/) | `dynamic` blocks to generate nested config |
| 14 | [modules/](modules/) | Local modules — inputs and outputs |
| 15 | [changes/module/](changes/module/) | `for_each` on a module (Terraform 0.13+) |
| 16 | [changes/errors/](changes/errors/) | Deliberately broken config for error handling |
| 17 | [import/](import/) | Importing pre-existing infrastructure into state |
| 18 | [backend/](backend/) | Remote state on S3 |
| 19 | [challenge1/](challenge1/) | Challenge — tagged VPC |
| 20 | [challenge2/](challenge2/) | Challenge — full web + db stack in one file |
| 21 | [challenge3/](challenge3/) | Challenge — same stack, refactored into modules |

---

## 1. `first-resource/` — Your First Resource

The minimum viable Terraform config: a provider block and one resource.

```hcl
provider "aws" {
    region = "eu-west-2"
}

resource "aws_vpc" "myvpc" {
    cidr_block = "10.0.0.0/16"
}
```

**Anatomy:** `resource "<TYPE>" "<LOCAL_NAME>" { ... }`. The type (`aws_vpc`) is defined by the provider; the local name (`myvpc`) is how you reference it elsewhere in Terraform — it is not the name in AWS.

```bash
terraform init      # download the AWS provider
terraform plan      # preview
terraform apply     # create
terraform destroy   # tear down
```

**Files:** [main.tf](first-resource/main.tf)

---

## 2. `ec2/` — EC2 Instance

Same shape, different resource type. Note there is no security group or key pair — this is the bare minimum to get an instance running.

```hcl
resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
}
```

**Files:** [main.tf](ec2/main.tf)

---

## 3. `sg/` — Security Groups & Implicit Dependencies

Attaches a security group to an instance. The key learning is the **implicit dependency**: because `aws_instance.ec2` references `aws_security_group.webtraffic.name`, Terraform builds a dependency graph and creates the security group *first* — automatically.

```hcl
resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    security_groups = [aws_security_group.webtraffic.name]   # <-- implicit dependency
}

resource "aws_security_group" "webtraffic" {
    name = "Allow HTTPS"

    ingress {
        from_port = 443
        to_port = 443
        protocol = "TCP"
        cidr_blocks = ["0.0.0.0/0"]
    }

    egress {
        from_port = 443
        to_port = 443
        protocol = "TCP"
        cidr_blocks = ["0.0.0.0/0"]
    }
}
```

Order in the file is irrelevant — Terraform resolves ordering from references, not from position.

**Files:** [main.tf](sg/main.tf)

---

## 4. `eip/` — Elastic IP & Outputs

Allocates a static public IP and binds it to the instance, then surfaces the result with an `output` block.

```hcl
resource "aws_eip" "elasticeip" {
    instance = aws_instance.ec2.id
}

output "EIP" {
    value = aws_eip.elasticeip.public_ip
}
```

Outputs are printed after `terraform apply` and are how a child module returns values to its caller (see [modules/](modules/)).

```bash
terraform apply
terraform output EIP     # re-read a value from state at any time
```

**Files:** [main.tf](eip/main.tf)

---

## 5. `iam/` — IAM User, Policy & Attachment

Three resources wired together: a user, a custom JSON policy, and the binding between them.

```hcl
resource "aws_iam_user" "myUser" {
    name = "TJ"
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
                "glacier:ListVaults",
                "elasticfilesystem:ClientMount"
                // ... ~70 actions in total
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
```

**Heredoc syntax** (`<<EOF ... EOF`) embeds a multi-line string — ideal for raw JSON policy documents. The closing `EOF` must sit at the start of its own line.

> Note: `aws_iam_policy_attachment` is *exclusive* — it detaches the policy from any identity not listed. For production, prefer `aws_iam_user_policy_attachment`.

**Files:** [main.tf](iam/main.tf)

---

## 6. `rds/` — Managed Database

A single-resource RDS MariaDB instance.

```hcl
resource "aws_db_instance" "myRDS" {
    db_name = "myDB"
    identifier = "my-first-rds"
    instance_class = "db.t2.micro"
    engine = "mariadb"
    engine_version = "10.2.21"
    username = "bob"
    password = "password123"
    port = 3306
    allocated_storage = 20
    skip_final_snapshot = true
}
```

- `skip_final_snapshot = true` lets `terraform destroy` complete without prompting for a snapshot name — fine for a lab, dangerous in production.
- `identifier` is the AWS-side name; `db_name` is the database created inside the engine.

> The hardcoded password is for demo purposes only. In real use, pass it via a sensitive variable, AWS Secrets Manager, or `manage_master_user_password`.

**Files:** [main.tf](rds/main.tf)

---

## 7. `dep/` — Explicit Dependencies

When two resources have no attribute reference between them but ordering still matters, force it with `depends_on`.

```hcl
resource "aws_instance" "db" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
}

resource "aws_instance" "web" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"

    depends_on = [aws_instance.db]   # <-- explicit: db must exist first
}
```

Contrast with [sg/](sg/): use `depends_on` only when an implicit reference isn't available, since it's a blunter instrument.

**Files:** [main.tf](dep/main.tf)

---

## 8. `variables/` — Variable Types

A reference sheet of every variable type Terraform supports.

```hcl
variable "vpcname" {          # string
  type    = string
  default = "myvpc"
}

variable "sshport" {          # number
  type    = number
  default = 22
}

variable "enabled" {          # bool (type inferred)
  default = true
}

variable "mylist" {           # list
  type    = list(string)
  default = ["Value1", "Value2"]
}

variable "mymap" {            # map
  type = map
  default = {
    Key1 = "Value1"
    Key2 = "Value2"
  }
}

variable "mytuple" {          # tuple — fixed length, mixed types
  type    = tuple([string, number, string])
  default = ["cat", 1, "dog"]
}

variable "myobject" {         # object — named attributes, mixed types
  type = object({ name = string, port = list(number) })
  default = {
    name = "TJ"
    port = [22, 25, 80]
  }
}

variable "inputname" {        # no default -> Terraform prompts at apply time
  type        = string
  description = "Set the name of the VPC"
}
```

Consumed with `var.<name>`:

```hcl
resource "aws_vpc" "myvpc" {
  cidr_block = "10.0.0.0/16"

  tags = {
    Name = var.inputname
  }
}

output "vpcid" {
  value = aws_vpc.myvpc.id
}
```

A variable with **no `default`** and no supplied value makes Terraform prompt interactively. Supply it non-interactively instead:

```bash
terraform apply -var="inputname=MyVPC"
export TF_VAR_inputname=MyVPC && terraform apply
```

**Files:** [main.tf](variables/main.tf)

---

## 9. `count-demo/` — Replicating Resources

`count` turns one resource block into N instances.

```hcl
resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    count = 3
}
```

The resource is now addressed as a list: `aws_instance.ec2[0]`, `[1]`, `[2]`.

**Files:** [main.tf](count-demo/main.tf)

---

## 10. `vars/` — Variable Files per Environment

Hardcoding `count = 3` doesn't scale across environments. Drive it from a variable and swap the values file.

```hcl
variable "number_of_servers" {
    type = number
}

resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    count = var.number_of_servers
}
```

[test.tfvars](vars/test.tfvars):
```hcl
number_of_servers = 2
```

[prod.tfvars](vars/prod.tfvars):
```hcl
number_of_servers = 50
```

```bash
terraform apply -var-file="test.tfvars"    # 2 instances
terraform apply -var-file="prod.tfvars"    # 50 instances
```

> A file named `terraform.tfvars` (or `*.auto.tfvars`) is loaded automatically; anything else needs `-var-file`.

**Files:** [main.tf](vars/main.tf) · [test.tfvars](vars/test.tfvars) · [prod.tfvars](vars/prod.tfvars)

---

## 11. `feature_switch/` — Conditional Resources

The classic Terraform feature toggle: a ternary on `count` where `0` means "don't create this at all".

```hcl
variable "environment" {
    type = string
}

resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    count = var.environment == "prod" ? 1 : 0
}
```

[prod.tfvars](feature_switch/prod.tfvars):
```hcl
number_of_servers = 50
environment="prod"
```

[test.tfvars](feature_switch/test.tfvars):
```hcl
number_of_servers = 2
environment="test"
```

```bash
terraform apply -var-file="prod.tfvars"    # 1 instance created
terraform apply -var-file="test.tfvars"    # 0 instances — resource skipped entirely
```

**Files:** [main.tf](feature_switch/main.tf) · [prod.tfvars](feature_switch/prod.tfvars) · [test.tfvars](feature_switch/test.tfvars)

---

## 12. `count-advc/` — `count.index` and Splat Expressions

Combines `count`, `length()`, `count.index`, and the splat operator inside a module. Three servers are created, each tagged with a different name from a list.

Root — [main.tf](count-advc/main.tf):
```hcl
module "db" {
    source = "./db"
    server_names = ["mariadb","mysql","mssql"]
}

output "private_ips" {
    value = module.db.PrivateIP
}
```

Child — [db/db.tf](count-advc/db/db.tf):
```hcl
variable "server_names" {
    type = list(string)
}

resource "aws_instance" "db" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    count = length(var.server_names)          # 3 instances

    tags = {
        Name = var.server_names[count.index]  # "mariadb", "mysql", "mssql"
    }
}

output "PrivateIP" {
    value = [aws_instance.db.*.private_ip]    # splat: collect the attribute from every instance
}
```

**Three techniques at once:**
- `length(var.server_names)` — derive the instance count from the list, so adding a name adds a server.
- `count.index` — the 0-based iteration number, used to pick the matching name.
- `aws_instance.db.*.private_ip` — the **splat expression**, returning a list of the private IP of every instance.

**Files:** [main.tf](count-advc/main.tf) · [db/db.tf](count-advc/db/db.tf)

---

## 13. `dynamic/` — Dynamic Blocks

Writing one `ingress` block per port doesn't scale. `dynamic` generates repeated **nested blocks** from a collection.

```hcl
variable "ingressrules" {
    type = list(number)
    default = [80,443]
}

variable "egressrules" {
    type = list(number)
    default = [80,443,25,3306,53,8080]
}

resource "aws_security_group" "webtraffic" {
    name = "Allow HTTPS"

    dynamic "ingress" {
        iterator = port
        for_each = var.ingressrules
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }

    dynamic "egress" {
        iterator = port
        for_each = var.egressrules
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }
}
```

- `dynamic "ingress"` — the name of the nested block being generated.
- `for_each` — the collection to iterate.
- `iterator = port` — renames the iteration variable (defaults to the block name); access the current element with `port.value`.
- `content { }` — the body produced on each iteration.

This produces 2 ingress rules and 6 egress rules from 12 lines. Compare against the hand-written version in [sg/main.tf](sg/main.tf).

**Files:** [main.tf](dynamic/main.tf)

---

## 14. `modules/` — Local Modules

Packaging a resource into a reusable child module with an input variable and an output.

Root — [main.tf](modules/main.tf):
```hcl
provider "aws" {
    region = "eu-west-2"
}

module "ec2module" {
    source  = "./ec2"          # local path; can also be a registry or git URL
    ec2name = "Name From Module"
}

output "module_output" {
    value = module.ec2module.instance_id
}
```

Child — [ec2/ec2.tf](modules/ec2/ec2.tf):
```hcl
variable "ec2name" {
    type = string
}

resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    tags = {
        Name = var.ec2name
    }
}

output "instance_id" {
    value = aws_instance.ec2.id
}
```

**The module contract:**
- **Input** — the child declares `variable "ec2name"`; the caller sets it as an argument in the `module` block.
- **Output** — the child declares `output "instance_id"`; the caller reads it as `module.ec2module.instance_id`.
- The child has **no `provider` block** — it inherits the provider from the root module.

> Run `terraform init` after adding or changing a `module` block — Terraform must fetch/link the source.

**Files:** [main.tf](modules/main.tf) · [ec2/ec2.tf](modules/ec2/ec2.tf)

---

## 15. `changes/module/` — `for_each` on a Module

Terraform 0.13 added `count` and `for_each` support to `module` blocks, letting you stamp out the same module per environment.

Root — [main.tf](changes/module/main.tf):
```hcl
module "ec2" {
    source   = "./ec2"
    for_each = toset(["dev", "test", "prod"])
}
```

Child — [ec2/ec2.tf](changes/module/ec2/ec2.tf):
```hcl
resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
}
```

`toset()` converts the list to a set so each element becomes a **stable string key** rather than a positional index. The instances are addressed as `module.ec2["dev"]`, `module.ec2["test"]`, `module.ec2["prod"]`.

**Why `for_each` over `count` here:** removing `"test"` from a `count`-based list would shift indices and cause Terraform to destroy and recreate unrelated resources. With `for_each`, only the `"test"` module is destroyed — the others are untouched.

Inside the child, reference the current key with `each.key` (e.g. to tag the instance).

**Files:** [main.tf](changes/module/main.tf) · [ec2/ec2.tf](changes/module/ec2/ec2.tf)

---

## 16. `changes/errors/` — Error Handling

This config is **intentionally broken** — it references `var.type`, which is never declared.

```hcl
resource "aws_instance" "ec2" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = var.type     # <-- undeclared variable
}
```

Run it to see how Terraform reports the problem:

```bash
terraform validate    # catches it without touching AWS
terraform plan
```

Expected output:
```
Error: Reference to undeclared input variable
  on main.tf line 7, in resource "aws_instance" "ec2":
   7:     instance_type = var.type
An input variable with the name "type" has not been declared.
```

**Fix it** by adding the missing declaration:
```hcl
variable "type" {
    type    = string
    default = "t2.micro"
}
```

Terraform's error messages improved substantially in 0.12+ — they name the file, line, and the specific fix. `terraform validate` is the fastest feedback loop since it needs no credentials or network access.

**Files:** [main.tf](changes/errors/main.tf)

---

## 17. `import/` — Importing Existing Infrastructure

Bringing resources that already exist in AWS (created manually or by another tool) under Terraform management.

```hcl
resource "aws_vpc" "myvpc" {
    cidr_block = "10.0.0.0/16"
}

resource "aws_vpc" "myvpc2" {
    cidr_block = "192.168.0.0/24"
}
```

The workflow:

```bash
# 1. Write the resource block first (Terraform will not generate it for you)
# 2. Map the real AWS resource ID onto the address in your config
terraform import aws_vpc.myvpc2 vpc-0a1b2c3d4e5f67890

# 3. Confirm the config matches reality — the goal is "No changes"
terraform plan
```

**Key points:**
- `terraform import` only writes to **state**. It does not write HCL — you author the resource block yourself.
- If `plan` shows changes after importing, your HCL doesn't match the real resource. Adjust the HCL until the plan is clean, or you'll modify live infrastructure on the next apply.
- Terraform 1.5+ also offers a declarative `import { }` block plus `-generate-config-out` to scaffold the HCL for you.

**Files:** [main.tf](import/main.tf)

---

## 18. `backend/` — Remote State on S3

By default `terraform.tfstate` sits on your local disk — unusable for a team. A backend moves it to shared storage.

[backend.tf](backend/backend.tf):
```hcl
terraform {
    backend "s3" {
        key        = "terraform/tfstate.tfstate"
        bucket     = "tj-remote-backend-2020"
        region     = "eu-west-2"
        access_key = "your_access_key"
        secret_key = "your_secret_key"
    }
}
```

[main.tf](backend/main.tf):
```hcl
provider "aws" {
    region = "eu-west-2"
}

resource "aws_vpc" "test" {
    cidr_block = "10.0.0.0/16"
}
```

```bash
terraform init      # prompts to copy existing local state to the backend
```

**Notes:**
- The S3 bucket must already exist — the backend cannot create it.
- The backend block cannot use variables or interpolation; values must be literals or passed via `terraform init -backend-config=...`.
- **Do not commit credentials.** Omit `access_key`/`secret_key` and let the provider chain resolve them (env vars, `~/.aws/credentials`, or an instance role). The [.gitignore](.gitignore) here already excludes `terraform.tfstate*` for the same reason — state files contain secrets in plain text.
- Add state locking with `use_lockfile = true` (or a DynamoDB table on older versions) to stop two people applying at once.

**Files:** [backend.tf](backend/backend.tf) · [main.tf](backend/main.tf)

---

## 19. `challenge1/` — Tagged VPC

> **Challenge:** create a VPC with CIDR `192.168.0.0/24`, tagged `Name = TerraformVPC`.

```hcl
provider "aws" {
    region = "eu-west-2"
}

resource "aws_vpc" "challenge1vpc" {
    cidr_block = "192.168.0.0/24"
    tags = {
        Name = "TerraformVPC"
    }
}
```

**Files:** [main.tf](challenge1/main.tf)

---

## 20. `challenge2/` — Full Stack in a Single File

> **Challenge:** build a two-tier stack — a DB server, a web server running Apache, a security group opening 80/443, an Elastic IP on the web server, and outputs for both IPs.

This brings together security groups, `user_data`, dynamic blocks, Elastic IPs, and outputs.

```hcl
resource "aws_instance" "db" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"

    tags = {
        Name = "DB Server"
    }
}

resource "aws_instance" "web" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    security_groups = [aws_security_group.web_traffic.name]
    user_data = file("server-script.sh")     # bootstrap script runs at first boot
    tags = {
        Name = "Web Server"
    }
}

resource "aws_eip" "web_ip" {
    instance = aws_instance.web.id
}

variable "ingress" {
    type = list(number)
    default = [80,443]
}

variable "egress" {
    type = list(number)
    default = [80,443]
}

resource "aws_security_group" "web_traffic" {
    name = "Allow Web Traffic"

    dynamic "ingress" {
        iterator = port
        for_each = var.ingress
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }

    dynamic "egress" {
        iterator = port
        for_each = var.egress
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }
}

output "PrivateIP" {
    value = aws_instance.db.private_ip
}

output "PublicIP" {
    value = aws_eip.web_ip.public_ip
}
```

[server-script.sh](challenge2/server-script.sh) — installs and starts Apache on first boot:
```bash
#!/bin/bash
sudo yum update
sudo yum install -y httpd
sudo systemctl start httpd
sudo systemctl enable httpd
echo "<h1>Hello from Terraform</h1>" | sudo tee /var/www/html/index.html
```

`file("server-script.sh")` reads the script from disk and passes it to `user_data`, where cloud-init executes it on first boot. Browse to the `PublicIP` output to see the page.

> The path is relative to the working directory, which is why [challenge3](challenge3/) has to use `./web/server-script.sh` — see below.

**Files:** [main.tf](challenge2/main.tf) · [server-script.sh](challenge2/server-script.sh)

---

## 21. `challenge3/` — The Same Stack, Modularised

> **Challenge:** take challenge 2 and refactor it into modules.

This is the capstone: the identical infrastructure split into four modules, demonstrating **module composition** — modules calling other modules and passing values between them.

```
challenge3/
├── main.tf          root — calls db + web, re-exports their outputs
├── db/db.tf         DB instance          -> outputs PrivateIP
├── web/web.tf       Web instance         -> calls eip + sg, outputs pub_ip
├── sg/sg.tf         Security group       -> outputs sg_name
└── eip/eip.tf       Elastic IP           -> takes instance_id, outputs PublicIP
```

**Root** — [main.tf](challenge3/main.tf):
```hcl
provider "aws" {
    region = "eu-west-2"
}

module "db" {
    source = "./db"
}

module "web" {
    source = "./web"
}

output "PrivateIP" {
    value = module.db.PrivateIP
}

output "PublicIP" {
    value = module.web.pub_ip
}
```

**DB module** — [db/db.tf](challenge3/db/db.tf):
```hcl
resource "aws_instance" "db" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"

    tags = {
        Name = "DB Server"
    }
}

output "PrivateIP" {
    value = aws_instance.db.private_ip
}
```

**Web module** — [web/web.tf](challenge3/web/web.tf). Note it calls two further modules and feeds its own instance ID into the EIP module:
```hcl
resource "aws_instance" "web" {
    ami = "ami-032598fcc7e9d1c7a"
    instance_type = "t2.micro"
    security_groups = [module.sg.sg_name]              # consumes the sg module's output
    user_data = file("./web/server-script.sh")         # path relative to the ROOT module
    tags = {
        Name = "Web Server"
    }
}

module "eip" {
    source      = "../eip"
    instance_id = aws_instance.web.id                  # passes a value INTO the eip module
}

module "sg" {
    source = "../sg"
}

output "pub_ip" {
    value = module.eip.PublicIP                        # re-exports a nested module's output
}
```

**SG module** — [sg/sg.tf](challenge3/sg/sg.tf), the dynamic-block security group, now exposing its name:
```hcl
variable "ingress" {
    type = list(number)
    default = [80,443]
}

variable "egress" {
    type = list(number)
    default = [80,443]
}

resource "aws_security_group" "web_traffic" {
    name = "Allow Web Traffic"

    dynamic "ingress" {
        iterator = port
        for_each = var.ingress
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }

    dynamic "egress" {
        iterator = port
        for_each = var.egress
        content {
            from_port   = port.value
            to_port     = port.value
            protocol    = "TCP"
            cidr_blocks = ["0.0.0.0/0"]
        }
    }
}

output "sg_name" {
    value = aws_security_group.web_traffic.name
}
```

**EIP module** — [eip/eip.tf](challenge3/eip/eip.tf), a fully generic module: it takes any instance ID and returns the public IP:
```hcl
variable "instance_id" {
    type = string
}

resource "aws_eip" "web_ip" {
    instance = var.instance_id
}

output "PublicIP" {
    value = aws_eip.web_ip.public_ip
}
```

**What this example teaches:**
1. **Output chaining** — `aws_eip.web_ip.public_ip` → `module.eip.PublicIP` → `module.web.pub_ip` → root `PublicIP`. Each layer must explicitly re-export; outputs are not inherited automatically.
2. **Relative sources** — the root uses `./db`, while `web` reaches sideways to siblings with `../eip` and `../sg`.
3. **`file()` paths are relative to the root module**, not to the file containing the call — hence `./web/server-script.sh` inside `web/web.tf`. Using `"${path.module}/server-script.sh"` is the robust alternative, since it resolves relative to the module's own directory.
4. **Reusability** — the `eip` module has no knowledge of web servers; it only needs an `instance_id`, so it can be reused anywhere.

**Files:** [main.tf](challenge3/main.tf) · [db/db.tf](challenge3/db/db.tf) · [web/web.tf](challenge3/web/web.tf) · [sg/sg.tf](challenge3/sg/sg.tf) · [eip/eip.tf](challenge3/eip/eip.tf) · [web/server-script.sh](challenge3/web/server-script.sh)

---

## Suggested Learning Path

**Fundamentals** → `first-resource` → `ec2` → `sg` → `eip`
**More resources** → `iam` → `rds` → `dep`
**Making it dynamic** → `variables` → `count-demo` → `vars` → `feature_switch`
**Advanced expressions** → `count-advc` → `dynamic`
**Structuring code** → `modules` → `changes/module`
**Operations** → `changes/errors` → `import` → `backend`
**Put it together** → `challenge1` → `challenge2` → `challenge3`

---

## Common Commands

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

## Files Ignored by Git

From [.gitignore](.gitignore):
```
.terraform
terraform.tfstate
terraform.tfstate.*
.terraform.lock.hcl
backup
```

State files contain resource attributes **in plain text**, including database passwords — never commit them. (`.terraform.lock.hcl` is ignored here for the labs, but production repos should normally commit it to pin provider versions.)

## Caveats in These Examples

These are teaching examples, deliberately kept minimal. Before reusing any of it:

- **Hardcoded credentials** — the RDS password ([rds/main.tf](rds/main.tf)) and the S3 backend keys ([backend/backend.tf](backend/backend.tf)) are inline. Use variables, Secrets Manager, or the ambient AWS credential chain.
- **Wide-open security groups** — `cidr_blocks = ["0.0.0.0/0"]` is used throughout. Restrict to known ranges in real deployments.
- **Region-locked AMI** — `ami-032598fcc7e9d1c7a` only exists in `eu-west-2`. Use an `aws_ami` data source to look it up dynamically.
- **No provider version pinning** — none of the examples include a `required_providers` block, so `init` pulls the latest provider. Pin versions in real projects.
- **EC2-Classic style `security_groups`** — the examples set `security_groups` with the SG *name*. On modern default-VPC accounts, `vpc_security_group_ids` with the SG *id* is the correct attribute.
