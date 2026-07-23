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


# ─── Azure DevOps module inputs (auto‑exported by run.sh) ───────────────

variable "ado_project_name" {
  description = "Azure DevOps project name (from bootstrap)."
  type        = string
}

variable "ado_github_service_connection_name" {
  description = "GitHub service connection name."
  type        = string
}

variable "ado_azure_service_connection_name" {
  description = "Azure service connection name (e.g. azdo-oidc-ci)."
  type        = string
}

variable "github_owner" {
  description = "GitHub repository owner."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
}

variable "state_rg_name" {
  description = "Resource group of the remote state storage account."
  type        = string
}

variable "state_storage_account_name" {
  description = "Storage account for remote state."
  type        = string
}

variable "state_container_name" {
  description = "Blob container for remote state."
  type        = string
}

variable "enable_request_failures_alert" {
  description = "Enable the request-failures scheduled query alert."
  type        = bool
  default     = false
}

variable "enable_slow_requests_alert" {
  description = "Enable the slow-requests scheduled query alert."
  type        = bool
  default     = false
}

variable "enable_exceptions_alert" {
  description = "Enable the exceptions scheduled query alert."
  type        = bool
  default     = false
}

variable "enable_validation_failures_alert" {
  description = "Enable the validation-failures scheduled query alert."
  type        = bool
  default     = false
}