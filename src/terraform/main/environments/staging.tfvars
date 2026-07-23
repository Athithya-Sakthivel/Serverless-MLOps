alert_email_address = "athithya651@gmail.com"
tags = {
  app     = "serverless-mlops"
  owner   = "athithya"
  env     = "staging"
  project = "serverless-mlops"
}


location    = "southindia" # eventbridge down in centralindia
environment = "staging"

storage_container_names = ["raw", "clean", "models", "logs"]

# required to avoid premature validations
shared_access_key_enabled = true

# ACA — only the values that aren't derived in locals.tf
aca_training_image = "busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
aca_serving_image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld@sha256:e9b3e7c34664c7cffd7144864b0e4eec369bfde80068f9095dc63b37058bec48"
aca_serve_port     = 80

# Event Grid — only the overrides for raw container/prefix if different from defaults
event_raw_container_name = "raw"
event_raw_blob_prefix    = "monthly/"

