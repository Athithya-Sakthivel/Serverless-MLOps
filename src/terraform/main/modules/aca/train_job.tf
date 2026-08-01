resource "azurerm_container_app_job" "train" {
  name                         = var.train_job_name
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id

  replica_timeout_in_seconds = var.train_replica_timeout_seconds
  replica_retry_limit        = var.train_replica_retry_limit

  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = var.acr_login_server
    identity = "system"
  }

  manual_trigger_config {
    parallelism = 1
  }

  template {
    container {
      name   = "train"
      image  = var.training_image
      cpu    = var.train_cpu
      memory = var.train_memory

      command = ["sh", "-c"]
      args    = ["echo training placeholder; sleep 5"]

      env {
        name  = "MODE"
        value = "train"
      }

      env {
        name  = "RAW_CONTAINER_NAME"
        value = "raw"
      }

      env {
        name  = "CLEAN_CONTAINER_NAME"
        value = "clean"
      }

      env {
        name  = "STORAGE_ACCOUNT_NAME"
        value = var.storage_account_name
      }

      env {
        name  = "CHECKPOINT_CONTAINER_NAME"
        value = "checkpoints"
      }

      env {
        name  = "MLFLOW_TRACKING_URI"
        value = var.mlflow_tracking_uri
      }

      env {
        name  = "AZUREML_WORKSPACE_ID"
        value = var.ml_workspace_id
      }
    }
  }

  tags = var.tags
}