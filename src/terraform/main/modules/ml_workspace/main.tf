resource "azurerm_storage_account" "ml" {
  name                     = var.ml_storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = false

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false

  tags = var.tags
}

resource "azurerm_key_vault" "this" {
  name                = var.key_vault_name
  location            = var.location
  resource_group_name = var.resource_group_name
  tenant_id           = var.tenant_id
  sku_name            = "standard"

  soft_delete_retention_days = 7
  purge_protection_enabled   = var.environment == "prod"
  rbac_authorization_enabled = true

  tags = var.tags
}

resource "azurerm_machine_learning_workspace" "this" {
  name                    = var.workspace_name
  location                = var.location
  resource_group_name     = var.resource_group_name
  application_insights_id = var.application_insights_id
  key_vault_id            = azurerm_key_vault.this.id
  storage_account_id      = azurerm_storage_account.ml.id
  container_registry_id   = var.container_registry_id

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags

  # Azure ML automatically creates role assignments for the workspace's managed
  # identity on the linked storage, key vault, and container registry. Do NOT
  # define those assignments here — they would cause 409 conflicts.
}

# Additional role: workspace identity on the *data lake* storage (not the
# workspace's own storage). This one is not auto-created by Azure ML.
resource "azurerm_role_assignment" "workspace_datalake_blob" {
  scope                = var.datalake_storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_machine_learning_workspace.this.identity[0].principal_id
}