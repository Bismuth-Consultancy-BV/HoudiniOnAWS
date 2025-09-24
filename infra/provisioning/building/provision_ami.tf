# This Terraform configuration sets up the necessary AWS infrastructure for building an AMI using Packer.
# Requires AWS Secrets Manager with credentials for GitHub and SideFX OAuth. See constants.py


variable "aws_region" {
  description = "The AWS region to deploy on"
  type        = string
}

variable "sidefx_oauth_credentials_name" {
  description = "The name of the AWS Secrets Manager secret containing SideFX OAuth credentials"
}

variable "github_credentials_name" {
  description = "The name of the AWS Secrets Manager secret containing GitHub credentials"
}

provider "aws" {
  region = var.aws_region
}

resource "aws_vpc" "packer_vpc" {
  cidr_block = "10.0.0.0/16"
  tags       = { Name = "packer-vpc" }
}

resource "aws_subnet" "packer_subnet" {
  vpc_id            = aws_vpc.packer_vpc.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags              = { Name = "packer-subnet" }
}
resource "aws_internet_gateway" "packer_igw" {
  vpc_id = aws_vpc.packer_vpc.id
  tags   = { Name = "packer-igw" }
}

resource "aws_route_table" "packer_public_rt" {
  vpc_id = aws_vpc.packer_vpc.id
  tags   = { Name = "packer-public-rt" }
}

resource "aws_route" "packer_internet_access" {
  route_table_id         = aws_route_table.packer_public_rt.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.packer_igw.id
}

resource "aws_route_table_association" "packer_subnet_assoc" {
  subnet_id      = aws_subnet.packer_subnet.id
  route_table_id = aws_route_table.packer_public_rt.id
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {}

resource "aws_security_group" "packer_sg" {
  name        = "packer-sg"
  description = "Allow SSH and outbound"
  vpc_id      = aws_vpc.packer_vpc.id

  # Should limit to your IP
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


resource "aws_iam_role" "packer_role" {
  name               = "packer-create-ami-role"
  assume_role_policy = data.aws_iam_policy_document.packer_assume_role.json
}

data "aws_iam_policy_document" "packer_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "secrets_manager_access" {
  statement {
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.github_credentials_name}-*",
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.sidefx_oauth_credentials_name}-*",
    ]
    effect = "Allow"
  }
}

resource "aws_iam_policy" "secrets_manager_access" {
  name   = "packer-secrets-manager-access"
  policy = data.aws_iam_policy_document.secrets_manager_access.json
}

resource "aws_iam_role_policy_attachment" "packer_secrets_manager" {
  role       = aws_iam_role.packer_role.name
  policy_arn = aws_iam_policy.secrets_manager_access.arn
}


resource "aws_iam_role_policy_attachment" "packer_attach" {
  role       = aws_iam_role.packer_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2FullAccess"
}

resource "aws_iam_instance_profile" "packer_profile" {
  name = "packer-create-ami-profile"
  role = aws_iam_role.packer_role.name
}

output "vpc_id" {
  value = aws_vpc.packer_vpc.id
}

output "subnet_id" {
  value = aws_subnet.packer_subnet.id
}

output "security_group_id" {
  value = aws_security_group.packer_sg.id
}

output "provisioning_iam_role" {
  value = aws_iam_instance_profile.packer_profile.name
}
