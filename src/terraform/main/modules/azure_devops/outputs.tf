output "elt_ci_pipeline_id" {
  value = azuredevops_build_definition.elt_ci.id
}

output "elt_ci_variable_group_id" {
  value = azuredevops_variable_group.elt_ci_vars.id
}