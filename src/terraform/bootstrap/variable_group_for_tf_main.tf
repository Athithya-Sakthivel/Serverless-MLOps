# ------------------------------------------------------------------------------
# Variable group for terraform-ci / terraform-cd pipelines.
# Holds non‑derivable Terraform variables that are the same across environments
# (overridden per environment in .tfvars if needed).
# ------------------------------------------------------------------------------

resource "azuredevops_variable_group" "terraform_vars" {
  project_id   = azuredevops_project.this.id
  name         = "terraform-vars"
  description  = "Common Terraform variables for CI/CD pipelines"
  allow_access = true

  variable {
    name  = "TF_VAR_location"
    value = var.location
  }

  variable {
    name  = "TF_VAR_alert_email_address"
    value = var.alert_email_address
  }
}