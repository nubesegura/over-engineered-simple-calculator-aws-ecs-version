data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"

  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.region
  account_root = "arn:aws:iam::${local.account_id}:root"

  # Services whose requests use the key on behalf of an IAM principal of this account.
  via_services = [
    for s in ["s3", "sns", "ecr", "secretsmanager", "rds"] : "${s}.${local.region}.amazonaws.com"
  ]
}

# ---------------------------------------------------------
# KMS key (one customer managed key per environment)
# ---------------------------------------------------------
resource "aws_kms_key" "main" {
  description             = "Main CMK of ${var.context} (${var.env_type})"
  enable_key_rotation     = true
  deletion_window_in_days = var.deletion_window_in_days

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The account root delegates to IAM: whoever has IAM permissions can administer
        # the key (KMS-06). Replace with named administrators when they are defined.
        Sid       = "KeyAdministration"
        Effect    = "Allow"
        Principal = { AWS = local.account_root }
        Action = [
          "kms:Describe*",
          "kms:PutKeyPolicy",
          "kms:Create*",
          "kms:Update*",
          "kms:Enable*",
          "kms:RevokeGrant",
          "kms:List*",
          "kms:Disable*",
          "kms:Get*",
          "kms:Delete*",
          "kms:ScheduleKeyDeletion",
          "kms:CancelKeyDeletion",
          "kms:TagResource",
          "kms:UntagResource",
          "kms:RetireGrant",
          "kms:RotateKeyOnDemand",
        ]
        Resource = "*"
      },
      {
        # Data plane for IAM principals of this account (delegated by the account root),
        # only when the request comes through RDS, Secrets Manager, S3, SNS or ECR. Each
        # role still needs its own IAM permission on this key.
        Sid       = "KeyUsageViaServices"
        Effect    = "Allow"
        Principal = { AWS = local.account_root }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = local.account_id
            "kms:ViaService"    = local.via_services
          }
        }
      },
      {
        # Log groups of this account only (ECS, Lambda, flow logs, RDS exports).
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
      {
        # Publishers to the encrypted alerts topic (EventBridge rules, CloudWatch alarms)
        # Without this, alerts are silently dropped. S3 is not a producer: the bucket
        # notifies EventBridge only, and S3 reaches this key as an IAM principal via
        # kms:ViaService (KeyUsageViaServices).
        Sid    = "AwsServiceProducers"
        Effect = "Allow"
        Principal = {
          Service = ["events.amazonaws.com", "cloudwatch.amazonaws.com"]
        }
        Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
        }
      },
    ]
  })

  # KMS keys have no name attribute: the convention goes in the Name tag.
  tags = {
    Name = "kms-${local.name_mid}-data-${var.env_type}"
  }
}

resource "aws_kms_alias" "main" {
  name          = "alias/kms-${local.name_mid}-data-${var.env_type}"
  target_key_id = aws_kms_key.main.key_id
}

# ---------------------------------------------------------
# SNS topic for alerts and alarms
# ---------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name              = "sns-${local.name_mid}-alerts-${var.env_type}"
  kms_master_key_id = aws_kms_key.main.arn
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Id      = "AlertsTopicPolicy"
    Statement = [
      {
        # Only the CloudWatch alarms of this account.
        Sid       = "AllowCloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
          ArnLike      = { "aws:SourceArn" = "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:*" }
        }
      },
      {
        # Only the EventBridge rules of this account (failed task rule, rotation events).
        Sid       = "AllowEventBridgeRules"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
          ArnLike      = { "aws:SourceArn" = "arn:aws:events:${local.region}:${local.account_id}:rule/*" }
        }
      },
      {
        # Encryption in transit (SNS-03).
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = ["sns:Publish", "sns:Subscribe"]
        Resource  = aws_sns_topic.alerts.arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

# The owner confirms the subscription from the email the first time.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
