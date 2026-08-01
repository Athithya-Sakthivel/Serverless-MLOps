resource "azurerm_service_plan" "this" {
  name                = var.service_plan_name
  resource_group_name = var.resource_group_name
  location            = var.location

  os_type  = "Linux"
  sku_name = "FC1"

  tags = var.tags
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = var.function_app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.this.id

  # Deployed as a package in a blob container
  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.this.primary_blob_endpoint}${azurerm_storage_container.deploymentpackage.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.this.primary_access_key

  # Flex Consumption – runtime is set here, not in app_settings
  runtime_name    = var.runtime_name
  runtime_version = var.runtime_version

  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = var.instance_memory_in_mb

  identity {
    type = "SystemAssigned"
  }

  # Application Insights goes inside site_config, not as a top-level argument
  site_config {
    application_insights_connection_string = var.application_insights_connection_string
  }

  app_settings = {
    # ── ACA job start settings ──────────────────────────────────────
    ACA_SUBSCRIPTION_ID         = var.subscription_id
    ACA_RESOURCE_GROUP_NAME     = var.resource_group_name
    ACA_JOB_NAME                = var.aca_job_name
    ACA_JOB_API_VERSION         = var.aca_job_api_version
    ACA_REQUEST_TIMEOUT_SECONDS = tostring(var.aca_request_timeout_seconds)

    # ── Blob trigger identity‑based connection ──────────────────────
    SOURCE_STORAGE__blobServiceUri = var.source_storage_account_blob_endpoint
    SOURCE_STORAGE__credential     = "managedidentity"

    # Optional: improve Python indexing/startup performance
    PYTHON_ENABLE_INIT_INDEXING = "1"
  }

  tags = var.tags
}