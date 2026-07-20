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

output "app_request_failure_alert_id" {
  value = module.observability.app_request_failure_alert_id
}

output "app_slow_request_alert_id" {
  value = module.observability.app_slow_request_alert_id
}

output "app_exception_alert_id" {
  value = module.observability.app_exception_alert_id
}

output "app_validation_failure_alert_id" {
  value = module.observability.app_validation_failure_alert_id
}