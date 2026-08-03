#!/usr/bin/env bash
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
#   - bash src/scripts/other_roles.sh && bash src/workloads/training_pipeline/ci_checks_locally.sh
#
# Environment variables (all optional, with sensible defaults):
#   TRAINING_TARGET_INCOME_THRESHOLD   – income threshold for binary target (default: 50000)
#   TRAIN_RANDOM_SEED                  – random seed (default: 42)
#   MLFLOW_EXPERIMENT_NAME             – MLflow experiment name
#   MODEL_NAME                         – registered model name
#   ENABLE_MODEL_REGISTRATION          – "true" / "false"
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
PIPELINE_DIR="$REPO_ROOT/src/workloads/training_pipeline"
TF_DIR="$REPO_ROOT/src/terraform/main"
CI_SAMPLE="$REPO_ROOT/src/ci-samples/data.parquet"

# Prevent non‑CLI credentials from interfering (as seen in previous issues)
unset AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_CLIENT_SECRET AZURE_SUBSCRIPTION_ID

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
export ENABLE_MODEL_REGISTRATION="${ENABLE_MODEL_REGISTRATION:-true}"
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
source .venv_train/bin/activate

# Ensure azureml-mlflow plugin is present (idempotent)
pip install -q azureml-mlflow 2>/dev/null || true

# ── 4. Optional force‑clean existing artefacts ────────────────────────
if $FORCE; then
    echo "Force mode: deleting existing blob and checkpoints for $INPUT_BLOB_NAME"
    python3 -c "
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
cred = AzureCliCredential()
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

# ── 5. Validate the CI sample dataset ─────────────────────────────────
echo "Validating CI sample at ${CI_SAMPLE}..."
python3 -c "
import polars as pl
from pathlib import Path
import sys

ci_path = Path('${CI_SAMPLE}')
if not ci_path.exists():
    sys.exit('CI sample not found at ${CI_SAMPLE}. Run src/scripts/simulate_data_upload.py first.')
df = pl.read_parquet(ci_path)
income = df.get_column('PINCP').cast(pl.Float64)
above = (income >= ${TRAINING_TARGET_INCOME_THRESHOLD}).sum()
below = (income < ${TRAINING_TARGET_INCOME_THRESHOLD}).sum()
if above == 0 or below == 0:
    sys.exit(f'CI sample does not contain both classes. Please regenerate it.')
print(f'CI sample OK: {above} above, {below} below threshold.')
"

# ── 6. Upload the CI sample to Azure ─────────────────────────────────
echo "Checking if blob exists: $INPUT_BLOB_NAME"
python3 -c "
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
cred = AzureCliCredential()
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

# ── 7. Run the pipeline ──────────────────────────────────────────────
cd "$PIPELINE_DIR"
python main.py
cd "$REPO_ROOT"

echo ""
echo "Pipeline finished for blob: $INPUT_BLOB_NAME"

# ── 8. Fetch and display model evaluation report from MLflow ─────────
echo ""
echo "Fetching evaluation report from MLflow..."

python3 - <<PYEOF
import os, json, tempfile
from pathlib import Path
from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts

tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "training_pipeline_local_test")
raw_blob_name = os.environ.get("INPUT_BLOB_NAME", "").replace("/", "_")

client = MlflowClient(tracking_uri=tracking_uri)

# Find the latest completed run for this blob by reading the training checkpoint
# We'll get the run ID from the checkpoint file (stored in blob storage)
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import io

cred = AzureCliCredential()
blob_client = BlobServiceClient(
    account_url=f'https://{os.environ["AZURE_STORAGE_ACCOUNT_NAME"]}.blob.core.windows.net',
    credential=cred,
)
checkpoint_blob = f"training/{raw_blob_name.replace('.parquet','')}.json"
container_client = blob_client.get_container_client("checkpoints")
try:
    blob_data = container_client.download_blob(checkpoint_blob).readall()
    checkpoint = json.loads(blob_data)
    run_id = checkpoint.get("mlflow_run_id")
    if not run_id:
        print("No run_id in checkpoint.")
        raise SystemExit(1)
    print(f"Using run_id from checkpoint: {run_id}")
except Exception as e:
    print(f"Could not read checkpoint: {e}")
    # Fallback: get the latest run in the experiment
    runs = client.search_runs(experiment_ids=[client.get_experiment_by_name(experiment_name).experiment_id],
                              order_by=["start_time DESC"], max_results=1)
    if runs:
        run_id = runs[0].info.run_id
        print(f"Fallback to latest run: {run_id}")
    else:
        print("No runs found in experiment.")
        raise SystemExit(1)

# Download the metrics report artifact
try:
    with tempfile.TemporaryDirectory() as tmp:
        local_path = download_artifacts(
            artifact_uri=f"runs:/{run_id}/reports/metrics.json",
            dst_path=tmp,
            tracking_uri=tracking_uri,
        )
        metrics = json.loads(Path(local_path).read_text())
        print("\n==============================================================")
        print("                 MODEL EVALUATION REPORT")
        print("==============================================================")
        print(f" Run ID          : {run_id}")
        print(f" ONNX SHA256     : {checkpoint.get('onnx_sha256', 'N/A')}")
        print(f" Model version   : {checkpoint.get('model_version', 'N/A')}")
        print(f" Target threshold: {checkpoint.get('target_threshold', 'N/A')}")
        print(f" Seed            : {checkpoint.get('seed', 'N/A')}")
        print()
        if "evaluation" in metrics:
            ev = metrics["evaluation"]
            val = ev.get("validation", {})
            test = ev.get("test", {})
            print(" Validation metrics:")
            for k, v in val.items():
                print(f"   {k:30s} {v}")
            print()
            print(" Test metrics:")
            for k, v in test.items():
                print(f"   {k:30s} {v}")
        else:
            print("No evaluation metrics found in report.")
        print()
        if "feature_importance" in metrics:
            print(" Top 5 features (gain):")
            for rank, feat in enumerate(metrics["feature_importance"][:5], 1):
                print(f"   {rank}. {feat['feature']:30s} {feat['gain']:.4f}")
        else:
            print("No feature importance data.")
        print()
        if "best_iteration" in metrics:
            print(f" Best iteration   : {metrics['best_iteration']}")
        print("==============================================================")
except Exception as e:
    print(f"Could not download or parse evaluation report: {e}")
    print(f"Run ID: {run_id}")
    print("Check the MLflow UI for the run artifacts.")
PYEOF

echo ""
echo "Full pipeline execution completed."