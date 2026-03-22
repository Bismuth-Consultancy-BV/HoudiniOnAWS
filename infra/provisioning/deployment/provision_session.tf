# Aurora Session Infrastructure
# This creates the infrastructure needed for Aurora Session (real-time WebSocket Houdini)
# Shared infrastructure is in shared_infra.tf

############################
# SESSION-SPECIFIC VARIABLES
############################

variable "enable_session_mode" {
  description = "Enable Aurora Session mode."
  type        = bool
  default     = false
}

############################
# CloudWatch Log Group
############################

resource "aws_cloudwatch_log_group" "interactive_runtime" {
  count             = var.enable_session_mode ? 1 : 0
  name              = "/aws/ec2/aurora-session"
  retention_in_days = 7
}

############################
# DynamoDB for Session State
############################

resource "aws_dynamodb_table" "houdini_sessions" {
  count        = var.enable_session_mode ? 1 : 0
  name         = "aurora-session-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "connection_id"
    type = "S"
  }

  global_secondary_index {
    name            = "ConnectionIdIndex"
    hash_key        = "connection_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "Aurora Session Table"
  }
}

############################
# IAM Role for WebSocket Lambda
############################

resource "aws_iam_role" "websocket_lambda_role" {
  count = var.enable_session_mode ? 1 : 0
  name  = "aurora-session-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "websocket_lambda_policy" {
  count = var.enable_session_mode ? 1 : 0
  name  = "aurora-session-lambda-policy"
  role  = aws_iam_role.websocket_lambda_role[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.houdini_sessions[0].arn,
          "${aws_dynamodb_table.houdini_sessions[0].arn}/index/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:TerminateInstances",
          "ec2:DescribeInstances",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "execute-api:ManageConnections"
        ]
        Resource = "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*/*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.ec2_role.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.input_bucket.arn}/*"
        ]
      }
    ]
  })
}

############################
# Lambda Functions
############################

data "archive_file" "websocket_lambda" {
  count       = var.enable_session_mode ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/session/lambda_websocket_handler.py"
  output_path = "${path.module}/.terraform/lambda_websocket.zip"
}

# Connect handler
resource "aws_lambda_function" "websocket_connect" {
  count            = var.enable_session_mode ? 1 : 0
  filename         = data.archive_file.websocket_lambda[0].output_path
  function_name    = "aurora-session-connect"
  role             = aws_iam_role.websocket_lambda_role[0].arn
  handler          = "lambda_websocket_handler.connect_handler"
  source_code_hash = data.archive_file.websocket_lambda[0].output_base64sha256
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      SESSIONS_TABLE         = aws_dynamodb_table.houdini_sessions[0].name
      WEBSOCKET_API_ENDPOINT = aws_apigatewayv2_stage.production[0].invoke_url
      LAUNCH_TEMPLATE_NAME   = aws_launch_template.interactive_app[0].name
      SUBNET_ID              = aws_subnet.public_subnet.id
      SECURITY_GROUP_ID      = aws_security_group.aurora_app_security_group.id
      INPUT_BUCKET           = aws_s3_bucket.input_bucket.bucket
      OUTPUT_BUCKET          = aws_s3_bucket.output_bucket.bucket
    }
  }
}

# Disconnect handler
resource "aws_lambda_function" "websocket_disconnect" {
  count            = var.enable_session_mode ? 1 : 0
  filename         = data.archive_file.websocket_lambda[0].output_path
  function_name    = "aurora-session-disconnect"
  role             = aws_iam_role.websocket_lambda_role[0].arn
  handler          = "lambda_websocket_handler.disconnect_handler"
  source_code_hash = data.archive_file.websocket_lambda[0].output_base64sha256
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      SESSIONS_TABLE         = aws_dynamodb_table.houdini_sessions[0].name
      WEBSOCKET_API_ENDPOINT = aws_apigatewayv2_stage.production[0].invoke_url
      LAUNCH_TEMPLATE_NAME   = aws_launch_template.interactive_app[0].name
      SUBNET_ID              = aws_subnet.public_subnet.id
      SECURITY_GROUP_ID      = aws_security_group.aurora_app_security_group.id
      INPUT_BUCKET           = aws_s3_bucket.input_bucket.bucket
      OUTPUT_BUCKET          = aws_s3_bucket.output_bucket.bucket
    }
  }
}

# Message handler
resource "aws_lambda_function" "websocket_message" {
  count            = var.enable_session_mode ? 1 : 0
  filename         = data.archive_file.websocket_lambda[0].output_path
  function_name    = "aurora-session-message"
  role             = aws_iam_role.websocket_lambda_role[0].arn
  handler          = "lambda_websocket_handler.message_handler"
  source_code_hash = data.archive_file.websocket_lambda[0].output_base64sha256
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      SESSIONS_TABLE         = aws_dynamodb_table.houdini_sessions[0].name
      WEBSOCKET_API_ENDPOINT = aws_apigatewayv2_stage.production[0].invoke_url
      LAUNCH_TEMPLATE_NAME   = aws_launch_template.interactive_app[0].name
      SUBNET_ID              = aws_subnet.public_subnet.id
      SECURITY_GROUP_ID      = aws_security_group.aurora_app_security_group.id
      INPUT_BUCKET           = aws_s3_bucket.input_bucket.bucket
      OUTPUT_BUCKET          = aws_s3_bucket.output_bucket.bucket
    }
  }
}

############################
# Launch Template for Aurora Session Mode
############################

resource "aws_launch_template" "interactive_app" {
  count         = var.enable_session_mode ? 1 : 0
  name_prefix   = "aurora-session-"
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
    
    cat <<EOF1 > /opt/aws/amazon-cloudwatch-agent/bin/config.json
    {
      "logs": {
              "logs_collected": {
                      "files": {
                              "collect_list": [
                                      {
                                              "file_path": "/var/log/cloud-init-output.log",
                                              "log_group_name": "/aws/ec2/aurora-session",
                                              "log_stream_name": "{instance_id}/runtime.log"
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

    echo "Starting Aurora Session EC2 instance setup..."

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
    
    # Set up the environment for session mode
    chmod +x $AURORA_TOOLING_ROOT/runtime/session/entrypoint.sh

    # Run the session entrypoint script as the ubuntu user
    sudo -u ubuntu -E bash -c "$AURORA_TOOLING_ROOT/runtime/session/entrypoint.sh"
    EOF
  )

  lifecycle {
    create_before_destroy = true
  }
}

############################
# API Gateway WebSocket
############################

# IAM role for API Gateway logging
resource "aws_iam_role" "apigateway_cloudwatch" {
  count = var.enable_session_mode ? 1 : 0
  name  = "aurora-session-apigateway-cloudwatch-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "apigateway.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apigateway_cloudwatch" {
  count      = var.enable_session_mode ? 1 : 0
  role       = aws_iam_role.apigateway_cloudwatch[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

# Set account-level CloudWatch role for API Gateway
resource "aws_api_gateway_account" "main" {
  count               = var.enable_session_mode ? 1 : 0
  cloudwatch_role_arn = aws_iam_role.apigateway_cloudwatch[0].arn
}

resource "aws_apigatewayv2_api" "houdini_websocket" {
  count                      = var.enable_session_mode ? 1 : 0
  name                       = "aurora-session-api"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
}

resource "aws_cloudwatch_log_group" "websocket_api" {
  count             = var.enable_session_mode ? 1 : 0
  name              = "/aws/apigateway/aurora-session"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "websocket_api_execution" {
  count             = var.enable_session_mode ? 1 : 0
  name              = "/aws/apigateway/aurora-session-api/production"
  retention_in_days = 7
}

# Note: Lambda log groups are auto-created by Lambda runtime
# To manage their retention via Terraform, import them with:
# terraform import 'aws_cloudwatch_log_group.lambda_connect[0]' '/aws/lambda/aurora-session-connect'
# terraform import 'aws_cloudwatch_log_group.lambda_disconnect[0]' '/aws/lambda/aurora-session-disconnect'
# terraform import 'aws_cloudwatch_log_group.lambda_message[0]' '/aws/lambda/aurora-session-message'
# For now, set retention manually via AWS CLI:
# aws logs put-retention-policy --log-group-name /aws/lambda/aurora-session-connect --retention-in-days 7
# aws logs put-retention-policy --log-group-name /aws/lambda/aurora-session-disconnect --retention-in-days 7
# aws logs put-retention-policy --log-group-name /aws/lambda/aurora-session-message --retention-in-days 7

resource "aws_apigatewayv2_stage" "production" {
  count       = var.enable_session_mode ? 1 : 0
  api_id      = aws_apigatewayv2_api.houdini_websocket[0].id
  name        = "production"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.websocket_api[0].arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      protocol         = "$context.protocol"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }

  default_route_settings {
    logging_level            = "ERROR"
    data_trace_enabled       = false
    detailed_metrics_enabled = true
    throttling_burst_limit   = 500
    throttling_rate_limit    = 100
  }

}

# Connect route
resource "aws_apigatewayv2_integration" "connect" {
  count            = var.enable_session_mode ? 1 : 0
  api_id           = aws_apigatewayv2_api.houdini_websocket[0].id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.websocket_connect[0].invoke_arn
}

resource "aws_apigatewayv2_route" "connect" {
  count     = var.enable_session_mode ? 1 : 0
  api_id    = aws_apigatewayv2_api.houdini_websocket[0].id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.connect[0].id}"
}

# Disconnect route
resource "aws_apigatewayv2_integration" "disconnect" {
  count            = var.enable_session_mode ? 1 : 0
  api_id           = aws_apigatewayv2_api.houdini_websocket[0].id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.websocket_disconnect[0].invoke_arn
}

resource "aws_apigatewayv2_route" "disconnect" {
  count     = var.enable_session_mode ? 1 : 0
  api_id    = aws_apigatewayv2_api.houdini_websocket[0].id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.disconnect[0].id}"
}

# Message route
resource "aws_apigatewayv2_integration" "message" {
  count            = var.enable_session_mode ? 1 : 0
  api_id           = aws_apigatewayv2_api.houdini_websocket[0].id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.websocket_message[0].invoke_arn
}

resource "aws_apigatewayv2_route" "message" {
  count     = var.enable_session_mode ? 1 : 0
  api_id    = aws_apigatewayv2_api.houdini_websocket[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.message[0].id}"
}

# Lambda permissions
resource "aws_lambda_permission" "connect" {
  count         = var.enable_session_mode ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_connect[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.houdini_websocket[0].execution_arn}/*/*"
}

resource "aws_lambda_permission" "disconnect" {
  count         = var.enable_session_mode ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_disconnect[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.houdini_websocket[0].execution_arn}/*/*"
}

resource "aws_lambda_permission" "message" {
  count         = var.enable_session_mode ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_message[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.houdini_websocket[0].execution_arn}/*/*"
}

############################
# Outputs
############################

output "websocket_url" {
  value       = var.enable_session_mode ? aws_apigatewayv2_stage.production[0].invoke_url : null
  description = "WebSocket API Gateway URL for Aurora Session"
}

output "sessions_table_name" {
  value       = var.enable_session_mode ? aws_dynamodb_table.houdini_sessions[0].name : null
  description = "DynamoDB table name for session tracking"
}

output "session_launch_template_name" {
  value       = var.enable_session_mode ? aws_launch_template.interactive_app[0].name : null
  description = "Launch template name for Aurora Session instances"
}
