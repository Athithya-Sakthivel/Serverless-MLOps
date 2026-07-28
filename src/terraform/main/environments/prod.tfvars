location    = "centralindia"
environment = "prod"

storage_container_names   = ["raw", "clean", "models", "logs"]
shared_access_key_enabled = true
alert_email_address       = "alerts@example.com"

# ACA — only the values that aren't derived in locals.tf
aca_training_image = "busybox:1.36.1"
aca_serving_image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
aca_serve_port     = 80

# Event Grid — only the overrides for raw container/prefix if different from defaults
event_raw_container_name = "raw"
event_raw_blob_prefix    = "monthly/"

tags = {
  app     = "serverless-mlops"
  owner   = "athithya"
  env     = "prod"
  project = "serverless-mlops"
}