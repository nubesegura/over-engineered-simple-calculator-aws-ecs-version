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
  description = "Environment KMS key: log group encryption and secret reads"
}

variable "enable_container_insights" {
  type        = bool
  description = "Container Insights on the cluster (prod only, ECS-05)"
  default     = false
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the task log group in days"
  default     = 14
}

variable "task_cpu" {
  type        = number
  description = "CPU units of the migrate task"
  default     = 256
}

variable "task_memory" {
  type        = number
  description = "Memory in MiB of the migrate task"
  default     = 512
}

variable "image_repository_url" {
  type        = string
  description = "ECR repository URL of the image that holds the migrations (the history repository)"
}

variable "image_repository_arn" {
  type        = string
  description = "ARN of that repository: the only one the execution role can pull from"
}

variable "image_tag" {
  type        = string
  description = "Image tag (the commit SHA) of the migrate task definition"

  validation {
    condition     = length(var.image_tag) > 0
    error_message = "image_tag must not be empty."
  }

  validation {
    condition     = !can(regex("^(migrate-)?unset-", var.image_tag))
    error_message = "image_tag is the placeholder (unset-...): set the IMAGE_TAG environment variable to the commit SHA before plan or apply."
  }
}

variable "master_secret_arn" {
  type        = string
  description = "Master secret created by RDS (the migration creates and alters database users with it)"
}

variable "rotation_role_arn" {
  type        = string
  description = "ARN of the rotation function role: with the migrate task role, the only principal that may read the master secret"
}

variable "admin_principal_arns" {
  type        = list(string)
  default     = []
  description = "Extra role ARNs (administrators) exempt from the master secret deny; the account root is always exempt"
}

variable "app_secret_arn" {
  type        = string
  description = "Application secret (the migration sets the password of the application user from it)"
}

variable "db_host" {
  type        = string
  description = "Database endpoint address"
}

variable "db_port" {
  type        = number
  description = "Database port"
}

variable "db_name" {
  type        = string
  description = "Database name"
}

variable "job_subnet_ids" {
  type        = list(string)
  description = "Private subnets for run-task (passed through to the outputs; the task is started by the pipeline)"
}

variable "job_security_group_id" {
  type        = string
  description = "Jobs security group for run-task (passed through to the outputs)"
}
