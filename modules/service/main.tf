data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid   = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

# ---------------------------------------------------------
# Log groups (row 28), one per service
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "svc" {
  for_each = var.services

  name              = "/${var.context}/${var.env_type}/ecs/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# ---------------------------------------------------------
# Roles (row 27)
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

# Execution role: pulls its image and writes its logs, nothing else.
resource "aws_iam_role" "exec" {
  for_each = var.services

  name               = "iamr-${local.name_mid}-${each.key}-exec-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "exec" {
  for_each = var.services

  name = "iamp-${local.name_mid}-${each.key}-exec-${var.env_type}"
  role = aws_iam_role.exec[each.key].id

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
        Sid    = "PullOwnImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = var.image_repository_arns[each.key]
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.svc[each.key].arn}:*"
      },
    ]
  })
}

# Task role: application secret, the key and the alerts topic. No master secret, no S3.
resource "aws_iam_role" "task" {
  for_each = var.services

  name               = "iamr-${local.name_mid}-${each.key}-task-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "task" {
  for_each = var.services

  name = "iamp-${local.name_mid}-${each.key}-task-${var.env_type}"
  role = aws_iam_role.task[each.key].id

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
        # Decrypt for the secret; GenerateDataKey is what publishing to the encrypted
        # topic needs. Both only through Secrets Manager and SNS.
        Sid      = "UseEnvironmentKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "secretsmanager.${local.region}.amazonaws.com",
              "sns.${local.region}.amazonaws.com",
            ]
          }
        }
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
# Task definitions (row 25)
# ---------------------------------------------------------
resource "aws_ecs_task_definition" "svc" {
  for_each = var.services

  family                   = "ecst-${local.name_mid}-${each.key}-${var.env_type}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.exec[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # Ephemeral scratch space (TLS material): the only writable path.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = each.key
      image                  = "${var.image_repository_urls[each.key]}:${var.image_tag}"
      essential              = true
      privileged             = false
      readonlyRootFilesystem = true

      portMappings = [{
        containerPort = var.container_port
        protocol      = "tcp"
      }]

      mountPoints = [{
        sourceVolume  = "tmp"
        containerPath = "/tmp"
        readOnly      = false
      }]

      # Only references and addresses: the secret value is read by the task at run time.
      environment = [
        { name = "ENVIRONMENT", value = var.env_type },
        { name = "SERVICE_NAME", value = each.key },
        { name = "APP_SECRET_ARN", value = var.app_secret_arn },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_PORT", value = tostring(var.db_port) },
        { name = "DB_NAME", value = var.db_name },
        { name = "ALERT_TOPIC_ARN", value = var.alert_topic_arn },
        { name = "CORS_ALLOWED_ORIGIN", value = var.cors_allowed_origin },
        # Fargate mounts the ephemeral volume at /tmp owned by root, which the non-root user of the image cannot write to.
        # /dev/shm is a writable in-memory mount even with a read-only root filesystem, and the key never touches a disk.
        { name = "TLS_DIRECTORY", value = "/dev/shm" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.svc[each.key].name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = each.key
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
# ECS services (row 26)
# ---------------------------------------------------------
resource "aws_ecs_service" "svc" {
  for_each = var.services

  name                              = "ecss-${local.name_mid}-${each.key}-${var.env_type}"
  cluster                           = var.cluster_arn
  task_definition                   = aws_ecs_task_definition.svc[each.key].arn
  desired_count                     = 1
  health_check_grace_period_seconds = var.health_check_grace_period_seconds
  wait_for_steady_state             = false
  propagate_tags                    = "SERVICE"

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arns[each.key]
    container_name   = each.key
    container_port   = var.container_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}
