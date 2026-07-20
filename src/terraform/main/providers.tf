provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  # Use Azure AD instead of Shared Key for Storage data-plane operations
  storage_use_azuread = true
}

provider "azuread" {
  tenant_id = var.tenant_id
}