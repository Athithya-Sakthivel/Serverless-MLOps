variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "tenant_id" {
  description = "Azure tenant ID."
  type        = string
}

variable "location" {
  description = "Azure region for application resources."
  type        = string
}

variable "environment" {
  description = "Environment name, e.g. staging or prod."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be one of: staging, prod."
  }
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "storage_container_names" {
  description = "Blob containers to create in ADLS Gen2."
  type        = list(string)
  default     = ["raw", "clean", "models", "logs"]
}

variable "shared_access_key_enabled" {
  description = "Enable shared access key authentication. Set false after initial apply."
  type        = bool
  default     = true
}

variable "alert_email_address" {
  description = "Email address used by the monitoring action group."
  type        = string
}