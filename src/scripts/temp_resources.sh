#!/usr/bin/env bash
# =============================================================================
# temp_resources.sh — Idempotent provisioning for the MLOps ELT stack
#
# Key updates from the older version:
#   - Uses Flex Consumption for Azure Functions instead of Linux Consumption.
#   - Passes a storage account resource ID when the account lives in another RG.
#   - Creates an explicit deployment container for Flex Consumption deployments.
#   - Removes stale identity/deployment assumptions from the old flow.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Logging / errors
# -----------------------------------------------------------------------------
log() {
    printf '[%(%H:%M:%S)T] %s\n' -1 "$*"
}

err() {
    printf '[%(%H:%M:%S)T] ERROR: %s\n' -1 "$*" >&2
}

die() {
    err "$*"
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

# -----------------------------------------------------------------------------
# Prerequisites
# -----------------------------------------------------------------------------
need_cmd az
need_cmd python3

# -----------------------------------------------------------------------------
# Defaults — override via environment if needed
# -----------------------------------------------------------------------------
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null || true)}"
[[ -n "$SUBSCRIPTION_ID" ]] || die "No active Azure subscription. Run 'az login' and 'az account set' first."

STORAGE_SUFFIX="${STORAGE_SUFFIX:-$(echo "$SUBSCRIPTION_ID" | tr -d '-' | cut -c1-8)}"
LOCATION="${LOCATION:-eastus}"

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-mlops-${STORAGE_SUFFIX}}"

AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME:-mlops${STORAGE_SUFFIX}}"
FUNCTION_APP_NAME="${FUNCTION_APP_NAME:-func-elt-${STORAGE_SUFFIX}}"
ADF_NAME="${ADF_NAME:-adf-mlops-${STORAGE_SUFFIX}}"
AIRFLOW_IR_NAME="${AIRFLOW_IR_NAME:-airflow-ir-${STORAGE_SUFFIX}}"

RAW_CONTAINER_NAME="${RAW_CONTAINER_NAME:-raw}"
CLEAN_CONTAINER_NAME="${CLEAN_CONTAINER_NAME:-clean}"
DEPLOYMENT_CONTAINER_NAME="${DEPLOYMENT_CONTAINER_NAME:-func-packages}"

HF_DATASET="${HF_DATASET:-scikit-learn/adult-census-income}"
ROWS="${ROWS:-32500}"
HF_TOKEN="${HF_TOKEN:-}"

FUNCTION_RUNTIME="${FUNCTION_RUNTIME:-python}"
FUNCTION_RUNTIME_VERSION="${FUNCTION_RUNTIME_VERSION:-3.11}"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
resource_group_exists() {
    az group show -n "$RESOURCE_GROUP" >/dev/null 2>&1
}

storage_account_rg() {
    az storage account list \
        --query "[?name=='${AZURE_STORAGE_ACCOUNT_NAME}'].resourceGroup | [0]" \
        -o tsv 2>/dev/null || true
}

storage_account_exists() {
    [[ -n "$(storage_account_rg)" ]]
}

storage_account_id() {
    local rg
    rg="$(storage_account_rg)"
    [[ -n "$rg" ]] || return 1
    az storage account show -g "$rg" -n "$AZURE_STORAGE_ACCOUNT_NAME" --query id -o tsv
}

storage_account_location() {
    local rg
    rg="$(storage_account_rg)"
    [[ -n "$rg" ]] || return 1
    az storage account show -g "$rg" -n "$AZURE_STORAGE_ACCOUNT_NAME" --query primaryLocation -o tsv
}

storage_account_ref_for_function_create() {
    # Azure CLI accepts either a storage account name in the same RG or a
    # resource ID if the storage account lives in a different RG.
    local rg
    rg="$(storage_account_rg)"
    [[ -n "$rg" ]] || return 1

    if [[ "$rg" == "$RESOURCE_GROUP" ]]; then
        printf '%s\n' "$AZURE_STORAGE_ACCOUNT_NAME"
    else
        storage_account_id
    fi
}

container_exists() {
    local container_name="$1"
    az storage container exists \
        --account-name "$AZURE_STORAGE_ACCOUNT_NAME" \
        --name "$container_name" \
        --auth-mode login \
        --query exists -o tsv 2>/dev/null | grep -qi '^true$'
}

blob_exists() {
    local blob_name="$1"
    az storage blob exists \
        --account-name "$AZURE_STORAGE_ACCOUNT_NAME" \
        --container-name "$RAW_CONTAINER_NAME" \
        --name "$blob_name" \
        --auth-mode login \
        --query exists -o tsv 2>/dev/null | grep -qi '^true$'
}

function_app_exists() {
    az functionapp show -g "$RESOURCE_GROUP" -n "$FUNCTION_APP_NAME" >/dev/null 2>&1
}

function_code_deployed() {
    az functionapp function show \
        -g "$RESOURCE_GROUP" \
        -n "$FUNCTION_APP_NAME" \
        --function-name OnRawData >/dev/null 2>&1
}

adf_exists() {
    az datafactory show -g "$RESOURCE_GROUP" -n "$ADF_NAME" >/dev/null 2>&1
}

airflow_ir_exists() {
    az datafactory integration-runtime managed-airflow show \
        --resource-group "$RESOURCE_GROUP" \
        --factory-name "$ADF_NAME" \
        --name "$AIRFLOW_IR_NAME" >/dev/null 2>&1
}

ensure_rg() {
    log "Resource group: $RESOURCE_GROUP"
    if resource_group_exists; then
        log "  already exists"
    else
        az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none
        log "  created"
    fi
}

ensure_storage_account() {
    log "Storage account: $AZURE_STORAGE_ACCOUNT_NAME"
    local storage_rg

    if storage_account_exists; then
        storage_rg="$(storage_account_rg)"
        log "  already exists in resource group '$storage_rg'"

        if [[ "$storage_rg" != "$RESOURCE_GROUP" ]]; then
            log "  ! The account lives in a different resource group than '$RESOURCE_GROUP'."
            log "  ! The function app will reference the storage account by resource ID."
        fi

        local storage_loc
        storage_loc="$(storage_account_location)"
        if [[ "$storage_loc" != "$LOCATION" ]]; then
            log "  ! Storage account location is '$storage_loc' while this stack targets '$LOCATION'."
            log "  ! Azure recommends same-region storage for best performance."
        fi
    else
        az storage account create \
            -g "$RESOURCE_GROUP" \
            -n "$AZURE_STORAGE_ACCOUNT_NAME" \
            -l "$LOCATION" \
            --sku Standard_LRS \
            --kind StorageV2 \
            --allow-blob-public-access false \
            -o none
        log "  created in resource group '$RESOURCE_GROUP'"
    fi
}

ensure_containers() {
    for container in "$RAW_CONTAINER_NAME" "$CLEAN_CONTAINER_NAME" "$DEPLOYMENT_CONTAINER_NAME"; do
        log "Container: $container"
        if container_exists "$container"; then
            log "  already exists"
        else
            az storage container create \
                --account-name "$AZURE_STORAGE_ACCOUNT_NAME" \
                -n "$container" \
                --auth-mode login \
                -o none
            log "  created"
        fi
    done
}

ensure_user_blob_role() {
    log "Role assignment: Storage Blob Data Contributor for signed-in user"

    local scope assignee
    scope="$(storage_account_id)"
    assignee="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
    [[ -n "$assignee" ]] || die "Could not resolve signed-in user object ID."

    if az role assignment list \
        --assignee "$assignee" \
        --scope "$scope" \
        --query "[?roleDefinitionName=='Storage Blob Data Contributor'].id" \
        -o tsv 2>/dev/null | grep -q .; then
        log "  already assigned"
    else
        az role assignment create \
            --role "Storage Blob Data Contributor" \
            --assignee-object-id "$assignee" \
            --assignee-principal-type User \
            --scope "$scope" \
            -o none
        log "  assigned"
    fi
}

ensure_func_tools() {
    if command -v func >/dev/null 2>&1; then
        return 0
    fi

    need_cmd npm
    log "Azure Functions Core Tools not found; installing via npm"
    npm install -g azure-functions-core-tools@4 --unsafe-perm true >/dev/null
}

scaffold_function_project() {
    local func_dir="func_elt"
    log "Scaffolding function project in ./$func_dir"

    mkdir -p "$func_dir"

    cat > "$func_dir/requirements.txt" <<'EOF'
azure-functions
azure-storage-blob
pandas
pyarrow
EOF

    cat > "$func_dir/host.json" <<'EOF'
{
  "version": "2.0",
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.*, 5.0.0)"
  }
}
EOF

    cat > "$func_dir/function_app.py" <<'PYEOF'
import io
import logging
import os

import azure.functions as func
import pandas as pd
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        c.strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_")
        for c in df.columns
    ]
    return df

@app.function_name(name="OnRawData")
@app.blob_trigger(arg_name="blob", path="raw/monthly/{name}.parquet", connection="AzureWebJobsStorage")
def transform_new_data(blob: func.InputStream) -> None:
    logging.info("Processing blob %s (%s bytes)", blob.name, blob.length)

    raw_bytes = io.BytesIO(blob.read())
    df = pd.read_parquet(raw_bytes)
    df = _normalize_columns(df)

    # Treat common missing markers as missing, then remove incomplete rows.
    df = df.replace("?", pd.NA)
    df = df.dropna(subset=["income"]).copy()
    df = df.dropna().copy()

    # Convert target to binary.
    df["income"] = (df["income"].astype(str).str.strip() == ">50K").astype("int8")

    # Derived fields.
    df["net_capital"] = df["capital_gain"] - df["capital_loss"]
    df["age_group"] = pd.cut(
        df["age"],
        bins=[0, 25, 45, 65, 120],
        labels=["young", "adult", "middle_age", "senior"],
        include_lowest=True,
    )

    if df.empty:
        raise ValueError("Transformed dataframe is empty")
    if df["income"].nunique() != 2:
        raise ValueError("Expected both income classes to remain after cleaning")
    if df.isna().any().any():
        raise ValueError("Transformed dataframe still contains missing values")

    output = io.BytesIO()
    df.to_parquet(output, index=False)

    connection_string = os.environ.get("AzureWebJobsStorage")
    if not connection_string:
        raise RuntimeError("AzureWebJobsStorage is not set")

    blob_service = BlobServiceClient.from_connection_string(connection_string)
    clean_container = blob_service.get_container_client("clean")
    clean_container.upload_blob(
        name="adult_census_clean.parquet",
        data=output.getvalue(),
        overwrite=True,
    )

    logging.info("Wrote %d cleaned rows to clean/adult_census_clean.parquet", len(df))
PYEOF
}

ensure_function_app() {
    log "Function App: $FUNCTION_APP_NAME"
    local storage_ref
    storage_ref="$(storage_account_ref_for_function_create)"

    if function_app_exists; then
        log "  already exists"
        return 0
    fi

    # Flex Consumption is the current serverless recommendation.
    # The deployment storage container is required for Flex deploys.
    az functionapp create \
        --resource-group "$RESOURCE_GROUP" \
        --name "$FUNCTION_APP_NAME" \
        --storage-account "$storage_ref" \
        --flexconsumption-location "$LOCATION" \
        --runtime "$FUNCTION_RUNTIME" \
        --runtime-version "$FUNCTION_RUNTIME_VERSION" \
        --functions-version 4 \
        --deployment-storage-name "$AZURE_STORAGE_ACCOUNT_NAME" \
        --deployment-storage-container-name "$DEPLOYMENT_CONTAINER_NAME" \
        --deployment-storage-auth-type StorageAccountConnectionString \
        --deployment-storage-auth-value AzureWebJobsStorage \
        -o none

    log "  created"
}

ensure_function_code() {
    log "Function code deployment"

    if function_code_deployed; then
        log "  code already deployed (function OnRawData exists)"
        return 0
    fi

    ensure_func_tools
    scaffold_function_project

    pushd func_elt >/dev/null
    func azure functionapp publish "$FUNCTION_APP_NAME" --python
    popd >/dev/null

    log "  code deployed"
}

ensure_adf() {
    log "Data Factory: $ADF_NAME"
    if adf_exists; then
        log "  already exists"
    else
        az extension add --name datafactory -o none >/dev/null 2>&1 || true
        az datafactory create -g "$RESOURCE_GROUP" -n "$ADF_NAME" -l "$LOCATION" -o none
        log "  created"
    fi

    log "Managed Airflow IR: $AIRFLOW_IR_NAME"
    if airflow_ir_exists; then
        log "  already exists"
    else
        az datafactory integration-runtime managed-airflow create \
            --resource-group "$RESOURCE_GROUP" \
            --factory-name "$ADF_NAME" \
            --name "$AIRFLOW_IR_NAME" \
            --location "$LOCATION" \
            --compute-configuration '{"nodeSize":"Small","nodeCount":1}' \
            --airflow-environment-configuration '{}' \
            -o none
        log "  created"
    fi
}

ensure_sample_data() {
    log "Sample data upload"

    local sample_blob="monthly/batch_$(date +%Y%m%d_%H%M%S).parquet"
    if blob_exists "$sample_blob"; then
        log "  data blob already exists: $sample_blob"
        return 0
    fi

    need_cmd python3
    python3 - <<PYEOF
import io
import os
from datetime import datetime, timezone

import pandas as pd
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from datasets import load_dataset

storage_account = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
container_name = os.environ["RAW_CONTAINER_NAME"]
dataset_name = os.environ["HF_DATASET"]
rows = int(os.environ["ROWS"])
token = os.environ.get("HF_TOKEN") or None

ds = load_dataset(dataset_name, split=f"train[:{rows}]", token=token)
df = ds.to_pandas()

# Normalize columns to match the function logic.
df.columns = [
    c.strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_")
    for c in df.columns
]

# Emit a parquet file into raw/monthly/ to trigger the blob function.
buf = io.BytesIO()
df.to_parquet(buf, index=False)

timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
blob_name = f"monthly/batch_{timestamp}.parquet"

credential = DefaultAzureCredential()
service = BlobServiceClient(
    account_url=f"https://{storage_account}.blob.core.windows.net",
    credential=credential,
)
container = service.get_container_client(container_name)
container.upload_blob(name=blob_name, data=buf.getvalue(), overwrite=True)

print(blob_name)
PYEOF

    log "  data uploaded"
}

# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------
do_create() {
    log "Starting idempotent creation…"
    log "Subscription ID      : $SUBSCRIPTION_ID"
    log "Unique suffix        : $STORAGE_SUFFIX"
    log "Resource Group       : $RESOURCE_GROUP"
    log "Location             : $LOCATION"
    log "Storage account      : $AZURE_STORAGE_ACCOUNT_NAME"
    log "Function App         : $FUNCTION_APP_NAME"
    log "Data Factory         : $ADF_NAME"

    export AZURE_STORAGE_ACCOUNT_NAME RAW_CONTAINER_NAME CLEAN_CONTAINER_NAME HF_DATASET ROWS HF_TOKEN

    ensure_rg
    ensure_storage_account
    ensure_containers
    ensure_user_blob_role
    ensure_function_app
    ensure_function_code
    ensure_adf
    ensure_sample_data

    log "──────────────────────────────────────────────────────"
    log "ELT infrastructure ready."
    log "  Storage account   : $AZURE_STORAGE_ACCOUNT_NAME"
    log "  Function App      : $FUNCTION_APP_NAME"
    log "  Data Factory      : $ADF_NAME (Airflow enabled)"
    log "  Containers        : $RAW_CONTAINER_NAME / $CLEAN_CONTAINER_NAME"
    log "  Deployment bucket : $DEPLOYMENT_CONTAINER_NAME"
    log "Trigger the pipeline by uploading any new file to raw/monthly/"
}

do_delete() {
    log "Deleting resource group: $RESOURCE_GROUP"
    if resource_group_exists; then
        az group delete -n "$RESOURCE_GROUP" --yes --no-wait
        log "  deletion initiated (async)"
    else
        log "  resource group does not exist, nothing to delete"
    fi
}

usage() {
    cat <<EOF
Usage: $0 --create | --delete
EOF
    exit 1
}

[[ $# -eq 1 ]] || usage

case "$1" in
    --create) do_create ;;
    --delete) do_delete ;;
    *) usage ;;
esac