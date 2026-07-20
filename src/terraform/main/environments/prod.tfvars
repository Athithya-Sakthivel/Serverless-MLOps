location    = "centralindia"
environment = "prod"

storage_container_names = ["raw", "clean", "models", "logs"]

shared_access_key_enabled = true
alert_email_address       = "alerts@example.com"

tags = {
  app     = "serverless-mlops"
  owner   = "athithya"
  env     = "prod"
  project = "serverless-mlops"
}