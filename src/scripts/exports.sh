


export AZURE_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
export SUBSCRIPTION_SUFFIX="${AZURE_SUBSCRIPTION_ID: -6}"

export STATE_RG="rg-sm-state-${SUBSCRIPTION_SUFFIX}"
export STATE_STORAGE_ACC_NAME="smstatesa${SUBSCRIPTION_SUFFIX}"
export STATE_TF_CONTAINER_NAME="tfbackend"

bash src/terraform/bootstrap/bootstrap.sh


export STATE_AZ_FUNC_CONTAINER_NAME="azfuncstate"


export ARTIFACTS_RG="rg-sm-artifacts-${SUBSCRIPTION_SUFFIX}"
export ARTIFACTS_STORAGE_ACC_NAME="sm${SUBSCRIPTION_SUFFIX}artifactssa"
export RAW_CONTAINER_NAME="raw"
export RAW_BLOB_TRIGGER_PATH="raw/monthly/{name}"
export CLEAN_CONTAINER_NAME="clean"
export TRAINING_QUEUE_NAME="training-trigger"
export TRIGGER_ML_TRAINING="false"



export ARM_USE_OIDC=false
export ARM_USE_AZUREAD=false
export ARM_TENANT_ID=$(az account show --query tenantId -o tsv)
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
unset ARM_OIDC_TOKEN ARM_CLIENT_ID ARM_ACCESS_KEY
export TF_BACKEND_AUTH_MODE=cli

USER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)" && \
az role assignment create \
  --assignee-object-id "$USER_OBJECT_ID" \
  --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/b1e221f4-74ef-4e62-9bca-fb70aef41930/resourceGroups/rg-sm-artifacts-stg/providers/Microsoft.Storage/storageAccounts/smstgartifactsf41930"


export ARTIFACTS_STORAGE_ACC_NAME="smstgartifactsf41930"
python3 src/scripts/simulate_data_upload.py
