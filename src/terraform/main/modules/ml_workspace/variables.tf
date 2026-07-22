variable "resource_group_name" {
  description = "Resource group name for the Azure ML workspace."
  type        = string
}

variable "location" {
  description = "Azure region."
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

variable "workspace_name" {
  description = "Azure Machine Learning workspace name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,31}[a-z0-9]$", var.workspace_name))
    error_message = "workspace_name must be 3-33 characters, lowercase letters, digits, and hyphens only, and cannot start or end with a hyphen."
  }
}

variable "key_vault_name" {
  description = "Key Vault name used by the ML workspace."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.key_vault_name))
    error_message = "key_vault_name must be 3-24 characters, lowercase letters, digits, and hyphens only, and cannot start or end with a hyphen."
  }
}

variable "ml_storage_account_name" {
  description = "Dedicated storage account name for AML workspace (HNS disabled)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.ml_storage_account_name))
    error_message = "ml_storage_account_name must be 3-24 characters, lowercase letters and digits only."
  }
}

variable "datalake_storage_account_id" {
  description = "Data lake storage account ID (HNS enabled) for training data access."
  type        = string
}

variable "container_registry_id" {
  description = "Existing container registry ID used by the ML workspace."
  type        = string
}

variable "application_insights_id" {
  description = "Existing Application Insights ID used by the ML workspace."
  type        = string
}

variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "tenant_id" {
  description = "Azure tenant ID."
  type        = string
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}