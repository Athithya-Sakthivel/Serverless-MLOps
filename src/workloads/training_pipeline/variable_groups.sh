#!/usr/bin/env bash
set -euo pipefail

cd src/terraform/bootstrap
PROJECT_NAME="azdo-bootstrap-$(az account show --query id -o tsv | tail -c 7)"
echo "$PROJECT_NAME"
cd -

ORG_URL="${TF_VAR_AZDO_ORG_SERVICE_URL:?TF_VAR_AZDO_ORG_SERVICE_URL must be set}"
GROUP_NAME="elt-ci-vars"

# Fetch storage and MLflow values once
STORAGE_ACCOUNT="$(cd src/terraform/main && tofu output -raw storage_account_name)"
MLFLOW_URI="$(cd src/terraform/main && tofu output -raw mlflow_tracking_uri)"

# Check if the variable group already exists
EXISTING_GROUP_ID="$(az pipelines variable-group list \
  --org "$ORG_URL" \
  --project "$PROJECT_NAME" \
  --group-name "$GROUP_NAME" \
  --query "[?name=='$GROUP_NAME'].id" -o tsv 2>/dev/null || true)"

if [[ -n "$EXISTING_GROUP_ID" ]]; then
  echo "Variable group '$GROUP_NAME' already exists (id=$EXISTING_GROUP_ID). Updating variables..."
  
  # Update each variable individually (az CLI doesn't support bulk update)
  for var in \
    "AZURE_STORAGE_ACCOUNT_NAME=$STORAGE_ACCOUNT" \
    "MLFLOW_TRACKING_URI=$MLFLOW_URI" \
    "RAW_CONTAINER_NAME=raw" \
    "CLEAN_CONTAINER_NAME=clean" \
    "CHECKPOINT_CONTAINER_NAME=checkpoints" \
    "azureServiceConnection=azdo-oidc-ci"; do
    
    name="${var%%=*}"
    value="${var#*=}"
    az pipelines variable-group variable update \
      --org "$ORG_URL" \
      --project "$PROJECT_NAME" \
      --group-id "$EXISTING_GROUP_ID" \
      --name "$name" \
      --value "$value" \
      --output none
  done
  
  echo "All variables updated in group '$GROUP_NAME'."
else
  echo "Creating variable group '$GROUP_NAME'..."
  az pipelines variable-group create \
    --name "$GROUP_NAME" \
    --authorize true \
    --org "$ORG_URL" \
    --project "$PROJECT_NAME" \
    --variables \
      AZURE_STORAGE_ACCOUNT_NAME="$STORAGE_ACCOUNT" \
      MLFLOW_TRACKING_URI="$MLFLOW_URI" \
      RAW_CONTAINER_NAME='raw' \
      CLEAN_CONTAINER_NAME='clean' \
      CHECKPOINT_CONTAINER_NAME='checkpoints' \
      azureServiceConnection='azdo-oidc-ci' \
    --output json
fi

echo "Variable group '$GROUP_NAME' is ready."