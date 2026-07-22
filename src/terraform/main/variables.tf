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
  description = "Enable shared access key authentication for the storage account."
  type        = bool
  default     = false
}

variable "alert_email_address" {
  description = "Email address used by the monitoring action group."
  type        = string
}

# ACA images and port – not derived, differ per environment
variable "aca_training_image" {
  description = "Container image for the training job."
  type        = string
}

variable "aca_serving_image" {
  description = "Container image for the serving app."
  type        = string
}

variable "aca_serve_port" {
  description = "Ingress port for the serving app."
  type        = number
  default     = 80
}

# Event Grid raw data filter – may vary per environment
variable "event_raw_container_name" {
  description = "Blob container monitored for uploads."
  type        = string
  default     = "raw"
}

variable "event_raw_blob_prefix" {
  description = "Blob prefix inside the monitored container."
  type        = string
  default     = "monthly/"
}