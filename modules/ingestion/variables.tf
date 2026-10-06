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

variable "cluster_arn" {
  type        = string
  description = "ARN of the ECS cluster the ingestion job runs in"
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key: log group encryption, secret reads, bucket objects and encrypted topic publishing"
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the job log group in days"
  default     = 14
}

variable "task_cpu" {
  type        = number
  description = "CPU units of the job task"
  default     = 256
}

variable "task_memory" {
  type        = number
  description = "Memory in MiB of the job task"
  default     = 512
}

variable "image_repository_url" {
  type        = string
  description = "ECR repository URL of the ingest image"
}

variable "image_repository_arn" {
  type        = string
  description = "ARN of the ingest repository: the only one the execution role can pull from"
}

variable "image_tag" {
  type        = string
  description = "Image tag (the commit SHA) of the task definition"

  validation {
    condition     = length(var.image_tag) > 0
    error_message = "image_tag must not be empty."
  }

  validation {
    condition     = !can(regex("^(migrate-)?unset-", var.image_tag))
    error_message = "image_tag is the placeholder (unset-...): set the IMAGE_TAG environment variable to the commit SHA before plan or apply."
  }
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets of the job tasks"
}

variable "security_group_id" {
  type        = string
  description = "Jobs security group of the job tasks"
}

variable "ingest_bucket_name" {
  type        = string
  description = "Name of the CSV ingestion bucket (the rule matches its events)"
}

variable "ingest_bucket_arn" {
  type        = string
  description = "ARN of the CSV ingestion bucket"
}

variable "app_secret_arn" {
  type        = string
  description = "Application secret: the only secret the job can read"
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

variable "alert_topic_arn" {
  type        = string
  description = "Alerts topic: the job publishes internal errors and the failed-task rule publishes to it"
}

variable "cors_allowed_origin" {
  type        = string
  description = "Origin of the web page (CORS); required by the shared settings, unused by the job"
}
