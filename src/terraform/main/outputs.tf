output "artifact_resource_group_name" {
  value = module.state.resource_group_name
}

output "artifact_resource_group_id" {
  value = module.state.resource_group_id
}

output "storage_account_name" {
  value = module.state.storage_account_name
}

output "storage_account_id" {
  value = module.state.storage_account_id
}

output "storage_account_blob_endpoint" {
  value = module.state.storage_account_blob_endpoint
}

output "ml_storage_account_name" {
  value = module.ml_workspace.ml_storage_account_name
}

output "ml_storage_account_id" {
  value = module.ml_workspace.ml_storage_account_id
}

output "acr_name" {
  value = module.state.acr_name
}

output "acr_id" {
  value = module.state.acr_id
}

output "acr_login_server" {
  value = module.state.acr_login_server
}

output "log_analytics_workspace_name" {
  value = module.observability.log_analytics_workspace_name
}

output "log_analytics_workspace_id" {
  value = module.observability.log_analytics_workspace_id
}

output "application_insights_name" {
  value = module.observability.application_insights_name
}

output "application_insights_id" {
  value = module.observability.application_insights_id
}

output "application_insights_connection_string" {
  value     = module.observability.application_insights_connection_string
  sensitive = true
}

output "application_insights_instrumentation_key" {
  value     = module.observability.application_insights_instrumentation_key
  sensitive = true
}

output "workbook_id" {
  value = module.observability.workbook_id
}

output "workbook_name" {
  value = module.observability.workbook_name
}

output "action_group_id" {
  value = module.observability.action_group_id
}

output "ml_workspace_name" {
  value = module.ml_workspace.workspace_name
}

output "ml_workspace_id" {
  value = module.ml_workspace.workspace_id
}

output "mlflow_tracking_uri" {
  value = module.ml_workspace.mlflow_tracking_uri
}

output "ml_key_vault_name" {
  value = module.ml_workspace.key_vault_name
}

output "ml_key_vault_id" {
  value = module.ml_workspace.key_vault_id
}

output "ml_key_vault_uri" {
  value = module.ml_workspace.key_vault_uri
}

output "aca_environment_name" {
  value = module.aca.environment_name
}

output "aca_environment_id" {
  value = module.aca.environment_id
}

output "aca_train_job_name" {
  value = module.aca.train_job_name
}

output "aca_train_job_id" {
  value = module.aca.train_job_id
}

output "aca_serve_app_name" {
  value = module.aca.serve_app_name
}

output "aca_serve_app_id" {
  value = module.aca.serve_app_id
}

output "aca_serve_app_latest_revision_fqdn" {
  value = module.aca.serve_app_latest_revision_fqdn
}

output "aca_serve_app_latest_revision_name" {
  value = module.aca.serve_app_latest_revision_name
}

output "event_grid_system_topic_name" {
  value = module.eventing.system_topic_name
}

output "event_grid_system_topic_id" {
  value = module.eventing.system_topic_id
}

output "aca_storage_queue_name" {
  value = module.eventing.storage_queue_name
}