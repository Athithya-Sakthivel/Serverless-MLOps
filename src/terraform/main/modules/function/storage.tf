resource "azurerm_storage_account" "this" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  tags = var.tags
}

resource "azurerm_storage_container" "deploymentpackage" {
  name                  = var.deployment_container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}