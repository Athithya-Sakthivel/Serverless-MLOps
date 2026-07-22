output "environment_name" {
  value = azurerm_container_app_environment.this.name
}

output "environment_id" {
  value = azurerm_container_app_environment.this.id
}

output "train_job_name" {
  value = azurerm_container_app_job.train.name
}

output "train_job_id" {
  value = azurerm_container_app_job.train.id
}

output "serve_app_name" {
  value = azurerm_container_app.serve.name
}

output "serve_app_id" {
  value = azurerm_container_app.serve.id
}

output "serve_app_latest_revision_fqdn" {
  value = azurerm_container_app.serve.latest_revision_fqdn
}

output "serve_app_latest_revision_name" {
  value = azurerm_container_app.serve.latest_revision_name
}