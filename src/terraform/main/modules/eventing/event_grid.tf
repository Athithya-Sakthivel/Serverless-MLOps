# =============================================================================
# event_grid.tf – Event Grid System Topic and Storage Queue delivery
#
# Creates a System Topic on the data lake storage account, a Storage Queue
# for the training job, a User-Assigned Managed Identity that Event Grid
# uses to authenticate to the queue, and the event subscription itself.
#
# Important:
#   - The subscription MUST use the dedicated System Topic Event Subscription
#     resource (azurerm_eventgrid_system_topic_event_subscription). The generic
#     resource (azurerm_eventgrid_event_subscription) targets the wrong ARM API
#     and returns "InvalidRequest: This event subscription operation is not
#     supported using this API call".
#   - The UAMI requires "Storage Queue Data Message Sender" (not Contributor)
#     on the storage account to push messages into the queue.
# =============================================================================

# ------------------------------------------------------------------------------
# Storage Queue – destination for blob-created events
# ------------------------------------------------------------------------------
resource "azurerm_storage_queue" "training" {
  name               = var.storage_queue_name
  storage_account_id = var.storage_account_id
}

# ------------------------------------------------------------------------------
# Event Grid System Topic – monitors blob creations in the storage account
# ------------------------------------------------------------------------------
resource "azurerm_eventgrid_system_topic" "storage" {
  name                = var.event_grid_system_topic_name
  resource_group_name = var.resource_group_name
  location            = var.location
  source_resource_id  = var.storage_account_id
  topic_type          = "Microsoft.Storage.StorageAccounts"
}

# ------------------------------------------------------------------------------
# User-Assigned Managed Identity – Event Grid uses this to push to the queue
# ------------------------------------------------------------------------------
resource "azurerm_user_assigned_identity" "eventgrid_delivery" {
  name                = "${var.event_subscription_name}-identity"
  resource_group_name = var.resource_group_name
  location            = var.location
}

# ------------------------------------------------------------------------------
# RBAC – grant the UAMI the ability to send messages to the Storage Queue
# ------------------------------------------------------------------------------
resource "azurerm_role_assignment" "eventgrid_queue_sender" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = azurerm_user_assigned_identity.eventgrid_delivery.principal_id
}

# ------------------------------------------------------------------------------
# System Topic Event Subscription – the link between blob events and the queue
# ------------------------------------------------------------------------------
resource "azurerm_eventgrid_system_topic_event_subscription" "blob_created" {
  name                = var.event_subscription_name
  resource_group_name = var.resource_group_name
  system_topic        = azurerm_eventgrid_system_topic.storage.name # must match the System Topic name

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  # Only trigger on parquet files under the configured raw container prefix
  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${var.raw_container_name}/blobs/${var.raw_blob_prefix}"
    subject_ends_with   = ".parquet"
  }

  # Destination – the storage queue created above
  storage_queue_endpoint {
    storage_account_id = var.storage_account_id
    queue_name         = azurerm_storage_queue.training.name
  }

  # Delivery with the UAMI – enables managed identity authentication
  delivery_identity {
    type                   = "UserAssigned"
    user_assigned_identity = azurerm_user_assigned_identity.eventgrid_delivery.id
  }

  # Ensure the role assignment exists before the subscription is created
  depends_on = [
    azurerm_role_assignment.eventgrid_queue_sender
  ]
}