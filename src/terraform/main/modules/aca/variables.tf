variable "resource_group_name" {
  description = "Resource group that owns the ACA environment, job, and app."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "environment_name" {
  description = "Container Apps managed environment name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", var.environment_name))
    error_message = "environment_name must be 3-64 characters, lowercase letters, digits, and hyphens only, and cannot start or end with a hyphen."
  }
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace ID for Container Apps environment logging."
  type        = string
}

variable "training_image" {
  description = "Training image. Public placeholder now; replaced by CI/CD later."
  type        = string
}

variable "serving_image" {
  description = "Serving image. Public placeholder now; replaced by CI/CD later."
  type        = string
}

variable "train_job_name" {
  description = "Container App Job name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", var.train_job_name))
    error_message = "train_job_name must be 3-64 characters, lowercase letters, digits, and hyphens only, and cannot start or end with a hyphen."
  }
}

variable "serve_app_name" {
  description = "Container App name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", var.serve_app_name))
    error_message = "serve_app_name must be 3-64 characters, lowercase letters, digits, and hyphens only, and cannot start or end with a hyphen."
  }
}

variable "storage_account_id" {
  description = "Storage account ID that holds raw/clean data."
  type        = string
}

variable "storage_account_name" {
  description = "Storage account name used by the queue scaler."
  type        = string
}

variable "storage_queue_name" {
  description = "Azure Storage queue used as the event bridge for training."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,63}$", var.storage_queue_name))
    error_message = "storage_queue_name must be 3-63 characters and contain only lowercase letters, digits, and hyphens."
  }
}

variable "acr_id" {
  description = "Azure Container Registry ID."
  type        = string
}

variable "acr_login_server" {
  description = "Azure Container Registry login server."
  type        = string
}

variable "ml_workspace_id" {
  description = "Azure Machine Learning workspace ID."
  type        = string
}

variable "mlflow_tracking_uri" {
  description = "MLflow tracking URI for the training container."
  type        = string
}

variable "serve_port" {
  description = "Ingress port for the serving container."
  type        = number
  default     = 80

  validation {
    condition     = var.serve_port >= 1 && var.serve_port <= 65535
    error_message = "serve_port must be between 1 and 65535."
  }
}

variable "serve_cpu" {
  description = "CPU for the serving app."
  type        = number
  default     = 0.5

  validation {
    condition     = var.serve_cpu > 0
    error_message = "serve_cpu must be greater than 0."
  }
}

variable "serve_memory" {
  description = "Memory for the serving app."
  type        = string
  default     = "1Gi"
}

variable "serve_min_replicas" {
  description = "Minimum replicas for the serving app."
  type        = number
  default     = 0

  validation {
    condition     = var.serve_min_replicas >= 0
    error_message = "serve_min_replicas must be >= 0."
  }
}

variable "serve_max_replicas" {
  description = "Maximum replicas for the serving app."
  type        = number
  default     = 10

  validation {
    condition     = var.serve_max_replicas >= var.serve_min_replicas
    error_message = "serve_max_replicas must be >= serve_min_replicas."
  }
}

variable "train_cpu" {
  description = "CPU for the training job."
  type        = number
  default     = 2

  validation {
    condition     = var.train_cpu > 0
    error_message = "train_cpu must be greater than 0."
  }
}

variable "train_memory" {
  description = "Memory for the training job."
  type        = string
  default     = "4Gi"
}

variable "train_replica_timeout_seconds" {
  description = "Maximum runtime per training job execution."
  type        = number
  default     = 1800

  validation {
    condition     = var.train_replica_timeout_seconds > 0
    error_message = "train_replica_timeout_seconds must be greater than 0."
  }
}

variable "train_replica_retry_limit" {
  description = "Retry count for failed training executions."
  type        = number
  default     = 1

  validation {
    condition     = var.train_replica_retry_limit >= 0
    error_message = "train_replica_retry_limit must be >= 0."
  }
}

variable "train_parallelism" {
  description = "Parallel job executions per event batch."
  type        = number
  default     = 1

  validation {
    condition     = var.train_parallelism >= 1
    error_message = "train_parallelism must be >= 1."
  }
}

variable "train_replica_completion_count" {
  description = "Number of replicas that must complete for a successful job execution."
  type        = number
  default     = 1

  validation {
    condition     = var.train_replica_completion_count >= 1
    error_message = "train_replica_completion_count must be >= 1."
  }
}

variable "train_min_executions" {
  description = "Minimum executions for the event-driven job scaler."
  type        = number
  default     = 0

  validation {
    condition     = var.train_min_executions >= 0
    error_message = "train_min_executions must be >= 0."
  }
}

variable "train_max_executions" {
  description = "Maximum executions for the event-driven job scaler."
  type        = number
  default     = 1

  validation {
    condition     = var.train_max_executions >= var.train_min_executions
    error_message = "train_max_executions must be >= train_min_executions."
  }
}

variable "train_polling_interval_seconds" {
  description = "Polling interval for the event-driven job scaler."
  type        = number
  default     = 30

  validation {
    condition     = var.train_polling_interval_seconds > 0
    error_message = "train_polling_interval_seconds must be greater than 0."
  }
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}