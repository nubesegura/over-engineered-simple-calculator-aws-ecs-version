data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid   = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  family = "ecst-${local.name_mid}-ingest-${var.env_type}"
}

# ---------------------------------------------------------
# Log group (row 28)
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/${var.context}/${var.env_type}/ecs/ingest"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# ---------------------------------------------------------
# Roles of the job task (row 27)
# ---------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "exec" {
  name               = "iamr-${local.name_mid}-ingest-exec-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "exec" {
  name = "iamp-${local.name_mid}-ingest-exec-${var.env_type}"
  role = aws_iam_role.exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The authorization token does not support resource-level permissions.
        Sid      = "RegistryLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "PullIngestImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = var.image_repository_arn
      },
      {
        Sid      = "WriteIngestLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.ingest.arn}:*"
      },
    ]
  })
}

# Task role: application secret, the bucket prefixes it needs, the key and the topic.
# No ListBucket (a missing object must read as not found, and the job never lists).
resource "aws_iam_role" "task" {
  name               = "iamr-${local.name_mid}-ingest-task-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "task" {
  name = "iamp-${local.name_mid}-ingest-task-${var.env_type}"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadApplicationSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = var.app_secret_arn
      },
      {
        Sid      = "UseEnvironmentKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "secretsmanager.${local.region}.amazonaws.com",
              "s3.${local.region}.amazonaws.com",
              "sns.${local.region}.amazonaws.com",
            ]
          }
        }
      },
      {
        Sid      = "ReadAndRemoveIncoming"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = "${var.ingest_bucket_arn}/incoming/*"
      },
      {
        Sid    = "WriteResults"
        Effect = "Allow"
        Action = "s3:PutObject"
        Resource = [
          "${var.ingest_bucket_arn}/reports/*",
          "${var.ingest_bucket_arn}/processed/*",
          "${var.ingest_bucket_arn}/rejected/*",
        ]
      },
      {
        Sid      = "PublishAlerts"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.alert_topic_arn
      },
    ]
  })
}

# ---------------------------------------------------------
# Task definition (row 25). INGEST_BUCKET and INGEST_KEY are not set here: the rule
# target injects them on every run.
# ---------------------------------------------------------
resource "aws_ecs_task_definition" "ingest" {
  family                   = local.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # Ephemeral scratch space: the only writable path.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "ingest"
      image                  = "${var.image_repository_url}:${var.image_tag}"
      essential              = true
      privileged             = false
      readonlyRootFilesystem = true

      mountPoints = [{
        sourceVolume  = "tmp"
        containerPath = "/tmp"
        readOnly      = false
      }]

      environment = [
        { name = "ENVIRONMENT", value = var.env_type },
        { name = "SERVICE_NAME", value = "ingest" },
        { name = "APP_SECRET_ARN", value = var.app_secret_arn },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_PORT", value = tostring(var.db_port) },
        { name = "DB_NAME", value = var.db_name },
        { name = "ALERT_TOPIC_ARN", value = var.alert_topic_arn },
        { name = "CORS_ALLOWED_ORIGIN", value = var.cors_allowed_origin },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ingest.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "ingest"
        }
      }
    }
  ])

  depends_on = [
    aws_iam_role_policy.exec,
    aws_iam_role_policy.task,
  ]
}

# ---------------------------------------------------------
# Events role (row 37): starts the job and passes its two roles, nothing else
# ---------------------------------------------------------
data "aws_iam_policy_document" "events_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "iamr-${local.name_mid}-ingest-events-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
}

resource "aws_iam_role_policy" "events" {
  name = "iamp-${local.name_mid}-ingest-events-${var.env_type}"
  role = aws_iam_role.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Every revision of the ingest family, only on this cluster.
        Sid      = "RunIngestTask"
        Effect   = "Allow"
        Action   = "ecs:RunTask"
        Resource = "arn:aws:ecs:${local.region}:${local.account_id}:task-definition/${local.family}:*"
        Condition = {
          ArnEquals = { "ecs:cluster" = var.cluster_arn }
        }
      },
      {
        Sid      = "PassIngestRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.task.arn, aws_iam_role.exec.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

# ---------------------------------------------------------
# Rule 1 (row 35): CSV created under incoming/ starts one job run
# ---------------------------------------------------------
resource "aws_cloudwatch_event_rule" "ingest" {
  name        = "evr-${local.name_mid}-ingest-${var.env_type}"
  description = "Starts the ingestion job when a CSV file arrives under incoming/"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.ingest_bucket_name] }
      # One wildcard expresses prefix and suffix together (two filters on one field are ORed).
      object = { key = [{ wildcard = "incoming/*.csv" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule      = aws_cloudwatch_event_rule.ingest.name
  target_id = "ingest-task"
  arn       = var.cluster_arn
  role_arn  = aws_iam_role.events.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.ingest.arn
    task_count          = 1
    launch_type         = "FARGATE"
    platform_version    = "LATEST"

    network_configuration {
      subnets          = var.subnet_ids
      security_groups  = [var.security_group_id]
      assign_public_ip = false
    }
  }

  input_transformer {
    input_paths = {
      bucket = "$.detail.bucket.name"
      key    = "$.detail.object.key"
    }
    input_template = "{\"containerOverrides\":[{\"name\":\"ingest\",\"environment\":[{\"name\":\"INGEST_BUCKET\",\"value\":<bucket>},{\"name\":\"INGEST_KEY\",\"value\":<key>}]}]}"
  }

  retry_policy {
    maximum_retry_attempts       = 2
    maximum_event_age_in_seconds = 3600
  }
}

# ---------------------------------------------------------
# Rule 2 (row 36): any task of this cluster that stops with a non-zero exit code alerts
# ---------------------------------------------------------
resource "aws_cloudwatch_event_rule" "task_failed" {
  name        = "evr-${local.name_mid}-task-failed-${var.env_type}"
  description = "Alerts when a task of the cluster stops with a non-zero exit code or fails to start"

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [var.cluster_arn]
      lastStatus = ["STOPPED"]
      # Two cases: a container stopped with a non-zero exit code, or a task that never
      # started (image pull failed, NAT down at night), which has no exit code.
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "task_failed" {
  rule      = aws_cloudwatch_event_rule.task_failed.name
  target_id = "alerts-topic"
  arn       = var.alert_topic_arn

  input_transformer {
    input_paths = {
      task   = "$.detail.taskArn"
      group  = "$.detail.group"
      reason = "$.detail.stoppedReason"
      time   = "$.time"
    }
    input_template = "\"An ECS task failed (non-zero exit code or it never started). Group: <group>. Task: <task>. Reason: <reason>. Time: <time>. See the task logs in CloudWatch.\""
  }
}
