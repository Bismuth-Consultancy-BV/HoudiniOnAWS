# Shared Infrastructure for Houdini on AWS
# This file contains resources shared between batch and interactive modes

############################
# VARIABLES
############################

variable "aws_region" {
  description = "The AWS region to deploy on"
  type        = string
}

variable "aurora_ami_name" {
  description = "The AMI name for the Aurora EC2 instances"
  type        = string
}

# You will likely want to restrict this to your own IP address for security reasons.
variable "admin_ip_access" {
  description = "The IP address that will have SSH access to the EC2 instances"
  type        = string
  default     = "0.0.0.0/0"
}

variable "s3_file_retention_days" {
  description = "The number of days a file will be retained in the S3 buckets"
  type        = number
  default     = 1
}

variable "aurora_ec2_machine_type" {
  description = "The type of EC2 instance that will be provisioned"
  type        = string
  default     = "t3.large"
}

variable "aurora_ec2_storage" {
  description = "The amount of storage in each EC2 instance that will be provisioned"
  type        = number
  default     = 50
}

variable "sidefx_oauth_credentials_name" {
  description = "The name of the AWS Secrets Manager secret containing SideFX OAuth credentials"
  type        = string
}

variable "vulkan_version" {
  description = "The version of Vulkan to install"
  type        = string
  default     = "1.3.280"
}

############################
# PROVIDER & DATA SOURCES
############################

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.2.0"
}

provider "aws" {
  region = var.aws_region
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

############################
# STORAGE RESOURCES (S3)
############################

resource "aws_s3_bucket" "input_bucket" {
  bucket        = "aurora-input-bucket"
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "input_bucket_cors" {
  bucket = aws_s3_bucket.input_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "POST", "GET"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "input_bucket_lifecycle" {
  bucket = aws_s3_bucket.input_bucket.id

  rule {
    id     = "expire-objects"
    status = "Enabled"

    expiration {
      days = var.s3_file_retention_days
    }

    filter {
      prefix = ""
    }
  }
}

resource "aws_s3_bucket_public_access_block" "input" {
  bucket = aws_s3_bucket.input_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "output_bucket" {
  bucket        = "aurora-output-bucket"
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "output_bucket_cors" {
  bucket = aws_s3_bucket.output_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "output_bucket_lifecycle" {
  bucket = aws_s3_bucket.output_bucket.id

  rule {
    id     = "expire-objects"
    status = "Enabled"

    expiration {
      days = var.s3_file_retention_days
    }

    filter {
      prefix = ""
    }
  }
}

resource "aws_s3_bucket_public_access_block" "output" {
  bucket = aws_s3_bucket.output_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

############################
# NETWORKING RESOURCES
############################

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    "Name" = "aurora-vpc"
  }
}

resource "aws_subnet" "public_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags = {
    "Name" = "aurora-public-subnet"
  }
}

resource "aws_subnet" "private_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.3.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = false
  tags = {
    "Name" = "aurora-private-subnet"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = {
    "Name" = "aurora-internet-gateway"
  }
}

resource "aws_route_table" "public_route_table" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  depends_on = [aws_internet_gateway.main]
  tags = {
    "Name" = "aurora-public-route-table"
  }
}

resource "aws_route_table" "private_route_table" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat_gateway.id
  }

  depends_on = [aws_internet_gateway.main]
  tags = {
    "Name" = "aurora-private-route-table"
  }
}

resource "aws_route_table_association" "public_association" {
  subnet_id      = aws_subnet.public_subnet.id
  route_table_id = aws_route_table.public_route_table.id
  depends_on     = [aws_route_table.public_route_table]
}

resource "aws_route_table_association" "private_association" {
  subnet_id      = aws_subnet.private_subnet.id
  route_table_id = aws_route_table.private_route_table.id
  depends_on     = [aws_route_table.private_route_table]
}

resource "aws_eip" "nat_gateway" {
  domain     = "vpc"
  depends_on = [aws_internet_gateway.main]
  tags = {
    "Name" : "aurora-nat-gateway-eip"
  }
}

resource "aws_nat_gateway" "nat_gateway" {
  allocation_id = aws_eip.nat_gateway.id
  subnet_id     = aws_subnet.public_subnet.id
  depends_on    = [aws_internet_gateway.main]
  tags = {
    "Name" : "aurora-nat-gateway"
  }
}

############################
# SECURITY GROUPS
############################

resource "aws_security_group" "aurora_app_security_group" {
  name        = "aurora-app-security-group"
  description = "Security group for EC2 instances"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_access]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "EC2 Security Group"
  }
}

############################
# AMI DATA SOURCE
############################

data "aws_ami" "latest" {
  most_recent = true
  owners      = [data.aws_caller_identity.current.account_id]

  filter {
    name   = "name"
    values = [var.aurora_ami_name]
  }
}

############################
# IAM ROLES & POLICIES FOR EC2
############################

resource "aws_iam_role" "ec2_role" {
  name = "aurora-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Principal = {
          Service = "ec2.amazonaws.com"
        },
        Effect = "Allow",
      },
    ],
  })
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "aurora-instance-profile"
  role = aws_iam_role.ec2_role.name
}

# S3 Access Policies
resource "aws_iam_policy" "aurora_app_s3_policy" {
  name        = "aurora-app-s3-upload-policy"
  description = "Policy to allow S3 PutObject and GetObject actions on output bucket"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl",
          "s3:GetObject"
        ],
        Resource = "arn:aws:s3:::aurora-output-bucket/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "aurora_app_s3_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.aurora_app_s3_policy.arn
}

resource "aws_iam_policy" "aurora_app_s3_input_policy" {
  name        = "aurora-app-s3-input-policy"
  description = "Policy to allow S3 GetObject action on input bucket"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:GetObjectAcl"
        ],
        Resource = "arn:aws:s3:::aurora-input-bucket/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "aurora_app_s3_input_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.aurora_app_s3_input_policy.arn
}

# EC2 Policies
resource "aws_iam_policy" "ec2_describe_tags_policy" {
  name = "aurora-ec2-describe-tags-policy"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = "ec2:DescribeTags",
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_describe_tags_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.ec2_describe_tags_policy.arn
}

resource "aws_iam_policy" "terminate_instances_policy" {
  name        = "aurora-terminate-instances-policy"
  description = "Policy to allow EC2 instances to terminate instances"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = "ec2:TerminateInstances",
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "terminate_instances_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.terminate_instances_policy.arn
}

# Secrets Manager Access
resource "aws_iam_policy" "secrets_manager_access" {
  name   = "aurora-secrets-manager-access-policy"
  policy = data.aws_iam_policy_document.secrets_manager_access.json
}

resource "aws_iam_role_policy_attachment" "secrets_manager_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.secrets_manager_access.arn
}

data "aws_iam_policy_document" "secrets_manager_access" {
  statement {
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.sidefx_oauth_credentials_name}-*",
    ]
    effect = "Allow"
  }
}

# CloudWatch Logs
resource "aws_iam_policy" "cloudwatch_logs_policy" {
  name        = "aurora-cloudwatch-logs-policy"
  description = "Policy to allow EC2 instances to write logs to CloudWatch"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
          "logs:PutRetentionPolicy"
        ],
        Resource = [
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ec2/aurora-jobs:*",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ec2/houdini-interactive:*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cloudwatch_logs_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.cloudwatch_logs_policy.arn
}

############################
# SHARED LAUNCH TEMPLATE
############################

resource "aws_launch_template" "aurora_app" {
  name_prefix   = "aurora-batch-"
  image_id      = data.aws_ami.latest.id
  instance_type = var.aurora_ec2_machine_type
  key_name      = "aurora-key-pair"

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = var.aurora_ec2_storage
    }
  }

  network_interfaces {
    associate_public_ip_address = true
    delete_on_termination       = true
    device_index                = 0
    subnet_id                   = aws_subnet.public_subnet.id
    security_groups             = [aws_security_group.aurora_app_security_group.id]
  }

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2_profile.arn
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    echo "Starting Aurora EC2 instance setup..."

    # The AURORA_TOOLING_ROOT is already set in /etc/environment
    # But we need to source it explicitly for this script
    source /etc/environment
    export HOME=/home/ubuntu
    export AWS_REGION=${var.aws_region}
    export S3_OUTPUT_BUCKET=${aws_s3_bucket.output_bucket.bucket}

    # Enter the tooling root
    cd $AURORA_TOOLING_ROOT

    # Fix Git safe.directory issue
    git config --global --add safe.directory $AURORA_TOOLING_ROOT

    # Get latest code from the repository
    git fetch && git pull

    cat <<EOF1 > /opt/aws/amazon-cloudwatch-agent/bin/config.json
    {
      "logs": {
              "logs_collected": {
                      "files": {
                              "collect_list": [
                                      {
                                              "file_path": "$AURORA_TOOLING_ROOT/logs/runtime.log",
                                              "log_group_class": "STANDARD",
                                              "log_group_name": "/aws/ec2/aurora-jobs",
                                              "log_stream_name": "{instance_id}/runtime.log",
                                              "retention_in_days": 7
                                      }
                              ]
                      }
              }
      },
      "metrics": {
              "metrics_collected": {
                      "statsd": {
                              "metrics_aggregation_interval": 60,
                              "metrics_collection_interval": 30,
                              "service_address": ":8125"
                      }
              }
      }
    }
    EOF1

    # Run cloudwatch agent
    sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json

    # Set up the environment
    chmod +x $AURORA_TOOLING_ROOT/runtime/batch/entrypoint.sh

    # Run the entrypoint script as the ubuntu user
    # This script will handle the rest of the setup
    sudo -u ubuntu -E bash -c "$AURORA_TOOLING_ROOT/runtime/batch/entrypoint.sh"
    EOF
  )

  lifecycle {
    create_before_destroy = true
  }
}

############################
# OUTPUTS
############################

output "aws_region" {
  value = var.aws_region
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_id" {
  value = aws_subnet.public_subnet.id
}

output "private_subnet_id" {
  value = aws_subnet.private_subnet.id
}

output "security_group_id" {
  value = aws_security_group.aurora_app_security_group.id
}

output "launch_template_id" {
  value = aws_launch_template.aurora_app.id
}

output "launch_template_name" {
  value = aws_launch_template.aurora_app.name
}

output "input_bucket_name" {
  value = aws_s3_bucket.input_bucket.bucket
}

output "output_bucket_name" {
  value = aws_s3_bucket.output_bucket.bucket
}
