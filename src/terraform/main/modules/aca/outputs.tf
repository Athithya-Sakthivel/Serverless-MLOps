output "environment_name" {
  description = "Name of the ACA environment."
  value       = azurerm_container_app_environment.this.name
}

output "environment_id" {
  description = "Resource ID of the ACA environment."
  value       = azurerm_container_app_environment.this.id
}

output "train_job_name" {
  description = "Name of the ACA training job."
  value       = azurerm_container_app_job.train.name
}

output "train_job_id" {
  description = "Resource ID of the ACA training job."
  value       = azurerm_container_app_job.train.id
}

output "serve_app_name" {
  description = "Name of the ACA serving app."
  value       = azurerm_container_app.serve.name
}

output "serve_app_id" {
  description = "Resource ID of the ACA serving app."
  value       = azurerm_container_app.serve.id
}

output "serve_app_latest_revision_fqdn" {
  description = "Latest revision FQDN of the serving app."
  value       = azurerm_container_app.serve.latest_revision_fqdn
}

output "serve_app_latest_revision_name" {
  description = "Latest revision name of the serving app."
  value       = azurerm_container_app.serve.latest_revision_name
}