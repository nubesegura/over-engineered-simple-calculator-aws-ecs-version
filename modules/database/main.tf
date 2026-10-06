data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid   = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
  identifier = "rds-${local.name_mid}-${var.env_type}"
}

# ---------------------------------------------------------
# Subnet group (row 18) and parameter group (row 19)
# ---------------------------------------------------------
resource "aws_db_subnet_group" "main" {
  name        = "dbsg-${local.name_mid}-${var.env_type}"
  description = "Private subnets of both AZs for the calculator database"
  subnet_ids  = var.subnet_ids
}

resource "aws_db_parameter_group" "main" {
  name        = "dbpg-${local.name_mid}-${var.env_type}"
  family      = "postgres17"
  description = "Calculator database: TLS required"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "immediate"
  }
}

# ---------------------------------------------------------
# Exported engine logs: created here (not by RDS) so they have the key and retention
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "export" {
  for_each = toset(var.log_exports)

  name              = "/aws/rds/instance/${local.identifier}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# ---------------------------------------------------------
# RDS PostgreSQL instance (row 20) with the master secret managed by RDS (row 21)
# ---------------------------------------------------------
resource "aws_db_instance" "main" {
  identifier = local.identifier

  # Major version only: AWS picks the current minor and automatic minor upgrades move it.
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.instance_class

  storage_type           = "gp3"
  allocated_storage      = var.allocated_storage_gb
  storage_encrypted      = true
  kms_key_id             = var.kms_key_arn
  multi_az               = var.multi_az # owner-accepted exception: Single-AZ
  publicly_accessible    = false
  port                   = 5432
  db_name                = var.db_name
  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.main.name
  vpc_security_group_ids = [var.security_group_id]

  # No password argument: RDS creates and rotates the master secret in Secrets Manager.
  username                      = "calcadmin" # never a default user name
  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_arn

  iam_database_authentication_enabled = false # team standard (RDS-20)
  auto_minor_version_upgrade          = true
  copy_tags_to_snapshot               = true
  delete_automated_backups            = true
  performance_insights_enabled        = false

  backup_retention_period = var.backup_retention_days
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = var.skip_final_snapshot
  # Unique per destroy (a fixed name collides on the second destroy). The timestamp is only
  # evaluated when the argument changes, and the lifecycle ignores it afterwards.
  final_snapshot_identifier       = var.skip_final_snapshot ? null : "${local.identifier}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  enabled_cloudwatch_logs_exports = var.log_exports

  depends_on = [aws_cloudwatch_log_group.export]

  lifecycle {
    ignore_changes = [engine_version, final_snapshot_identifier]
  }
}

# ---------------------------------------------------------
# Rotation schedule of the RDS-managed master secret. The service owns the rotation
# function, so no Lambda is set. The first rotation happens at the first schedule.
# ---------------------------------------------------------
resource "aws_secretsmanager_secret_rotation" "master" {
  secret_id          = aws_db_instance.main.master_user_secret[0].secret_arn
  rotate_immediately = false

  rotation_rules {
    automatically_after_days = var.master_rotation_days
  }
}
