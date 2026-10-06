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

variable "alert_topic_arn" {
  type        = string
  description = "SNS topic that receives every alarm"
}

variable "cluster_name" {
  type        = string
  description = "ECS cluster name (ClusterName dimension)"
}

variable "service_names" {
  type        = map(string)
  description = "ECS service name by service key (ServiceName dimension)"
}

variable "target_group_arns" {
  type        = map(string)
  description = "Target group ARN by service key (same keys as service_names)"
}

variable "alb_arn" {
  type        = string
  description = "Application Load Balancer ARN (the LoadBalancer dimension is derived from it)"
}

variable "db_instance_identifier" {
  type        = string
  description = "RDS instance identifier (DBInstanceIdentifier dimension)"
}

variable "ingest_rule_arn" {
  type        = string
  description = "ARN of the EventBridge rule that starts the ingestion job (default event bus)"
}

variable "service_cpu_threshold_percent" {
  type        = number
  description = "ECS service CPU utilization (percent) above which the alarm fires"
}

variable "alb_5xx_threshold" {
  type        = number
  description = "Load balancer generated 5XX responses per 5 minutes above which the alarm fires"
}

variable "target_5xx_threshold" {
  type        = number
  description = "Target (application) 5XX responses per 5 minutes above which the alarm fires"
}

variable "db_free_storage_threshold_bytes" {
  type        = number
  description = "RDS free storage (bytes) below which the alarm fires"
}

variable "db_connections_threshold" {
  type        = number
  description = "RDS connection count above which the alarm fires"
}

variable "db_cpu_threshold_percent" {
  type        = number
  description = "RDS CPU utilization (percent) above which the alarm fires"
}
