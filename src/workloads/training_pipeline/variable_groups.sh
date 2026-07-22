



az pipelines variable-group create \
  --name 'elt-ci-vars' \
  --project 'Serverless-MLOps' \
  --variables \
    AZURE_STORAGE_ACCOUNT_NAME="$(cd src/terraform/main && tofu output -raw storage_account_name)" \
    MLFLOW_TRACKING_URI="$(cd src/terraform/main && tofu output -raw mlflow_tracking_uri)" \
    RAW_CONTAINER_NAME='raw' \
    CLEAN_CONTAINER_NAME='clean' \
    CHECKPOINT_CONTAINER_NAME='checkpoints' \
    azureServiceConnection='azdo-oidc-ci'

    