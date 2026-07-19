


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

