resource "azurerm_storage_queue" "training" {
  name               = var.storage_queue_name
  storage_account_id = var.storage_account_id
}

resource "azurerm_eventgrid_system_topic" "storage" {
  name                = var.event_grid_system_topic_name
  resource_group_name = var.resource_group_name
  location            = var.location
  source_resource_id  = var.storage_account_id
  topic_type          = "Microsoft.Storage.StorageAccounts"
}

resource "azurerm_user_assigned_identity" "eventgrid_delivery" {
  name                = "${var.event_subscription_name}-identity"
  resource_group_name = var.resource_group_name
  location            = var.location
}

resource "azurerm_role_assignment" "eventgrid_queue_sender" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.eventgrid_delivery.principal_id
}

# Event Grid subscription is created by run.sh after Terraform apply,
# and deleted by run.sh before destroy. This avoids the persistent
# 'Internal error' on student subscriptions in India regions.