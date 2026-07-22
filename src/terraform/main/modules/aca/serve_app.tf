data "azurerm_client_config" "current" {}

resource "azurerm_container_app" "serve" {
  name                         = var.serve_app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Multiple"

  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = var.acr_login_server
    identity = "system"
  }

  ingress {
    external_enabled = true
    target_port      = var.serve_port
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = local.serve_traffic_percentage
    }
  }

  template {
    min_replicas = var.serve_min_replicas
    max_replicas = var.serve_max_replicas

    container {
      name   = "serve"
      image  = var.serving_image
      cpu    = var.serve_cpu
      memory = var.serve_memory

      env {
        name  = "MODE"
        value = "serve"
      }

      env {
        name  = "PORT"
        value = tostring(var.serve_port)
      }

      env {
        name  = "MLFLOW_TRACKING_URI"
        value = var.mlflow_tracking_uri
      }

      env {
        name  = "AZUREML_WORKSPACE_ID"
        value = var.ml_workspace_id
      }
    }
  }

  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }

  tags = var.tags
}

resource "azuread_application" "serve" {
  display_name     = "${var.serve_app_name}-auth"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]

  web {
    redirect_uris = [
      azurerm_container_app.serve.latest_revision_fqdn != ""
      ? "https://${azurerm_container_app.serve.latest_revision_fqdn}/.auth/login/aad/callback"
      : "https://localhost/.auth/login/aad/callback"
    ]
    implicit_grant {
      id_token_issuance_enabled = true
    }
  }
}

resource "azuread_service_principal" "serve" {
  client_id = azuread_application.serve.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

resource "azapi_resource" "serve_auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  parent_id = azurerm_container_app.serve.id
  name      = "current"

  body = {
    properties = {
      platform = {
        enabled = true
      }
      globalValidation = {
        unauthenticatedClientAction = "Return401"
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId     = azuread_application.serve.client_id
            openIdIssuer = "https://sts.windows.net/${data.azurerm_client_config.current.tenant_id}/v2.0"
          }
        }
      }
    }
  }
}