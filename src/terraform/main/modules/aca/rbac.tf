resource "azurerm_role_assignment" "train_acr_pull" {
  scope                            = var.acr_id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_container_app_job.train.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "serve_acr_pull" {
  scope                            = var.acr_id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_container_app.serve.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "train_blob_contributor" {
  scope                            = var.storage_account_id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_container_app_job.train.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "train_queue_contributor" {
  scope                            = var.storage_account_id
  role_definition_name             = "Storage Queue Data Contributor"
  principal_id                     = azurerm_container_app_job.train.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "train_ml_workspace" {
  scope                            = var.ml_workspace_id
  role_definition_name             = "AzureML Data Scientist"
  principal_id                     = azurerm_container_app_job.train.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "serve_ml_workspace" {
  scope                            = var.ml_workspace_id
  role_definition_name             = "AzureML Data Scientist"
  principal_id                     = azurerm_container_app.serve.identity[0].principal_id
  skip_service_principal_aad_check = true
}