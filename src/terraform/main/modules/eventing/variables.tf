variable "resource_group_name" {
  description = "Resource group name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "storage_account_id" {
  description = "Storage account ID that owns the queue and emits blob events."
  type        = string
}

variable "storage_account_name" {
  description = "Storage account name used for the queue endpoint reference."
  type        = string
}

variable "storage_queue_name" {
  description = "Azure Storage queue name used as the Event Grid delivery destination."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,63}$", var.storage_queue_name))
    error_message = "storage_queue_name must be 3-63 characters and contain only lowercase letters, digits, and hyphens."
  }
}

variable "event_grid_system_topic_name" {
  description = "Event Grid system topic name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,64}$", var.event_grid_system_topic_name))
    error_message = "event_grid_system_topic_name must be 3-64 characters and contain only lowercase letters, digits, and hyphens."
  }
}

variable "event_subscription_name" {
  description = "Event Grid event subscription name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,64}$", var.event_subscription_name))
    error_message = "event_subscription_name must be 3-64 characters and contain only lowercase letters, digits, and hyphens."
  }
}

variable "raw_container_name" {
  description = "Blob container monitored for uploads."
  type        = string
  default     = "raw"
}

variable "raw_blob_prefix" {
  description = "Blob prefix inside the monitored container."
  type        = string
  default     = "monthly/"
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}