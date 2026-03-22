# Aurora Batch Processing Resources
# This file contains resources specific to Aurora Batch mode
# Shared infrastructure is in shared_infra.tf

############################
# BATCH-SPECIFIC VARIABLES
############################

variable "enable_batch_mode" {
  description = "Enable batch processing mode."
  type        = bool
  default     = true
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

############################
# SQS RESOURCES
############################

resource "aws_sqs_queue" "request_queue" {
  count                      = var.enable_batch_mode ? 1 : 0
  name                       = "aurora-request-message-queue"
  delay_seconds              = 0
  visibility_timeout_seconds = var.aurora_max_running_time + 300 # 5 minutes more than the expected max task completion time
  max_message_size           = 2048
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter_queue[0].arn
    maxReceiveCount     = var.aurora_max_request_retries
  })
}

resource "aws_sqs_queue" "dead_letter_queue" {
  count = var.enable_batch_mode ? 1 : 0
  name  = "aurora-dead-letter-queue"
}

resource "aws_sqs_queue" "response_queue" {
  count                      = var.enable_batch_mode ? 1 : 0
  name                       = "aurora-response-message-queue"
  delay_seconds              = 0
  visibility_timeout_seconds = 10
  max_message_size           = 2048
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true
}

resource "aws_iam_policy" "combined_sqs_policy" {
  count       = var.enable_batch_mode ? 1 : 0
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
          aws_sqs_queue.request_queue[0].arn,
          aws_sqs_queue.response_queue[0].arn,
        ]
      }
    ]
  })
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  count            = var.enable_batch_mode ? 1 : 0
  event_source_arn = aws_sqs_queue.request_queue[0].arn
  function_name    = aws_lambda_function.aurora_lambda[0].arn
  batch_size       = 1
  enabled          = true
}

############################
# LAMBDA RESOURCES FOR BATCH
############################

data "archive_file" "lambda" {
  count       = var.enable_batch_mode ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/batch/lambda_function.py"
  output_path = "${path.module}/.terraform/lambda_function.zip"
}

resource "aws_lambda_function" "aurora_lambda" {
  count            = var.enable_batch_mode ? 1 : 0
  filename         = data.archive_file.lambda[0].output_path
  function_name    = "aurora-batch-lambda"
  role             = aws_iam_role.lambda_exec[0].arn
  handler          = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.lambda[0].output_base64sha256
  runtime          = "python3.11"
  environment {
    variables = {
      SQS_QUEUE_URL        = aws_sqs_queue.request_queue[0].url
      MAX_INSTANCES        = var.aurora_ec2_max_instances
      LAUNCH_TEMPLATE_NAME = aws_launch_template.aurora_app.name
      SUBNET_ID            = aws_subnet.public_subnet.id
      SECURITY_GROUP_ID    = aws_security_group.aurora_app_security_group.id
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  count = var.enable_batch_mode ? 1 : 0
  name  = "aurora-batch-lambda-role"

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
  count = var.enable_batch_mode ? 1 : 0
  name  = "lambda_policy"
  role  = aws_iam_role.lambda_exec[0].id
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
        Resource = aws_sqs_queue.request_queue[0].arn
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

resource "aws_iam_policy" "aurora_lambda_passrole_policy" {
  count = var.enable_batch_mode ? 1 : 0
  name  = "aurora-lambda-passrole-policy"
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
  count      = var.enable_batch_mode ? 1 : 0
  role       = aws_iam_role.lambda_exec[0].name
  policy_arn = aws_iam_policy.aurora_lambda_passrole_policy[0].arn
}

############################
# OUTPUTS
############################

output "response_queue_url" {
  value       = var.enable_batch_mode ? aws_sqs_queue.response_queue[0].url : null
  description = "SQS response queue URL for batch processing"
}

output "request_queue_url" {
  value       = var.enable_batch_mode ? aws_sqs_queue.request_queue[0].url : null
  description = "SQS request queue URL for batch processing"
}
