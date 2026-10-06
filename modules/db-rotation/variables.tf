variable "context" {
  type        = string
  description = "Functional context shared by every resource of the project (naming convention segment)"

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.context))
    error_message = "context must contain only lowercase letters and digits."
  }
}

variable "env_type" {
  type        = string
  description = "Environment tier; always the last segment of every resource name"

  validation {
    condition     = contains(["dev", "prod"], var.env_type)
    error_message = "env_type must be dev or prod."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key: application secret, function log group and secret reads"
}

variable "alert_topic_arn" {
  type        = string
  description = "SNS topic that receives the function error alarm and the failed rotation event"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets where the rotation function runs"
}

variable "security_group_id" {
  type        = string
  description = "Jobs security group (egress 443 and the database port)"
}

variable "master_secret_arn" {
  type        = string
  description = "Master secret created by RDS; the function reads it to set the new password"
}

variable "db_host" {
  type        = string
  description = "Database endpoint address stored in the application secret"
}

variable "db_port" {
  type        = number
  description = "Database port stored in the application secret"
}

variable "db_name" {
  type        = string
  description = "Database name stored in the application secret"
}

variable "app_username" {
  type        = string
  description = "Base database user of the application (the alternate user is <name>_clone)"
  default     = "calc_app"
}

variable "package_path" {
  type        = string
  description = "Zip built by scripts/package_rotation.py (handler plus pinned dependencies)"
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the function log group in days"
  default     = 14
}

variable "enable_xray_tracing" {
  type        = bool
  description = "Active X-Ray tracing of the function (prod only, LMB-07)"
  default     = false
}

variable "secret_recovery_window_days" {
  type        = number
  description = "Recovery window of the application secret: 0 (immediate delete) in dev, 30 in prod"
}

variable "rotation_days" {
  type        = number
  description = "Days between rotations when no schedule expression is set"
  default     = 3
}

variable "rotation_schedule_expression" {
  type        = string
  description = "Secrets Manager cron expression (UTC) for the rotation start; null rotates every rotation_days days at any time"
  default     = null
}

variable "rotation_window_duration" {
  type        = string
  description = "Length of the rotation window (for example 10h); only with rotation_schedule_expression. It cannot cross midnight UTC"
  default     = null

  validation {
    condition     = var.rotation_window_duration == null || var.rotation_schedule_expression != null
    error_message = "rotation_window_duration needs rotation_schedule_expression."
  }
}
