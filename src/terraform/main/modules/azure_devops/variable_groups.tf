resource "azuredevops_variable_group" "elt_ci_vars" {
  project_id   = data.azuredevops_project.main.id
  name         = "elt-ci-vars"
  description  = "Variables for the ELT training pipeline"
  allow_access = true

  variable {
    name  = "AZURE_STORAGE_ACCOUNT_NAME"
    value = var.storage_account_name
  }

  variable {
    name  = "MLFLOW_TRACKING_URI"
    value = var.mlflow_tracking_uri
  }

  variable {
    name  = "RAW_CONTAINER_NAME"
    value = "raw"
  }

  variable {
    name  = "CLEAN_CONTAINER_NAME"
    value = "clean"
  }

  variable {
    name  = "CHECKPOINT_CONTAINER_NAME"
    value = "checkpoints"
  }

  variable {
    name  = "azureServiceConnection"
    value = var.azure_service_connection_name
  }
}