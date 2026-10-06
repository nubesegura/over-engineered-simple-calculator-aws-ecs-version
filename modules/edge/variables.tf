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

variable "vpc_id" {
  type        = string
  description = "VPC where the target groups live"
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "Public subnets of the load balancer nodes"
}

variable "alb_security_group_id" {
  type        = string
  description = "Security group of the load balancer"
}

variable "log_bucket_id" {
  type        = string
  description = "Name of the load balancer access log bucket"
}

variable "log_prefix" {
  type        = string
  description = "Prefix of the access logs inside the log bucket"
  default     = "alb"
}

variable "deletion_protection" {
  type        = bool
  description = "Load balancer deletion protection (ELB-03: true in prod)"
  default     = false
}

variable "task_port" {
  type        = number
  description = "Port where the tasks serve HTTPS"
  default     = 8443
}

variable "cognito_user_pool_id" {
  type        = string
  description = "Cognito user pool ID whose ID tokens the load balancer verifies (COGNITO_USER_POOL_ID)"

  validation {
    condition     = length(trimspace(var.cognito_user_pool_id)) > 0
    error_message = "COGNITO_USER_POOL_ID is empty: set the environment variable (GitHub environment variable or local secrets.yml) before planning."
  }
}

variable "cognito_app_client_id" {
  type        = string
  description = "Cognito app client ID expected in the aud claim (COGNITO_APP_CLIENT_ID)"

  validation {
    condition     = length(trimspace(var.cognito_app_client_id)) > 0
    error_message = "COGNITO_APP_CLIENT_ID is empty: set the environment variable (GitHub environment variable or local secrets.yml) before planning."
  }
}

variable "enable_waf" {
  type        = bool
  description = "Associate the shared WAF ACL (ELB-11: prod only)"
  default     = false
}

variable "waf_acl_arn" {
  type        = string
  description = "ARN of the shared regional WAF ACL (WAF_ACL_ARN); the association exists only when enable_waf is true and this is set"
  default     = ""
}
