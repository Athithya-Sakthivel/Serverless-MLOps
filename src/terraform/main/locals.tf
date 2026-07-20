locals {
  project_name = "serverless-mlops"
  project_abbr = "sm"
  env_abbr     = var.environment == "staging" ? "stg" : "prod"

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
  storage_account_name         = "${local.project_abbr}${var.environment}artifactsa"
  acr_name                     = "acr${local.project_abbr}${local.env_abbr}"
  log_analytics_workspace_name = "law-${local.project_abbr}-${local.env_abbr}"
  application_insights_name    = "appi-${local.project_abbr}-${local.env_abbr}"
  workbook_display_name        = "Serverless MLOps - ${var.environment == "staging" ? "Staging" : "Production"}"
  action_group_name            = "ag-${local.project_abbr}-${local.env_abbr}"
  action_group_short_name      = "${local.project_abbr}${local.env_abbr}"
}