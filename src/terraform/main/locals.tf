locals {
  project_name = "serverless-mlops"
  project_abbr = "sm"
  env_abbr     = var.environment == "staging" ? "stg" : "prod"

  # Last 6 chars of subscription ID for globally-unique resource names
  sub_suffix = substr(var.subscription_id, length(var.subscription_id) - 6, 6)

  common_tags = merge(
    {
      project     = local.project_name
      managed_by  = "opentofu"
      environment = var.environment
    },
    var.tags
  )

  # Derived names — single source of truth, no per-environment duplication
  artifact_resource_group_name = "rg-${local.project_abbr}-artifacts-${local.env_abbr}"
  storage_account_name         = "${local.project_abbr}${local.env_abbr}artifacts${local.sub_suffix}"
  acr_name                     = "acr${local.project_abbr}${local.env_abbr}${local.sub_suffix}"
  log_analytics_workspace_name = "law-${local.project_abbr}-${local.env_abbr}"
  application_insights_name    = "appi-${local.project_abbr}-${local.env_abbr}"
  workbook_display_name        = "Serverless MLOps - ${var.environment == "staging" ? "Staging" : "Production"}"
  action_group_name            = "ag-${local.project_abbr}-${local.env_abbr}"
  action_group_short_name      = "${local.project_abbr}${local.env_abbr}"
  ml_workspace_name            = "mlw-${local.project_abbr}-${local.env_abbr}-s7"
  ml_key_vault_name            = "kv-${local.project_abbr}${local.env_abbr}ml${local.sub_suffix}"
  ml_storage_account_name      = "${local.project_abbr}${local.env_abbr}mlsa${local.sub_suffix}"

  # ACA and eventing names
  aca_environment_name         = "acae-${local.project_abbr}-${local.env_abbr}"
  aca_train_job_name           = "acaj-train-${local.env_abbr}"
  aca_serve_app_name           = "aca-serve-${local.env_abbr}"
  aca_storage_queue_name       = "${local.project_abbr}trainqueue-${local.env_abbr}"
  event_grid_system_topic_name = "eg-${local.project_abbr}-${local.env_abbr}-storage"
  event_grid_subscription_name = "eg-${local.project_abbr}-${local.env_abbr}-raw-monthly"
}