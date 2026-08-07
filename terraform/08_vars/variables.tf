variable "vpcname" {          # string
  type    = string
  default = "myvpc"
}

variable "sshport" {          # number
  type    = number
  default = 22
}

variable "enabled" {          # bool — type inferred from the default
  default = true
}

variable "mylist" {           # list — ordered, same type throughout
  type    = list(string)
  default = ["Value1", "Value2"]
}

variable "mymap" {            # map — key/value lookup
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
    name = "terraform-user"
    port = [22, 25, 80]
  }
}

variable "inputname" {        # no default -> Terraform prompts at apply time
  type        = string
  description = "Set the name of the VPC"
}

variable "env" { 
  type = string
  default = "dev" 
}

variable "teams"  { 
  type = list(string)
 default = ["orders", "billing", "shipping"] 
 }