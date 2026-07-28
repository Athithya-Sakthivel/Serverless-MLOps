# ------------------------------------------------------------------------------
# Bootstrap Key Vault
#
# Holds the Azure DevOps PAT so pipelines never store it in Azure DevOps.
# Created during bootstrap → exists before any pipeline runs.
# ------------------------------------------------------------------------------

resource "azurerm_key_vault" "bootstrap" {
  name                       = "kv-${var.project_name}"
  location                   = var.location
  resource_group_name        = var.state_rg
  tenant_id                  = data.azuread_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false   # bootstrap is not a production data store
  rbac_authorization_enabled = true
}

resource "azurerm_key_vault_secret" "azdo_pat" {
  name         = "azdo-pat"
  value        = var.azdo_personal_access_token
  key_vault_id = azurerm_key_vault.bootstrap.id

  lifecycle {
    ignore_changes = [value]   # allow manual rotation without Terraform drift
  }
}