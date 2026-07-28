# ------------------------------------------------------------------------------
# Serving Container App (canary‑ready)
#
# Provisioned once by Terraform, then updated by the CD pipeline with new
# image tags and traffic splits.  Traffic is initially 100 % to the latest
# revision so that the first deployment “just works”.
# ------------------------------------------------------------------------------

data "azurerm_client_config" "current" {}

# ---------------------------------------------------------------------------
# Container App
# ---------------------------------------------------------------------------
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
    transport        = "http"             # explicit – avoids auto‑negotiation
    allow_insecure   = false

    traffic_weight {
      latest_revision = true
      percentage      = 100              # CD pipeline overrides this after bootstrap
    }
  }

  template {
    min_replicas = var.serve_min_replicas
    max_replicas = var.serve_max_replicas

    # --------------------------------------------------------------------
    # Container definition
    # --------------------------------------------------------------------
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
      # Added: Application Insights telemetry
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.app_insights_connection_string
      }

      # ------------------------------------------------------------------
      # Health probes – used by the platform and canary verification
      # ------------------------------------------------------------------
      readiness_probe {
        transport      = "HTTP"
        port           = var.serve_port
        path           = "/ready"
        interval_seconds = 10
        timeout_seconds  = 5
        success_threshold = 1
        failure_threshold = 3
      }
      liveness_probe {
        transport      = "HTTP"
        port           = var.serve_port
        path           = "/health"
        interval_seconds = 30
        timeout_seconds  = 5
        failure_threshold = 3
      }
      startup_probe {
        transport      = "HTTP"
        port           = var.serve_port
        path           = "/health"
        interval_seconds = 5
        timeout_seconds  = 30
        failure_threshold = 60
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

# ---------------------------------------------------------------------------
# Entra ID authentication (unchanged logic, but stabilised redirect URI)
# ---------------------------------------------------------------------------
resource "azuread_application" "serve" {
  display_name     = "${var.serve_app_name}-auth"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]

  web {
    # Use a stable placeholder until the first deploy.
    # After the first `tofu apply` you can replace this with the real FQDN
    # (output `serve_app_latest_revision_fqdn`) to avoid a permanent diff.
    redirect_uris = [
      "https://${var.serve_app_name}.${var.location}.azurecontainerapps.io/.auth/login/aad/callback"
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

# ---------------------------------------------------------------------------
# Bind the app registration to the Container App (azurerm doesn’t support
# this natively, so we use azapi).
# ---------------------------------------------------------------------------
resource "azapi_resource" "serve_auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2026-01-01"
  name      = "current"
  parent_id = azurerm_container_app.serve.id

  schema_validation_enabled = true

  body = {
    properties = {
      platform = {
        enabled = true
      }

      globalValidation = {
        unauthenticatedClientAction = "Return401"
        excludedPaths = [
          "/health",
          "/ready",
          "/metrics",
          "/version",
        ]
      }

      httpSettings = {
        requireHttps = true
        routes = {
          apiPrefix = "/.auth"
        }
      }

      identityProviders = {
        azureActiveDirectory = {
          enabled           = true
          isAutoProvisioned = false

          login = {
            disableWWWAuthenticate = true
          }

          registration = {
            clientId               = azuread_application.serve.client_id
            openIdIssuer           = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
            # Optional: if you ever need a client secret, uncomment and set the secret on the Container App
            # clientSecretSettingName = "AUTH_CLIENT_SECRET"
          }

          validation = {
            allowedAudiences = [
              azuread_application.serve.client_id,
            ]
          }
        }
      }
    }
  }
}