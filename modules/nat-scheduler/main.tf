data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_default_tags" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  region_code = replace(data.aws_region.current.region, "-", "")
  name_mid    = "${local.region_code}-${var.context}"

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  function_name = "fnc-${local.name_mid}-nat-scheduler-${var.env_type}"
  log_group     = "/${var.context}/${var.env_type}/lambda/nat-scheduler"
  nat_name      = "nat-${local.name_mid}-${var.env_type}"

  # The function tags the NAT gateway with the five mandatory tags (provider default tags).
  tags_json = jsonencode(data.aws_default_tags.current.tags)

  ec2_arn_prefix = "arn:aws:ec2:${local.region}:${local.account_id}"
}

# ---------------------------------------------------------
# Function package (no third-party dependencies: boto3 comes with the runtime)
# ---------------------------------------------------------
data "archive_file" "function" {
  type        = "zip"
  source_dir  = var.handler_source_dir
  output_path = "${path.module}/build/nat-scheduler.zip"
  excludes    = ["__pycache__", "requirements.txt"]
}

# Created here so it has retention, the key and tags (Lambda would create it without them).
resource "aws_cloudwatch_log_group" "function" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# ---------------------------------------------------------
# Function role: NAT gateway, private default route, tagging and its own logs only
# ---------------------------------------------------------
resource "aws_iam_role" "function" {
  name = "iamr-${local.name_mid}-nat-fnc-${var.env_type}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "function" {
  name = "iamp-${local.name_mid}-nat-fnc-${var.env_type}"
  role = aws_iam_role.function.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          # Describe actions do not support resource-level permissions.
          Sid      = "ReadNetworkState"
          Effect   = "Allow"
          Action   = ["ec2:DescribeNatGateways", "ec2:DescribeRouteTables", "ec2:DescribeAddresses"]
          Resource = "*"
        },
        {
          # CreateNatGateway needs the subnet, the Elastic IP and the new gateway.
          Sid    = "CreateNatGateway"
          Effect = "Allow"
          Action = "ec2:CreateNatGateway"
          Resource = [
            "${local.ec2_arn_prefix}:subnet/${var.public_subnet_id}",
            "${local.ec2_arn_prefix}:elastic-ip/${var.eip_allocation_id}",
            "${local.ec2_arn_prefix}:natgateway/*",
          ]
        },
        {
          # Tags are applied at creation only (the five mandatory tags and the name).
          Sid      = "TagNatGatewayOnCreate"
          Effect   = "Allow"
          Action   = "ec2:CreateTags"
          Resource = "${local.ec2_arn_prefix}:natgateway/*"
          Condition = {
            StringEquals = { "ec2:CreateAction" = "CreateNatGateway" }
          }
        },
        {
          # Only a gateway that carries this module's Name tag can be deleted.
          Sid      = "DeleteTaggedNatGateway"
          Effect   = "Allow"
          Action   = "ec2:DeleteNatGateway"
          Resource = "${local.ec2_arn_prefix}:natgateway/*"
          Condition = {
            StringEquals = {
              "aws:ResourceTag/Name"      = local.nat_name
              "aws:ResourceTag/env-type"  = var.env_type
              "aws:ResourceTag/repo-name" = data.aws_default_tags.current.tags["repo-name"]
            }
          }
        },
        {
          # The default route of the private route table only.
          Sid      = "ManagePrivateDefaultRoute"
          Effect   = "Allow"
          Action   = ["ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute"]
          Resource = "${local.ec2_arn_prefix}:route-table/${var.private_route_table_id}"
        },
        {
          Sid      = "WriteOwnLogs"
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "${aws_cloudwatch_log_group.function.arn}:*"
        },
      ],
      var.enable_xray_tracing ? [{
        # X-Ray write actions do not support resource-level permissions.
        Sid      = "XRayTracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      }] : []
    )
  })
}

# ---------------------------------------------------------
# Function
# ---------------------------------------------------------
resource "aws_lambda_function" "nat_scheduler" {
  function_name = local.function_name
  role          = aws_iam_role.function.arn
  runtime       = "python3.14"
  architectures = ["arm64"]
  handler       = "handler.handler"
  timeout       = var.function_timeout_seconds
  memory_size   = 128

  filename         = data.archive_file.function.output_path
  source_code_hash = data.archive_file.function.output_base64sha256

  environment {
    variables = {
      PUBLIC_SUBNET_ID       = var.public_subnet_id
      PRIVATE_ROUTE_TABLE_ID = var.private_route_table_id
      EIP_ALLOCATION_ID      = var.eip_allocation_id
      NAME_TAG               = local.nat_name
      TAGS_JSON              = local.tags_json
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.function.name
  }

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  depends_on = [aws_iam_role_policy.function]
}

# ---------------------------------------------------------
# EventBridge Scheduler: off at night, on in the morning (invoke only this function)
# ---------------------------------------------------------
resource "aws_iam_role" "scheduler" {
  name = "iamr-${local.name_mid}-nat-scheduler-${var.env_type}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        # EventBridge Scheduler checks the role against the schedule group when it creates a schedule, so the condition names the
        # group (the default one). The role can only invoke this function, so the wider scope adds no permission.
        StringEquals = {
          "aws:SourceAccount" = local.account_id
          "aws:SourceArn"     = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule-group/default"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "iamp-${local.name_mid}-nat-scheduler-${var.env_type}"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeOnlyTheNatScheduler"
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.nat_scheduler.arn
    }]
  })
}

resource "aws_scheduler_schedule" "off" {
  name                         = "evs-${local.name_mid}-nat-off-${var.env_type}"
  description                  = "Remove the NAT gateway at night"
  schedule_expression          = var.off_schedule
  schedule_expression_timezone = var.time_zone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nat_scheduler.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "off" })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }

  depends_on = [aws_iam_role_policy.scheduler]
}

resource "aws_scheduler_schedule" "on" {
  name                         = "evs-${local.name_mid}-nat-on-${var.env_type}"
  description                  = "Create the NAT gateway and the private default route in the morning"
  schedule_expression          = var.on_schedule
  schedule_expression_timezone = var.time_zone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nat_scheduler.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "on" })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }

  depends_on = [aws_iam_role_policy.scheduler]
}

# ---------------------------------------------------------
# Apply creates the NAT gateway, destroy removes it. In CRUD mode the provider adds a
# "tf" key to the payload (action create, update or delete); the handler maps
# tf.action = delete to the off action.
# ---------------------------------------------------------
resource "aws_lambda_invocation" "nat" {
  function_name   = aws_lambda_function.nat_scheduler.function_name
  lifecycle_scope = "CRUD"
  input           = jsonencode({ action = "on" })

  depends_on = [
    aws_iam_role_policy.function,
    aws_cloudwatch_log_group.function,
  ]
}

# ---------------------------------------------------------
# Alarm on function errors
# ---------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "alrm-${local.name_mid}-nat-scheduler-errors-${var.env_type}"
  alarm_description   = "The NAT scheduler function failed: the NAT gateway and the private default route may be inconsistent"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.nat_scheduler.function_name
  }

  alarm_actions = [var.alert_topic_arn]
}
