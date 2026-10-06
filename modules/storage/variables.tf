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

variable "account_id" {
  type        = string
  description = "AWS account id; part of the global bucket names and of the load balancer log path"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key that encrypts the ingestion bucket"
}

variable "processed_retention_days" {
  type        = number
  description = "Days before objects under processed/ expire"
}

variable "rejected_retention_days" {
  type        = number
  description = "Days before objects under rejected/ expire"
}

variable "reports_retention_days" {
  type        = number
  description = "Days before objects under reports/ expire"
}

variable "noncurrent_retention_days" {
  type        = number
  description = "Days before noncurrent object versions expire (both buckets)"
  default     = 30
}

variable "alb_log_retention_days" {
  type        = number
  description = "Days before load balancer access logs expire (30 dev, 90 prod)"
}

variable "alb_log_prefix" {
  type        = string
  description = "Prefix of the load balancer access logs inside the log bucket"
  default     = "alb"
}

variable "force_destroy" {
  type        = bool
  description = "Let destroy empty and delete the buckets (dev only, S3-15)"
  default     = false
}
