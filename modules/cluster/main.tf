data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid   = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  migrate_log_group = "/${var.context}/${var.env_type}/ecs/migrate"
}

# ---------------------------------------------------------
# Cluster (row 24): Fargate only, Container Insights in prod
# ---------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "ecsc-${local.name_mid}-${var.env_type}"

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

# ---------------------------------------------------------
# Log group of the migrate task (row 28)
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "migrate" {
  name              = local.migrate_log_group
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# ---------------------------------------------------------
# Roles of the migrate task (row 27)
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

# Execution role: pulls the image and writes the task logs, nothing else.
resource "aws_iam_role" "migrate_exec" {
  name               = "iamr-${local.name_mid}-migrate-exec-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "migrate_exec" {
  name = "iamp-${local.name_mid}-migrate-exec-${var.env_type}"
  role = aws_iam_role.migrate_exec.id

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
        Sid    = "PullMigrationImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = var.image_repository_arn
      },
      {
        Sid      = "WriteTaskLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.migrate.arn}:*"
      },
    ]
  })
}

# Task role: reads the master secret and the application secret, with the key.
resource "aws_iam_role" "migrate_task" {
  name               = "iamr-${local.name_mid}-migrate-task-${var.env_type}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "migrate_task" {
  name = "iamp-${local.name_mid}-migrate-task-${var.env_type}"
  role = aws_iam_role.migrate_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadMasterAndApplicationSecrets"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = [var.master_secret_arn, var.app_secret_arn]
      },
      {
        Sid      = "DecryptSecretsWithEnvironmentKey"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = var.kms_key_arn
        Condition = {
          StringEquals = { "kms:ViaService" = "secretsmanager.${local.region}.amazonaws.com" }
        }
      },
    ]
  })
}

# ---------------------------------------------------------
# Migrate task definition (row 25). The image is the history one; the command is the
# image default unless run-task overrides it. Secrets are read by the task at run time
# from the ARNs below: no secret value is set here.
# ---------------------------------------------------------
resource "aws_ecs_task_definition" "migrate" {
  family                   = "ecst-${local.name_mid}-migrate-${var.env_type}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.migrate_exec.arn
  task_role_arn            = aws_iam_role.migrate_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # Ephemeral scratch space: the only writable path (the root file system is read-only).
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "migrate"
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
        { name = "MASTER_SECRET_ARN", value = var.master_secret_arn },
        { name = "APP_SECRET_ARN", value = var.app_secret_arn },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_PORT", value = tostring(var.db_port) },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migrate.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "migrate"
        }
      }
    }
  ])

  depends_on = [
    aws_iam_role_policy.migrate_exec,
    aws_iam_role_policy.migrate_task,
  ]
}

# ---------------------------------------------------------
# Resource policy of the RDS-managed master secret. It lives here (not in the database
# module) because this module knows the migrate role and the rotation unit already exists
# (database -> rotation -> cluster): putting it in the database module would be circular.
# ---------------------------------------------------------
resource "aws_secretsmanager_secret_policy" "master" {
  secret_arn          = var.master_secret_arn
  block_public_policy = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "secretsmanager:*"
        Resource  = "*"
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        # Only the migrate task, the rotation function and the administrators read the value.
        # AWS services acting by themselves (RDS managing and rotating its own secret) are
        # exempt through aws:PrincipalIsAWSService, so RDS keeps working.
        Sid       = "DenyReadExceptMigrateRotationAndAdmins"
        Effect    = "Deny"
        Principal = "*"
        Action    = "secretsmanager:GetSecretValue"
        Resource  = "*"
        Condition = {
          StringNotLike = {
            "aws:PrincipalArn" = concat(
              [
                aws_iam_role.migrate_task.arn,
                var.rotation_role_arn,
                "arn:aws:iam::${local.account_id}:root",
              ],
              var.admin_principal_arns,
            )
          }
          Bool = { "aws:PrincipalIsAWSService" = "false" }
        }
      },
    ]
  })
}
