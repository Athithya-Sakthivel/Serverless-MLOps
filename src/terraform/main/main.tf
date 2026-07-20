module "state" {
  source = "./modules/state"

  resource_group_name       = local.artifact_resource_group_name
  location                  = var.location
  storage_account_name      = local.storage_account_name
  acr_name                  = local.acr_name
  container_names           = var.storage_container_names
  shared_access_key_enabled = var.shared_access_key_enabled
  tags                      = local.common_tags
}

module "observability" {
  source = "./modules/observability"

  resource_group_name          = module.state.resource_group_name
  location                     = var.location
  environment                  = var.environment
  log_analytics_workspace_name = local.log_analytics_workspace_name
  application_insights_name    = local.application_insights_name
  workbook_display_name        = local.workbook_display_name
  action_group_name            = local.action_group_name
  action_group_short_name      = local.action_group_short_name
  alert_email_address          = var.alert_email_address
  tags                         = local.common_tags
}