output "storage_queue_name" {
  value = azurerm_storage_queue.training.name
}

output "storage_queue_id" {
  value = azurerm_storage_queue.training.id
}

output "system_topic_name" {
  value = azurerm_eventgrid_system_topic.storage.name
}

output "system_topic_id" {
  value = azurerm_eventgrid_system_topic.storage.id
}