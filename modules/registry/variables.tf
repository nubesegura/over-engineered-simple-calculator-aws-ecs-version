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
  description = "Environment KMS key that encrypts the images"
}

variable "services" {
  type        = list(string)
  description = "Images of the project, one repository each"
  default     = ["add", "sub", "mul", "div", "history", "ingest"]
}

variable "images_to_keep" {
  type        = number
  description = "Number of images kept per repository by the lifecycle policy"
  default     = 5
}

variable "force_delete" {
  type        = bool
  description = "Delete repositories that still hold images on destroy (dev only)"
  default     = false
}
