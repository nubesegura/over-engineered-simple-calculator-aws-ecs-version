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

variable "vpc_cidr" {
  type        = string
  description = "CIDR block of the VPC (a /16; subnets are /24 slices of it)"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && endswith(var.vpc_cidr, "/16")
    error_message = "vpc_cidr must be a valid /16 CIDR block."
  }
}

variable "task_port" {
  type        = number
  description = "Port where the tasks serve HTTPS (load balancer to services)"
  default     = 8443
}

variable "database_port" {
  type        = number
  description = "PostgreSQL port"
  default     = 5432
}

variable "enable_flow_logs" {
  type        = bool
  description = "Create the VPC flow log with its role and log group (prod only, VPC-01)"
  default     = false
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the flow log group in days"
  default     = 14
}

variable "kms_key_arn" {
  type        = string
  description = "KMS key that encrypts the flow log group"
}
