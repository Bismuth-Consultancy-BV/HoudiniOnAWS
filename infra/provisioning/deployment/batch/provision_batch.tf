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
  default     = "0.0.0.0"
}

variable "s3_file_retention_days" {
  description = "The number of days a file will be retained in the S3 buckets"
  type        = number
  default     = 1
}

variable "aurora_ec2_machine_type" {
  description = "The type of EC2 instance that will be provisioned"
  type        = string
  default     = "g4dn.2xlarge"
}

variable "aurora_ec2_storage" {
  description = "The amount of storage in each EC2 instance that will be provisioned"
  type        = number
  default     = 50
}

variable "aurora_ec2_max_instances" {
  description = "The maximum number of EC2 instances that can be provisioned"
  type        = number
  default     = 1
}

variable "aurora_max_running_time" {
  description = "The maximum time in seconds that the EC2 instances can run"
  type        = number
  default     = 5400
}

variable "aurora_max_request_retries" {
  description = "The maximum number of times a request can be retried"
  type        = number
  default     = 1

}

variable "sidefx_oauth_credentials_name" {
  description = "The name of the AWS Secrets Manager secret containing SideFX OAuth credentials"
}



##################################
# Constants for Aurora
##################################

variable "vulkan_version" {
  description = "The version of Vulkan to install"
  type        = string
  default     = "1.3.280"
}

# Terraform configuration for AWS provider
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.16"
    }
  }

  required_version = ">= 1.2.0"
}

# AWS provider configuration
provider "aws" {
  region = var.aws_region
}

# Data source to fetch available AWS availability zones in the specified region
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}



######################## RESOURCE DEFINITIONS ###########################
############################
# STORAGE RESOURCES
############################

# S3 Bucket for storing the output of the Aurora tasks
resource "aws_s3_bucket" "input_bucket" {
  bucket        = "aurora-input-bucket"
  force_destroy = true
}

resource "aws_s3_bucket_lifecycle_configuration" "input_bucket_lifecycle" {
  bucket = aws_s3_bucket.input_bucket.id

  rule {
    id     = "expire-objects"
    status = "Enabled"

    # Number of days after which the objects will be deleted
    expiration {
      days = var.s3_file_retention_days
    }

    # Filter to apply the rule to all objects in the bucket
    filter {
      prefix = ""
    }
  }
}

# Block public access to the input S3 bucket
resource "aws_s3_bucket_public_access_block" "input" {
  bucket = aws_s3_bucket.input_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}



# S3 Bucket for storing the output of the Aurora tasks
resource "aws_s3_bucket" "output_bucket" {
  bucket        = "aurora-output-bucket"
  force_destroy = true
}

resource "aws_s3_bucket_lifecycle_configuration" "output_bucket_lifecycle" {
  bucket = aws_s3_bucket.output_bucket.id

  rule {
    id     = "expire-objects"
    status = "Enabled"

    # Number of days after which the objects will be deleted
    expiration {
      days = var.s3_file_retention_days
    }

    # Filter to apply the rule to all objects in the bucket
    filter {
      prefix = ""
    }
  }
}

# Block public access to the output S3 bucket
resource "aws_s3_bucket_public_access_block" "output" {
  bucket = aws_s3_bucket.output_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}



############################
# SQS RESOURCES
############################
# Create an SQS queue (This is where the end user will send messages to trigger the EC2 task)
resource "aws_sqs_queue" "request_queue" {
  name                       = "aurora-request-message-queue"
  delay_seconds              = 0
  visibility_timeout_seconds = var.aurora_max_running_time + 300 # 5 minutes more than the expected max task completion time
  max_message_size           = 2048
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter_queue.arn
    maxReceiveCount     = var.aurora_max_request_retries
  })
}

resource "aws_sqs_queue" "dead_letter_queue" {
  name = "aurora-dead-letter-queue"
}

# Create an SQS queue for responses (This is where the EC2 task will send messages upon completion)
resource "aws_sqs_queue" "response_queue" {
  name                       = "aurora-response-message-queue"
  delay_seconds              = 0
  visibility_timeout_seconds = 10
  max_message_size           = 2048
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true
}

resource "aws_iam_policy" "combined_sqs_policy" {
  name        = "aurora-combined-sqs-policy"
  description = "Policy to allow full access to necessary SQS actions"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:GetQueueAttributes",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility"
        ],
        Resource = [
          aws_sqs_queue.request_queue.arn,
          aws_sqs_queue.response_queue.arn,
        ]
      }
    ]
  })
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.request_queue.arn
  function_name    = aws_lambda_function.aurora_lambda.arn
  batch_size       = 1
  enabled          = true
}



############################
# NETWORKING RESOURCES
############################
# The main virtual private cloud (VPC) for the resources
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    "Name" = "aurora-vpc"
  }
}

# Public Subnet (for NAT Gateway)
resource "aws_subnet" "public_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0] # Use the first available AZ
  map_public_ip_on_launch = true
  tags = {
    "Name" = "aurora-public-subnet"
  }
}

# Private Subnet for EC2 Tasks (no public IP)
resource "aws_subnet" "private_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.3.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1] # Use the second available AZ
  map_public_ip_on_launch = false
  tags = {
    "Name" = "aurora-private-subnet"
  }
}

# Internet Gateway for the VPC
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = {
    "Name" = "aurora-internet-gateway"
  }
}

# Public Route Table for the public subnet (NAT Gateway placement)
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

# Private Route Table for private subnet (using NAT Gateway)
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

# Associate the public subnet with the public route table
resource "aws_route_table_association" "public_association" {
  subnet_id      = aws_subnet.public_subnet.id
  route_table_id = aws_route_table.public_route_table.id
  depends_on     = [aws_route_table.public_route_table]
}

# Associating the private route table with the private subnet
resource "aws_route_table_association" "private_association" {
  subnet_id      = aws_subnet.private_subnet.id
  route_table_id = aws_route_table.private_route_table.id
  depends_on     = [aws_route_table.private_route_table]
}

# Elastic IP for the NAT Gateway
resource "aws_eip" "nat_gateway" {
  vpc        = true
  depends_on = [aws_internet_gateway.main]
  tags = {
    "Name" : "aurora-nat-gateway-eip"
  }
}

# NAT Gateway for the private subnet
resource "aws_nat_gateway" "nat_gateway" {
  allocation_id = aws_eip.nat_gateway.id
  subnet_id     = aws_subnet.public_subnet.id
  depends_on    = [aws_internet_gateway.main]
  tags = {
    "Name" : "aurora-nat-gateway"
  }
}



############################
# AMI
############################
# Data source to fetch the latest AMI for the EC2 instances
# This AMI is created by build_util.py --build_ami
data "aws_ami" "latest" {
  most_recent = true
  owners      = [data.aws_caller_identity.current.account_id]

  filter {
    name   = "name"
    values = [var.aurora_ami_name]
  }
}



############################
# LAMBDA RESOURCES
############################
data "archive_file" "lambda" {
  type        = "zip"
  source_file = "lambda_function.py"
  output_path = "lambda_function.zip"
}

resource "aws_lambda_function" "aurora_lambda" {
  filename         = data.archive_file.lambda.output_path
  function_name    = "aurora_lambda"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.lambda.output_base64sha256
  runtime          = "python3.8"
  environment {
    variables = {
      SQS_QUEUE_URL        = aws_sqs_queue.request_queue.url
      MAX_INSTANCES        = var.aurora_ec2_max_instances
      LAUNCH_TEMPLATE_NAME = aws_launch_template.aurora_app.name
      SUBNET_ID            = aws_subnet.public_subnet.id
      SECURITY_GROUP_ID    = aws_security_group.aurora_app_security_group.id
    }
  }
}

resource "aws_iam_policy" "aurora_lambda_passrole_policy" {
  name = "aurora-lambda-passrole-policy"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = "iam:PassRole",
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${aws_iam_role.ec2_role.name}"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "aurora_lambda_passrole_policy_attachment" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.aurora_lambda_passrole_policy.arn
}

resource "aws_iam_role" "lambda_exec" {
  name = "aurora-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Principal = {
          Service = "lambda.amazonaws.com"
        },
        Effect = "Allow",
      },
    ],
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "lambda_policy"
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "autoscaling:SetDesiredCapacity",
          "autoscaling:DescribeAutoScalingGroups"
        ],
        Resource = "*"
      },
      {
        Effect = "Allow",
        Action = [
          "sqs:GetQueueAttributes",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
        ],
        Resource = aws_sqs_queue.request_queue.arn
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow",
        Action = [
          "ec2:RunInstances",
          "ec2:DescribeInstances",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups",
          "ec2:CreateTags",
        ],
        Resource = "*"
      },
      {
        Effect   = "Allow",
        Action   = "iam:PassRole",
        Resource = aws_iam_instance_profile.ec2_profile.arn
      },
    ]
  })
}



############################
# LAUNCH TEMPLATE
############################
resource "aws_launch_template" "aurora_app" {
  name_prefix   = "aurora-app-"
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
      "agent": {
              "run_as_user": "cwagent"
      },
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
    chmod +x $AURORA_TOOLING_ROOT/runtime/entrypoint.sh

    # Run the entrypoint script as the ubuntu user
    # This script will handle the rest of the setup
    sudo -u ubuntu -E bash -c "$AURORA_TOOLING_ROOT/runtime/entrypoint.sh"
    EOF
  )

  lifecycle {
    create_before_destroy = true
  }
}

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

resource "aws_security_group" "aurora_app_security_group" {
  name        = "aurora-app-security-group"
  description = "Security group for EC2 instances"
  vpc_id      = aws_vpc.main.id

  # Allow SSH access from a specific IP address for maintenance
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.admin_ip_access}/32", ]
  }

  # Outbound rules - allow all outbound traffic for general internet access and AWS services
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

# Define the IAM policy with the necessary permissions
resource "aws_iam_policy" "aurora_app_s3_policy" {
  name        = "aurora-app-s3-upload-policy"
  description = "Policy to allow S3 PutObject action"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl"
        ],
        Resource = "arn:aws:s3:::aurora-output-bucket/*"
      }
    ]
  })
}

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

# Attach the policy to the IAM role
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

resource "aws_iam_role_policy_attachment" "aurora_app_s3_input_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.aurora_app_s3_input_policy.arn
}



############################
# SECRETS MANAGER RESOURCES
############################
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



############################
# LOGGING RESOURCES
############################
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
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ec2/aurora-jobs:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cloudwatch_logs_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.cloudwatch_logs_policy.arn
}

output "response_queue_url" {
  value = aws_sqs_queue.response_queue.url
}

output "request_queue_url" {
  value = aws_sqs_queue.request_queue.url
}

output "aws_region" {
  value = var.aws_region
}
