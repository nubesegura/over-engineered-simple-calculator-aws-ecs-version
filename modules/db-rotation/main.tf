data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid   = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  secret_name   = "sm-${local.name_mid}-app-${var.env_type}"
  function_name = "fnc-${local.name_mid}-db-rotation-${var.env_type}"
  log_group     = "/${var.context}/${var.env_type}/lambda/db-rotation"
}

# ---------------------------------------------------------
# Application secret (row 21a)
# The initial password is ephemeral and written with the write-only argument: it never
# reaches the plan or the state. Rotation replaces it; Terraform does not track the value.
# ---------------------------------------------------------
ephemeral "random_password" "initial" {
  length  = 32
  special = false # the rotation function also avoids / @ " ' \ and backtick
}

resource "aws_secretsmanager_secret" "app" {
  name                    = local.secret_name
  description             = "Credential of the limited application database user (alternating users, rotated by the db-rotation function)"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "initial" {
  secret_id = aws_secretsmanager_secret.app.id

  secret_string_wo = jsonencode({
    username = var.app_username
    password = ephemeral.random_password.initial.result
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
    engine   = "postgres"
  })
  secret_string_wo_version = 1

  # The rotation function moves the AWSCURRENT stage to newer versions.
  lifecycle {
    ignore_changes = [version_stages]
  }
}

# Every secret has a resource policy that refuses non-TLS access (SCRT-06, SCRT-07).
resource "aws_secretsmanager_secret_policy" "app" {
  secret_arn          = aws_secretsmanager_secret.app.arn
  block_public_policy = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "secretsmanager:*"
      Resource  = "*"
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------
# Rotation function log group and role (row 21b)
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "function" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_iam_role" "function" {
  name = "iamr-${local.name_mid}-db-rotation-${var.env_type}"

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
  name = "iamp-${local.name_mid}-db-rotation-${var.env_type}"
  role = aws_iam_role.function.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "RotateApplicationSecret"
          Effect = "Allow"
          Action = [
            "secretsmanager:DescribeSecret",
            "secretsmanager:GetSecretValue",
            "secretsmanager:PutSecretValue",
            "secretsmanager:UpdateSecretVersionStage",
          ]
          Resource = aws_secretsmanager_secret.app.arn
        },
        {
          Sid      = "ReadMasterSecret"
          Effect   = "Allow"
          Action   = "secretsmanager:GetSecretValue"
          Resource = var.master_secret_arn
        },
        {
          # GetRandomPassword does not support resource-level permissions.
          Sid      = "GenerateRandomPassword"
          Effect   = "Allow"
          Action   = "secretsmanager:GetRandomPassword"
          Resource = "*"
        },
        {
          Sid      = "UseEnvironmentKey"
          Effect   = "Allow"
          Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
          Resource = var.kms_key_arn
          Condition = {
            StringEquals = { "kms:ViaService" = "secretsmanager.${local.region}.amazonaws.com" }
          }
        },
        {
          # Equivalent of AWSLambdaVPCAccessExecutionRole: these actions do not support
          # resource-level permissions.
          Sid    = "VpcNetworkInterfaces"
          Effect = "Allow"
          Action = [
            "ec2:CreateNetworkInterface",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeSubnets",
            "ec2:DeleteNetworkInterface",
            "ec2:AssignPrivateIpAddresses",
            "ec2:UnassignPrivateIpAddresses",
          ]
          Resource = "*"
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
# Rotation function: private subnets, jobs security group, no reserved concurrency
# ---------------------------------------------------------
resource "aws_lambda_function" "rotation" {
  function_name = local.function_name
  role          = aws_iam_role.function.arn
  runtime       = "python3.14"
  architectures = ["arm64"]
  handler       = "handler.handler"
  timeout       = 60
  memory_size   = 256

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  environment {
    variables = {
      MASTER_SECRET_ARN = var.master_secret_arn
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.security_group_id]
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

# Only Secrets Manager, only for the application secret (LMB-04, LMB-05).
resource "aws_lambda_permission" "secrets_manager" {
  statement_id   = "AllowSecretsManagerRotation"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.rotation.function_name
  principal      = "secretsmanager.amazonaws.com"
  source_arn     = aws_secretsmanager_secret.app.arn
  source_account = local.account_id
}

# ---------------------------------------------------------
# Rotation schedule of the application secret. No rotation at apply time: the first run
# needs the application user, which the migration creates after this stack.
# ---------------------------------------------------------
resource "aws_secretsmanager_secret_rotation" "app" {
  secret_id           = aws_secretsmanager_secret.app.id
  rotation_lambda_arn = aws_lambda_function.rotation.arn
  rotate_immediately  = false

  rotation_rules {
    automatically_after_days = var.rotation_schedule_expression == null ? var.rotation_days : null
    schedule_expression      = var.rotation_schedule_expression
    duration                 = var.rotation_window_duration
  }

  depends_on = [
    aws_lambda_permission.secrets_manager,
    aws_secretsmanager_secret_version.initial,
  ]
}

# ---------------------------------------------------------
# Alerts: function errors and failed rotations (any secret of the account) to SNS
# ---------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "alrm-${local.name_mid}-db-rotation-errors-${var.env_type}"
  alarm_description   = "The database rotation function failed: the application credential may not have rotated"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.rotation.function_name
  }

  alarm_actions = [var.alert_topic_arn]
}

locals {
  # Secrets Manager rotation events (CloudTrail service events) carry the secret in
  # detail.additionalEventData.SecretId. Only the secrets of this environment are matched:
  # the application secret and the RDS-managed master secret. A secret ARN is
  # "<name>-<6 random characters>", so the prefix (ARN without the suffix, and the bare
  # name) matches the ARN form and the name form of SecretId without knowing the suffix.
  rotation_secret_arns = [aws_secretsmanager_secret.app.arn, var.master_secret_arn]
  rotation_secret_prefixes = flatten([
    for arn in local.rotation_secret_arns : [
      substr(arn, 0, length(arn) - 7),
      split(":secret:", substr(arn, 0, length(arn) - 7))[1],
    ]
  ])
}

resource "aws_cloudwatch_event_rule" "rotation_failed" {
  name        = "evr-${local.name_mid}-db-rotation-failed-${var.env_type}"
  description = "A Secrets Manager rotation of this environment failed"

  event_pattern = jsonencode({
    source = ["aws.secretsmanager"]
    detail = {
      eventSource = ["secretsmanager.amazonaws.com"]
      eventName   = ["RotationFailed"]
      additionalEventData = {
        SecretId = [for p in local.rotation_secret_prefixes : { prefix = p }]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "rotation_failed" {
  rule      = aws_cloudwatch_event_rule.rotation_failed.name
  target_id = "alerts-topic"
  arn       = var.alert_topic_arn

  retry_policy {
    maximum_retry_attempts       = 2
    maximum_event_age_in_seconds = 3600
  }
}
