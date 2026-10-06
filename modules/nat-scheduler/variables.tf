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

variable "public_subnet_id" {
  type        = string
  description = "Public subnet (AZ a) where the function creates the NAT gateway"
}

variable "private_route_table_id" {
  type        = string
  description = "Private route table whose default route the function creates and deletes"
}

variable "eip_allocation_id" {
  type        = string
  description = "Allocation id of the Elastic IP kept by the network unit and attached to the NAT gateway"
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key that encrypts the function log group"
}

variable "alert_topic_arn" {
  type        = string
  description = "SNS topic that receives the function error alarm"
}

variable "off_schedule" {
  type        = string
  description = "EventBridge Scheduler cron expression that removes the NAT gateway"
}

variable "on_schedule" {
  type        = string
  description = "EventBridge Scheduler cron expression that creates the NAT gateway"
}

variable "time_zone" {
  type        = string
  description = "IANA time zone of both schedules"
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

variable "function_timeout_seconds" {
  type        = number
  description = "Function timeout; it must cover the wait for the NAT gateway to become available"
  default     = 300
}
