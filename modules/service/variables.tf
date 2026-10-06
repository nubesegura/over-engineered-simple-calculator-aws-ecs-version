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

variable "services" {
  type        = set(string)
  description = "HTTP services to run, one ECS service each (add, sub, mul, div, history)"
  default     = ["add", "sub", "mul", "div", "history"]
}

variable "cluster_arn" {
  type        = string
  description = "ARN of the ECS cluster the services run in"
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key: log group encryption, secret reads and encrypted topic publishing"
}

variable "log_retention_days" {
  type        = number
  description = "Retention of each service log group in days"
  default     = 14
}

variable "task_cpu" {
  type        = number
  description = "CPU units of each task"
  default     = 256
}

variable "task_memory" {
  type        = number
  description = "Memory in MiB of each task"
  default     = 512
}

variable "container_port" {
  type        = number
  description = "Port of the container (HTTPS)"
  default     = 8443
}

variable "health_check_grace_period_seconds" {
  type        = number
  description = "Seconds the service ignores failing load balancer health checks after a task starts"
  default     = 60
}

variable "image_repository_urls" {
  type        = map(string)
  description = "ECR repository URL by service"
}

variable "image_repository_arns" {
  type        = map(string)
  description = "ECR repository ARN by service: each execution role pulls only from its own"
}

variable "image_tag" {
  type        = string
  description = "Image tag (the commit SHA) of every task definition"

  validation {
    condition     = length(var.image_tag) > 0
    error_message = "image_tag must not be empty."
  }

  validation {
    condition     = !can(regex("^(migrate-)?unset-", var.image_tag))
    error_message = "image_tag is the placeholder (unset-...): set the IMAGE_TAG environment variable to the commit SHA before plan or apply."
  }
}

variable "target_group_arns" {
  type        = map(string)
  description = "Load balancer target group ARN by service"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets of the tasks"
}

variable "security_group_id" {
  type        = string
  description = "Security group of the service tasks"
}

variable "app_secret_arn" {
  type        = string
  description = "Application secret: the only secret the tasks can read"
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
  description = "Alerts topic the tasks publish internal errors to"
}

variable "cors_allowed_origin" {
  type        = string
  description = "Origin of the web page (CORS)"
}
