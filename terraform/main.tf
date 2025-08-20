provider "aws" {
  profile = "default"
  region  = var.aws_region
}

data "aws_caller_identity" "current" {}
