# ------------------------------------------------------------------------------
# Azure identities, Azure DevOps WIF service connections, Azure RBAC, and
# Microsoft Entra directory role assignment.
#
# This file is written to match current provider schemas:
# - azuredevops_serviceendpoint_azurerm exposes workload_identity_federation_issuer
#   and workload_identity_federation_subject as computed outputs.
# - azuread_application_federated_identity_credential uses application_id,
#   audiences, issuer, subject, and optional description.
# - azurerm_role_definition supports permissions blocks with actions, not_actions,
#   data_actions, and not_data_actions, and exports role_definition_resource_id.
# - azuread_directory_role_assignment uses role_id + principal_object_id.
# ------------------------------------------------------------------------------

data "azuread_client_config" "current" {}
data "azurerm_subscription" "current" {}

locals {
  ci_application_display_name = "bootstrap-ci"
  cd_application_display_name = "bootstrap-cd"

  ci_service_connection_name = "azdo-oidc-ci"
  cd_service_connection_name = "azdo-oidc-cd"
}

# ------------------------------------------------------------------------------
# CI identity
# ------------------------------------------------------------------------------

resource "azuread_application" "ci" {
  display_name = local.ci_application_display_name
  owners       = [data.azuread_client_config.current.object_id]
}

resource "azuread_service_principal" "ci" {
  client_id = azuread_application.ci.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

# ------------------------------------------------------------------------------
# CD identity
# ------------------------------------------------------------------------------

resource "azuread_application" "cd" {
  display_name = local.cd_application_display_name
  owners       = [data.azuread_client_config.current.object_id]
}

resource "azuread_service_principal" "cd" {
  client_id = azuread_application.cd.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

# ------------------------------------------------------------------------------
# Azure DevOps service connections using workload identity federation
# ------------------------------------------------------------------------------
# These are intentionally created before the federated credentials that consume
# their computed issuer/subject outputs.
# ------------------------------------------------------------------------------
resource "azuredevops_serviceendpoint_azurerm" "ci" {
  project_id                             = azuredevops_project.this.id
  service_endpoint_name                  = local.ci_service_connection_name
  service_endpoint_authentication_scheme  = "WorkloadIdentityFederation"
  azurerm_spn_tenantid                   = data.azuread_client_config.current.tenant_id
  azurerm_subscription_id                = data.azurerm_subscription.current.subscription_id
  azurerm_subscription_name              = data.azurerm_subscription.current.display_name

  credentials {
    serviceprincipalid = azuread_service_principal.ci.client_id
  }
}

resource "azuredevops_serviceendpoint_azurerm" "cd" {
  project_id                             = azuredevops_project.this.id
  service_endpoint_name                  = local.cd_service_connection_name
  service_endpoint_authentication_scheme  = "WorkloadIdentityFederation"
  azurerm_spn_tenantid                   = data.azuread_client_config.current.tenant_id
  azurerm_subscription_id                = data.azurerm_subscription.current.subscription_id
  azurerm_subscription_name              = data.azurerm_subscription.current.display_name

  credentials {
    serviceprincipalid = azuread_service_principal.cd.client_id
  }
}

# ------------------------------------------------------------------------------
# Federated Identity Credentials
# ------------------------------------------------------------------------------
# Wire the Entra application federated credential to the Azure DevOps outputs.
# Microsoft now uses the Entra issuer for new WIF service connections, and the
# Azure DevOps provider exposes the issuer/subject values for this purpose.
# ------------------------------------------------------------------------------
resource "azuread_application_federated_identity_credential" "ci" {
  application_id = azuread_application.ci.id
  display_name   = local.ci_service_connection_name
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = azuredevops_serviceendpoint_azurerm.ci.workload_identity_federation_issuer
  subject        = azuredevops_serviceendpoint_azurerm.ci.workload_identity_federation_subject
}

resource "azuread_application_federated_identity_credential" "cd" {
  application_id = azuread_application.cd.id
  display_name   = local.cd_service_connection_name
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = azuredevops_serviceendpoint_azurerm.cd.workload_identity_federation_issuer
  subject        = azuredevops_serviceendpoint_azurerm.cd.workload_identity_federation_subject
}

# ------------------------------------------------------------------------------
# Azure RBAC for CI
# ------------------------------------------------------------------------------
resource "azurerm_role_assignment" "ci_reader" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Reader"
  principal_id         = azuread_service_principal.ci.object_id
}

resource "azurerm_role_assignment" "ci_acr_push" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "AcrPush"
  principal_id         = azuread_service_principal.ci.object_id
}

resource "azurerm_role_assignment" "ci_tfstate" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azuread_service_principal.ci.object_id
}

# Custom role that grants only the Container Apps secret listing operations
# required by plan/refresh.
resource "azurerm_role_definition" "ci_containerapp_secrets" {
  name        = "Container App Secret Reader (CI)"
  scope       = data.azurerm_subscription.current.id
  description = "Allows listing secrets on Container Apps and Container Apps Jobs."

  permissions {
    actions = [
      "Microsoft.App/containerApps/listSecrets/action",
      "Microsoft.App/jobs/listSecrets/action",
    ]
    not_actions     = []
    data_actions    = []
    not_data_actions = []
  }

  assignable_scopes = [data.azurerm_subscription.current.id]
}

resource "azurerm_role_assignment" "ci_containerapp_secrets" {
  scope              = data.azurerm_subscription.current.id
  role_definition_id = azurerm_role_definition.ci_containerapp_secrets.role_definition_resource_id
  principal_id       = azuread_service_principal.ci.object_id
}

# ------------------------------------------------------------------------------
# Azure RBAC for CD
# ------------------------------------------------------------------------------
resource "azurerm_role_assignment" "cd_contributor" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.cd.object_id
}

resource "azurerm_role_assignment" "cd_rbac_admin" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azuread_service_principal.cd.object_id
}

resource "azurerm_role_assignment" "cd_acr_pull" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "AcrPull"
  principal_id         = azuread_service_principal.cd.object_id
}

resource "azurerm_role_assignment" "cd_tfstate" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azuread_service_principal.cd.object_id
}

# ------------------------------------------------------------------------------
# Microsoft Entra directory role for CD
# ------------------------------------------------------------------------------
resource "azuread_directory_role" "application_administrator" {
  display_name = "Application Administrator"
}

resource "azuread_directory_role_assignment" "cd_app_admin" {
  role_id             = azuread_directory_role.application_administrator.template_id
  principal_object_id = azuread_service_principal.cd.object_id
}