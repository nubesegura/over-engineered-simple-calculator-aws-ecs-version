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

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets of both AZs for the DB subnet group"
}

variable "security_group_id" {
  type        = string
  description = "Database security group (ingress only from the service and job groups)"
}

variable "kms_key_arn" {
  type        = string
  description = "Environment KMS key: storage, managed master secret and exported log groups"
}

variable "instance_class" {
  type        = string
  description = "Instance class (Graviton burstable)"
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  type        = number
  description = "gp3 storage size in GB"
  default     = 20
}

variable "db_name" {
  type        = string
  description = "Name of the database created with the instance"
  default     = "calculator"
}

variable "backup_retention_days" {
  type        = number
  description = "Automated backup retention in days (1 in dev, 15 in prod)"
}

variable "deletion_protection" {
  type        = bool
  description = "Deletion protection (prod only)"
}

variable "skip_final_snapshot" {
  type        = bool
  description = "Skip the final snapshot on destroy (dev only)"
}

variable "log_exports" {
  type        = list(string)
  description = "Engine logs shipped to CloudWatch Logs (prod only): postgresql, upgrade"
  default     = []
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the exported log groups in days"
  default     = 14
}

variable "master_rotation_days" {
  type        = number
  description = "Days between rotations of the RDS-managed master secret"
  default     = 3
}

variable "multi_az" {
  type        = bool
  description = "Multi-AZ standby. Owner-accepted exception for this project: Single-AZ in both environments"
  default     = false
}
