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

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_request_failures" {
  name                = "sq-${var.environment}-app-request-failures"
  resource_group_name = var.resource_group_name
  location            = var.location

  scopes                = [azurerm_log_analytics_workspace.this.id]
  description           = "Triggers when failed inference requests appear in the last 15 minutes."
  severity              = 2
  enabled               = true
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  criteria {
    query                   = <<-KQL
      AppRequests
      | where TimeGenerated > ago(15m)
      | where Success == false
      | summarize Count = count()
    KQL
    operator                = "GreaterThan"
    threshold               = 0
    time_aggregation_method = "Count"
  }

  tags = var.tags
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_slow_requests" {
  name                = "sq-${var.environment}-app-slow-requests"
  resource_group_name = var.resource_group_name
  location            = var.location

  scopes                = [azurerm_log_analytics_workspace.this.id]
  description           = "Triggers when P95 inference latency crosses 200 ms in the last 15 minutes."
  severity              = 2
  enabled               = true
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  criteria {
    query                   = <<-KQL
      AppRequests
      | where TimeGenerated > ago(15m)
      | where Success == true
      | summarize P95DurationMs = percentile(DurationMs, 95)
    KQL
    operator                = "GreaterThan"
    threshold               = 200
    metric_measure_column   = "P95DurationMs" # explicitly names the measured column
    time_aggregation_method = "Maximum"
  }

  tags = var.tags
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_exceptions" {
  name                = "sq-${var.environment}-app-exceptions"
  resource_group_name = var.resource_group_name
  location            = var.location

  scopes                = [azurerm_log_analytics_workspace.this.id]
  description           = "Triggers when app exceptions appear in the last 15 minutes."
  severity              = 2
  enabled               = true
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  criteria {
    query                   = <<-KQL
      AppExceptions
      | where TimeGenerated > ago(15m)
      | summarize Count = count()
    KQL
    operator                = "GreaterThan"
    threshold               = 0
    time_aggregation_method = "Count"
  }

  tags = var.tags
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_validation_failures" {
  name                = "sq-${var.environment}-app-validation-failures"
  resource_group_name = var.resource_group_name
  location            = var.location

  scopes                = [azurerm_log_analytics_workspace.this.id]
  description           = "Triggers when validation-failure custom metrics are emitted in the last 15 minutes."
  severity              = 3
  enabled               = true
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  criteria {
    query                   = <<-KQL
      AppMetrics
      | where TimeGenerated > ago(15m)
      | where Name == "validation_failures"
      | summarize ValidationFailures = sum(todouble(Value))
    KQL
    operator                = "GreaterThan"
    threshold               = 0
    metric_measure_column   = "ValidationFailures"
    time_aggregation_method = "Total"
  }

  tags = var.tags
}