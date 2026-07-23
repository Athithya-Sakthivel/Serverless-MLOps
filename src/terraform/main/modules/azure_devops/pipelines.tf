# ------------------------------------------------------------------------------
# Data lookups – the project and service connections already exist (bootstrap).
# ------------------------------------------------------------------------------
data "azuredevops_project" "main" {
  name = var.project_name
}

data "azuredevops_serviceendpoint_github" "main" {
  project_id = data.azuredevops_project.main.id
  name       = var.github_service_connection_name
}

# ------------------------------------------------------------------------------
# ELT CI pipeline – triggered on ELT code changes.
# ------------------------------------------------------------------------------
resource "azuredevops_build_definition" "elt_ci" {
  project_id = data.azuredevops_project.main.id
  name       = "${var.project_name}-elt-ci"
  path       = "\\"

  ci_trigger {
    use_yaml = true
  }

  repository {
    repo_type             = "GitHub"
    repo_id               = "${var.github_owner}/${var.github_repo}"
    branch_name           = var.branch
    yml_path              = "azure-pipelines/ci/ci-elt.yaml"
    service_connection_id = data.azuredevops_serviceendpoint_github.main.id
    report_build_status   = true
  }

  timeouts {
    create = "30m"
    read   = "5m"
    update = "30m"
    delete = "30m"
  }
}

# ------------------------------------------------------------------------------
# Authorisations – allow ELT pipeline to use the GitHub endpoint.
# ------------------------------------------------------------------------------
resource "azuredevops_pipeline_authorization" "elt_ci_github" {
  project_id  = data.azuredevops_project.main.id
  resource_id = data.azuredevops_serviceendpoint_github.main.id
  type        = "endpoint"
  pipeline_id = azuredevops_build_definition.elt_ci.id
}