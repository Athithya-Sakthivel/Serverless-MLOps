output "workspace_name" {
  value = azurerm_machine_learning_workspace.this.name
}

output "workspace_id" {
  value = azurerm_machine_learning_workspace.this.id
}

output "mlflow_tracking_uri" {
  value = "azureml://${var.location}.api.azureml.ms/mlflow/v1.0/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.MachineLearningServices/workspaces/${azurerm_machine_learning_workspace.this.name}"
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

output "key_vault_id" {
  value = azurerm_key_vault.this.id
}

output "key_vault_uri" {
  value = azurerm_key_vault.this.vault_uri
}

output "ml_storage_account_name" {
  value = azurerm_storage_account.ml.name
}

output "ml_storage_account_id" {
  value = azurerm_storage_account.ml.id
}