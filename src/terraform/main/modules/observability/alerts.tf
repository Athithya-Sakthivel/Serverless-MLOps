resource "azurerm_monitor_action_group" "this" {
  name                = var.action_group_name
  resource_group_name = var.resource_group_name
  location            = "Global"
  short_name          = var.action_group_short_name
  enabled             = true

  email_receiver {
    name                    = "primary"
    email_address           = var.alert_email_address
    use_common_alert_schema = true
  }

  tags = var.tags
}

# Alert rules removed because Azure for Students lacks
# Microsoft.Insights/scheduledQueryRules/write. To enable them,
# set up a separate script with Monitoring Contributor role.