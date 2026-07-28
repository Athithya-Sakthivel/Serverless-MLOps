#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_e2e_locally.sh – Full local end‑to‑end test of the training pipeline.
#
# Runs ELT + training using real Azure resources (storage, MLflow).
# Designed to be *identical* to the ACA job entrypoint (`python main.py`).
#
# Usage:
#   bash src/workloads/training_pipeline/test_e2e_locally.sh                # idempotent – skips already done work
#   bash src/workloads/training_pipeline/test_e2e_locally.sh --force        # delete existing artefacts, fresh start
#   bash src/workloads/training_pipeline/test_e2e_locally.sh --new          # create a timestamped blob (parallel test)
#
# Requirements:
#   - `az login` with Storage Blob Data Contributor role on the storage account
#   - Python virtual env with dependencies (see requirements-ci.txt)
#   - Terraform/OpenTofu initialised in src/terraform/main
#
# Environment variables (all optional, with sensible defaults):
#   TRAINING_TARGET_INCOME_THRESHOLD   – income threshold for binary target (default: 50000)
#   TRAIN_RANDOM_SEED                  – random seed (default: 42)
#   MLFLOW_EXPERIMENT_NAME             – MLflow experiment name
#   MODEL_NAME                         – registered model name
#   ENABLE_MODEL_REGISTRATION          – "true" / "false"
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PIPELINE_DIR="$REPO_ROOT/src/workloads/training_pipeline"
TF_DIR="$REPO_ROOT/src/terraform/main"
CI_SAMPLE="$REPO_ROOT/src/ci-samples/data.parquet"

FORCE=false
NEW_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --new)   NEW_RUN=true; shift ;;
        *)       echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── 1. Terraform outputs (cached for 24h) ────────────────────────────
# We only need to call `tofu output` once per day; the values rarely change.
CACHE_FILE="$PIPELINE_DIR/.tf_outputs"
if [[ -f "$CACHE_FILE" ]] && [[ $(find "$CACHE_FILE" -mtime -1) ]]; then
    echo "Using cached Terraform outputs"
    source "$CACHE_FILE"
else
    echo "Refreshing Terraform outputs…"
    cd "$TF_DIR"
    source .bootstrap.generated.env 2>/dev/null || true
    unset ARM_CLIENT_ID ARM_TENANT_ID ARM_SUBSCRIPTION_ID ARM_ACCESS_KEY ARM_OIDC_TOKEN ARM_USE_OIDC ARM_USE_CLI || true
    export ARM_USE_CLI="true"

    BACKEND_CONFIG="$(mktemp)"
    cat >"$BACKEND_CONFIG" <<EOF
resource_group_name  = "$TF_BACKEND_RESOURCE_GROUP"
storage_account_name = "$TF_BACKEND_STORAGE_ACCOUNT"
container_name       = "$TF_BACKEND_CONTAINER"
key                  = "main/terraform/staging.tfstate"
EOF
    tofu init -reconfigure -input=false -upgrade -backend-config="$BACKEND_CONFIG" >/dev/null
    rm -f "$BACKEND_CONFIG"

    AZURE_STORAGE_ACCOUNT_NAME="$(tofu output -raw storage_account_name)"
    MLFLOW_TRACKING_URI="$(tofu output -raw mlflow_tracking_uri)"
    cd "$REPO_ROOT"

    cat >"$CACHE_FILE" <<EOF
export AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME}"
export MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI}"
EOF
    source "$CACHE_FILE"
fi

# ── 2. Environment – identical to ACA job definition ──────────────────
export RAW_CONTAINER_NAME="raw"
export CLEAN_CONTAINER_NAME="clean"
export CHECKPOINT_CONTAINER_NAME="checkpoints"
export MLFLOW_EXPERIMENT_NAME="${MLFLOW_EXPERIMENT_NAME:-training_pipeline_local_test}"
export MODEL_NAME="${MODEL_NAME:-acs_income_classifier}"
export ENABLE_MODEL_REGISTRATION="${ENABLE_MODEL_REGISTRATION:-false}"
export TRAINING_TARGET_INCOME_THRESHOLD="${TRAINING_TARGET_INCOME_THRESHOLD:-50000}"
export TRAIN_RANDOM_SEED="${TRAIN_RANDOM_SEED:-42}"

# Fixed blob name for idempotent reruns; --new creates a timestamped blob
if $NEW_RUN; then
    TEST_ID="local-$(date +%Y%m%d-%H%M%S)"
    INPUT_BLOB_NAME="raw/monthly/${TEST_ID}.parquet"
else
    INPUT_BLOB_NAME="raw/monthly/local-test.parquet"
fi
export INPUT_BLOB_NAME

# ── 3. Activate virtual environment ───────────────────────────────────
if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
    source "$REPO_ROOT/.venv/bin/activate"
elif [ -f "$REPO_ROOT/.venv1/bin/activate" ]; then
    source "$REPO_ROOT/.venv1/bin/activate"
else
    echo "No virtual environment found" >&2
    exit 1
fi

# Ensure azureml-mlflow plugin is present (idempotent)
pip install -q azureml-mlflow 2>/dev/null || true

# ── 4. Optional force‑clean existing artefacts ────────────────────────
if $FORCE; then
    echo "Force mode: deleting existing blob and checkpoints for $INPUT_BLOB_NAME"
    python3 -c "
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
client = BlobServiceClient(
    account_url='https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net',
    credential=cred,
)
# raw blob
try:
    client.get_container_client('${RAW_CONTAINER_NAME}').delete_blob('${INPUT_BLOB_NAME}')
    print('Deleted raw blob')
except Exception:
    pass
# ELT checkpoint
ck = client.get_container_client('${CHECKPOINT_CONTAINER_NAME}')
try:
    ck.delete_blob('elt/${INPUT_BLOB_NAME}.json')
    print('Deleted ELT checkpoint')
except Exception:
    pass
# training checkpoint
try:
    train_ck = 'training/' + '${INPUT_BLOB_NAME}'.replace('.parquet', '.json').replace('raw/', '')
    ck.delete_blob(train_ck)
    print('Deleted training checkpoint')
except Exception:
    pass
# clean blob
try:
    clean = 'clean/' + '${INPUT_BLOB_NAME}'.replace('raw/', '')
    client.get_container_client('${CLEAN_CONTAINER_NAME}').delete_blob(clean)
    print('Deleted clean blob')
except Exception:
    pass
"
fi

# ── 5. Prepare a test dataset that guarantees both classes ───────────
# If the existing CI sample does not contain both classes for the current
# threshold, we generate a synthetic one.  This ensures the pipeline always
# has a valid binary target to train on.
echo "Preparing test dataset with threshold=${TRAINING_TARGET_INCOME_THRESHOLD}..."

python3 -c "
import os, sys
import polars as pl
import numpy as np
from pathlib import Path

ci_path = Path('${CI_SAMPLE}')
threshold = int(os.environ['TRAINING_TARGET_INCOME_THRESHOLD'])

# Check if the real CI sample already has both classes – if so, keep it.
if ci_path.exists():
    df = pl.read_parquet(ci_path)
    income = df.get_column('PINCP').cast(pl.Float64)
    above = (income >= threshold).sum()
    below = (income < threshold).sum()
    if above > 0 and below > 0:
        print(f'CI sample OK: {above} above, {below} below threshold – no replacement needed.')
        sys.exit(0)

# Generate a balanced synthetic dataset (same schema as ACS)
n = 2000  # small enough for fast training, large enough for a meaningful split
rng = np.random.default_rng(42)

# Numeric columns
agep = rng.integers(18, 80, n).astype(float)
cow = rng.integers(1, 8, n).astype(float)
schl = rng.integers(1, 24, n).astype(float)
mar = rng.integers(1, 5, n).astype(float)
occp = rng.integers(100, 5000, n).astype(float)
pobp = rng.integers(1, 100, n).astype(float)
relp = rng.integers(0, 10, n).astype(float)
wkhp = rng.integers(0, 60, n).astype(float)
sex = rng.integers(1, 2, n).astype(float)
rac1p = rng.integers(1, 9, n).astype(float)
year = rng.integers(2019, 2025, n).astype(float)
state = rng.choice(['NY','CA','TX','FL','IL'], n)

# Income: half below threshold, half above
half = n // 2
income = np.empty(n, dtype=float)
income[:half] = rng.integers(max(1000, threshold//2), threshold-1, half).astype(float)
income[half:] = rng.integers(threshold, threshold+100000, n-half).astype(float)
rng.shuffle(income)

df = pl.DataFrame({
    'AGEP': agep, 'COW': cow, 'SCHL': schl, 'MAR': mar, 'OCCP': occp,
    'POBP': pobp, 'RELP': relp, 'WKHP': wkhp, 'SEX': sex, 'RAC1P': rac1p,
    'STATE': state, 'YEAR': year, 'PINCP': income
})

df.write_parquet(ci_path)
print(f'Replaced CI sample with synthetic data ({n} rows, ~{half} above threshold).')
"

# ── 6. Upload the (possibly synthetic) sample to Azure ───────────────
echo "Checking if blob exists: $INPUT_BLOB_NAME"
python3 -c "
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
client = BlobServiceClient(
    account_url='https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net',
    credential=cred,
)
cc = client.get_container_client('${RAW_CONTAINER_NAME}')
if cc.get_blob_client('${INPUT_BLOB_NAME}').exists():
    print('Blob already exists – skipping upload.')
else:
    with open('${CI_SAMPLE}', 'rb') as f:
        cc.upload_blob(name='${INPUT_BLOB_NAME}', data=f, overwrite=True)
    print('Upload complete.')
"

# ── 7. Run the pipeline (identical to ACA) ────────────────────────────
cd "$PIPELINE_DIR"
python main.py
cd "$REPO_ROOT"

echo ""
echo "Pipeline finished for blob: $INPUT_BLOB_NAME"