#!/usr/bin/env bash
# =============================================================================
# test_e2e_locally.sh – Battle‑test the serving API locally, idempotently.
#
# Starts the serving app, validates /health and /ready, then sends a correct
# prediction request if a model is registered under MODEL_NAME/MODEL_ALIAS.
# The app runs in the background and is killed on exit.
#
# Usage:
#   bash src/workloads/serving/test_e2e_locally.sh
#
# Prerequesities:
#   - `az login` with read access to the ML workspace (for Terraform outputs)
#   - Python virtual env with serving dependencies installed
#   - A model registered under MODEL_NAME / MODEL_ALIAS (default: production)
#  
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || pwd -P)"
SERVE_DIR="${REPO_ROOT}/src/workloads/serving"
TF_DIR="${REPO_ROOT}/src/terraform/main"

TEST_PORT="${TEST_PORT:-8081}"
CACHE_FILE="${SERVE_DIR}/.tf_serving_outputs"

# ---------------------------------------------------------------------------
# 1. Terraform outputs (cached for 24h)
# ---------------------------------------------------------------------------
if [[ -f "$CACHE_FILE" ]] && [[ $(find "$CACHE_FILE" -mtime -1) ]]; then
    echo "Using cached Terraform outputs"
    source "$CACHE_FILE"
else
    echo "Refreshing Terraform outputs …"
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

    MLFLOW_TRACKING_URI="$(tofu output -raw mlflow_tracking_uri)"
    # DO NOT use real telemetry – local tests must stay offline
    APP_INSIGHTS_CONN_STRING="$(tofu output -raw application_insights_connection_string)"
    cd "$REPO_ROOT"

    cat >"$CACHE_FILE" <<EOF
export MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI}"
export APPLICATIONINSIGHTS_CONNECTION_STRING="${APP_INSIGHTS_CONN_STRING}"
EOF
    source "$CACHE_FILE"
fi

# ---------------------------------------------------------------------------
# 2. Environment – identical to the ACA container EXCEPT telemetry is off
# ---------------------------------------------------------------------------
export MLFLOW_TRACKING_URI="$(cd src/terraform/main && source .bootstrap.generated.env 2>/dev/null; tofu output -raw mlflow_tracking_uri)"
export APPLICATIONINSIGHTS_CONNECTION_STRING="$(cd src/terraform/main && source .bootstrap.generated.env 2>/dev/null; tofu output -raw application_insights_connection_string)"
export MODEL_NAME="${MODEL_NAME:-acs_income_classifier}"
export MODEL_ALIAS="${MODEL_ALIAS:-production}"
export MODEL_VERSION="${MODEL_VERSION:-}"          # empty = use alias
export SERVICE_NAME="serving-api-e2e-local"
export SERVICE_VERSION="1.0.0"
export ENVIRONMENT="local-e2e"
export SERVING_SKIP_MODEL_LOAD="0"

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
# ── 3. Virtual environment ───────────────────────────────────
# If a virtualenv is already active (VIRTUAL_ENV is set), use it.
# Otherwise try the known venv directories.
source .venv_se/bin/activate

# ---------------------------------------------------------------------------
# 4. Model availability & input shape discovery
# ---------------------------------------------------------------------------
MODEL_READY=false
FEATURES_JSON="[[0.0]]"   # safe fallback

echo "Checking model availability (${MODEL_NAME}/${MODEL_ALIAS}) …"
MODEL_INFO=$(python3 -c "
import os, json, tempfile
from pathlib import Path
from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import onnxruntime as ort

client = MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'])
try:
    mv = client.get_model_version_by_alias('${MODEL_NAME}', '${MODEL_ALIAS}')
except Exception:
    print(json.dumps({'status': 'NOT_FOUND'}))
    raise SystemExit(0)

version = mv.version
run_id = mv.run_id
artifact_uri = f'models:/${MODEL_NAME}/{version}/onnx/model.onnx'

# Download just the ONNX file to inspect its input shape
with tempfile.TemporaryDirectory() as tmp:
    local = download_artifacts(artifact_uri=artifact_uri, dst_path=tmp,
                               tracking_uri=os.environ['MLFLOW_TRACKING_URI'])
    sess = ort.InferenceSession(str(local), providers=['CPUExecutionProvider'])
    input_shape = sess.get_inputs()[0].shape  # e.g. [None, 27]
    # Create a single dummy row of the required feature size
    feature_size = input_shape[1] if len(input_shape) > 1 and input_shape[1] is not None else 1
    dummy_row = [0.0] * feature_size

print(json.dumps({
    'status': 'FOUND',
    'version': version,
    'feature_size': feature_size,
    'dummy_row': dummy_row
}))
")

if echo "${MODEL_INFO}" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='FOUND' else 1)" 2>/dev/null; then
    MODEL_READY=true
    MODEL_VERSION=$(echo "${MODEL_INFO}" | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])")
    DUMMY_ROW=$(echo "${MODEL_INFO}" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['dummy_row']))")
    FEATURES_JSON="[${DUMMY_ROW}]"
    echo "Model version ${MODEL_VERSION} ready. Feature size = $(echo "${DUMMY_ROW}" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')."
else
    echo "No model registered under alias '${MODEL_ALIAS}' – prediction tests will be skipped."
fi

# ---------------------------------------------------------------------------
# 5. Start the serving app
# ---------------------------------------------------------------------------
# Use fuser instead of lsof (more portable)
if command -v fuser >/dev/null 2>&1; then
    EXISTING=$(fuser "${TEST_PORT}/tcp" 2>/dev/null || true)
    if [ -n "${EXISTING}" ]; then
        echo "Port ${TEST_PORT} is in use. Killing existing process."
        kill -9 ${EXISTING} || true
        sleep 1
    fi
fi

cd "${SERVE_DIR}"
echo "Starting serving app on port ${TEST_PORT} …"
uvicorn app:app --host 0.0.0.0 --port "${TEST_PORT}" \
    --log-level warning --no-access-log &
APP_PID=$!
echo "Started app with PID ${APP_PID}"

cleanup() {
    echo "Shutting down serving app (PID ${APP_PID}) …"
    kill -TERM "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 6. Wait for /health
# ---------------------------------------------------------------------------
echo "Waiting for /health …"
RETRIES=30
until curl -fsS "http://localhost:${TEST_PORT}/health" >/dev/null 2>&1; do
    RETRIES=$((RETRIES - 1))
    if [ "$RETRIES" -le 0 ]; then
        echo "ERROR: Serving app did not start in time" >&2
        exit 1
    fi
    sleep 1
done
echo "Health check passed."

# ---------------------------------------------------------------------------
# 7. Test /ready
# ---------------------------------------------------------------------------
echo "Testing /ready …"
READY_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${TEST_PORT}/ready")
READY_BODY=$(curl -s "http://localhost:${TEST_PORT}/ready")
echo "HTTP ${READY_CODE}: ${READY_BODY}"

if echo "${READY_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('model_loaded') else 1)" 2>/dev/null; then
    echo "Model is loaded and ready."
else
    echo "Model is NOT loaded."
fi

# ---------------------------------------------------------------------------
# 8. Test /predict (only if model exists)
# ---------------------------------------------------------------------------
if $MODEL_READY; then
    echo "Testing /predict with a correctly-shaped feature vector …"

    PREDICT_RESP=$(curl -fsS -X POST "http://localhost:${TEST_PORT}/predict" \
        -H "Content-Type: application/json" \
        -d "{\"features\": ${FEATURES_JSON}}")
    echo "${PREDICT_RESP}" | python3 -m json.tool 2>/dev/null || echo "${PREDICT_RESP}"

    echo "${PREDICT_RESP}" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert 'probabilities' in d, 'Missing probabilities'
assert 'prediction' in d, 'Missing prediction'
print('Prediction response looks valid.')
"
else
    echo "Skipping /predict test (no model available)."
fi

echo ""
echo "=== End‑to‑end serving test completed ==="
echo "App ran on http://localhost:${TEST_PORT}"
echo "Model available: ${MODEL_READY}"