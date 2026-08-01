#!/usr/bin/env bash
# ==============================================================================
# Canary deployment for Azure Container Apps (serving API)
#
# Usage:
#   canary-deploy.sh \
#     --app-name <container-app-name> \
#     --resource-group <rg> \
#     --image <new-image> | --model-version <version> \
#     [--canary-steps 10,50] \
#     [--k6-script tests/load/k6-script.js] \
#     [--health-path /health] \
#     [--ready-path /ready]
#
# Behaviour:
#   1. Creates a new revision at 0 % traffic with either a new image or an
#      updated environment variable (MODEL_VERSION).
#   2. Waits for the revision to reach "Running" state and probes /ready on
#      its private FQDN.
#   3. Runs k6 load tests against the private FQDN (0 % traffic) to validate
#      error rate, latency, and throughput.
#   4. Gradually shifts traffic according to --canary-steps (e.g. 10,50,100).
#      At each step, a short k6 validation run is executed against the public
#      FQDN.
#   5. If any step fails, traffic is instantly rolled back to the previous
#      stable revision.
#   6. On full success, the new revision becomes the stable one and the
#      previous revision is kept for manual rollback.
#
# Dependencies: az (Azure CLI), curl, k6 (downloaded automatically if missing)

set -uo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Environment variables with defaults
# ---------------------------------------------------------------------------
CANARY_STEPS="${CANARY_STEPS:-10,50}"
K6_SCRIPT="${K6_SCRIPT:-tests/load/k6-script.js}"
K6_BASELINE_DURATION="${K6_BASELINE_DURATION:-1m}"
K6_CANARY_DURATION="${K6_CANARY_DURATION:-30s}"
HEALTH_PATH="${HEALTH_PATH:-/health}"
READY_PATH="${READY_PATH:-/ready}"
MAX_REVISIONS_TO_KEEP="${MAX_REVISIONS_TO_KEEP:-5}"
MAX_P95_LATENCY_MS="${MAX_P95_LATENCY_MS:-200}"
MAX_ERROR_RATE="${MAX_ERROR_RATE:-0.01}"
ENABLE_ROLLBACK_ALERT="${ENABLE_ROLLBACK_ALERT:-false}"
ROLLBACK_WEBHOOK="${ROLLBACK_WEBHOOK:-}"
MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-}"

APP_NAME=""
RESOURCE_GROUP=""
IMAGE=""
MODEL_VERSION=""
BUILD_ID="${BUILD_BUILDID:-$(date +%s)}"

# ---------------------------------------------------------------------------
# Argument parsing – only the essentials that change every run
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $0 --app-name <name> --resource-group <rg> (--image <img> | --model-version <ver>)"
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-name)        APP_NAME="$2"; shift 2 ;;
        --resource-group)  RESOURCE_GROUP="$2"; shift 2 ;;
        --image)           IMAGE="$2"; shift 2 ;;
        --model-version)   MODEL_VERSION="$2"; shift 2 ;;
        *)                 usage ;;
    esac
done

[[ -z "$APP_NAME" || -z "$RESOURCE_GROUP" ]] && usage
[[ -z "$IMAGE" && -z "$MODEL_VERSION" ]] && { echo "Either --image or --model-version required"; exit 2; }
[[ -n "$IMAGE" && -n "$MODEL_VERSION" ]] && { echo "Specify either --image or --model-version, not both"; exit 2; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()   { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
err()   { log "ERROR: $*" >&2; }

# ---- k6 installation ------------------------------------------------------
install_k6() {
    command -v k6 >/dev/null 2>&1 && return 0
    log "Installing k6..."
    curl -sL https://github.com/grafana/k6/releases/download/v0.47.0/k6-v0.47.0-linux-amd64.tar.gz | tar xz
    sudo mv k6-v0.47.0-linux-amd64/k6 /usr/local/bin/k6
    command -v k6 >/dev/null || { err "k6 installation failed"; return 1; }
}

# ---- Azure CLI retry -------------------------------------------------------
retry_az() {
    local max=3 delay=15 i
    for ((i=1; i<=max; i++)); do
        if az "$@"; then return 0; fi
        log "AZ CLI attempt $i/$max failed, retrying in ${delay}s..."
        sleep $delay
    done
    return 1
}

# ---- revision helpers ------------------------------------------------------
get_revision_fqdn() {
    az containerapp revision show \
        --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision "$1" \
        --query "properties.fqdn" -o tsv
}

wait_for_ready() {
    local fqdn="$1" max=60 attempt=0
    log "Waiting for revision to become ready (private FQDN: $fqdn)..."
    until curl -fsS "https://${fqdn}${READY_PATH}" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max ]]; then return 1; fi
        sleep 5
    done
    log "Revision is ready"
    return 0
}

# ---- k6 execution & evaluation ---------------------------------------------
run_k6() {
    local fqdn="$1" label="$2" duration="$3"
    log "Running k6 test for '$label' (target: $fqdn, duration: $duration)..."
    k6 run "$K6_SCRIPT" -e APP_FQDN="$fqdn" \
        --duration "$duration" \
        --summary-export="k6-summary-${label}.json" \
        --quiet 2>&1 | tail -5
}

evaluate_k6() {
    local label="$1" threshold_p95="$2" threshold_error="$3"
    python3 -c "
import json, sys
with open('k6-summary-${label}.json') as f:
    data = json.load(f)
metrics = data.get('metrics', {})
req_failed = metrics.get('http_req_failed', {}).get('value', 1)
p95 = metrics.get('http_req_duration', {}).get('p(95)', 9999)
if req_failed > $threshold_error:
    print(f'FAIL: error rate {req_failed:.4f} > {threshold_error}')
    sys.exit(1)
if p95 > $threshold_p95:
    print(f'FAIL: P95 latency {p95:.1f}ms > {threshold_p95}')
    sys.exit(1)
print('k6 thresholds passed')
"
}

# ---- rollback (triggered by trap) ------------------------------------------
CANARY_SUCCEEDED=0

rollback() {
    if [[ $CANARY_SUCCEEDED -eq 1 ]]; then return 0; fi
    log "=============================================="
    log "ROLLBACK INITIATED – returning traffic to ${STABLE_REVISION}"
    log "=============================================="

    # Primary rollback: set 100% to stable revision
    if retry_az containerapp ingress traffic set \
        --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision-weight "${STABLE_REVISION}=100" --output none; then
        log "Rollback succeeded"
    else
        # Fallback: point all traffic to latest revision
        log "Primary rollback failed, falling back to latest revision"
        retry_az containerapp ingress traffic set \
            --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
            --traffic-weight latest=100 --output none || true
        # Last resort: delete the broken revision
        if [[ -n "${NEW_REVISION:-}" ]]; then
            log "Deleting failed revision $NEW_REVISION"
            az containerapp revision delete \
                --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
                --revision "$NEW_REVISION" --yes --output none || true
        fi
    fi

    # Alert
    if [[ "${ENABLE_ROLLBACK_ALERT}" == "true" && -n "${ROLLBACK_WEBHOOK:-}" ]]; then
        curl -sS -X POST "$ROLLBACK_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"Canary rollback for *$APP_NAME* in $RESOURCE_GROUP – traffic restored to \`$STABLE_REVISION\`\"}" \
            || true
    fi

    exit 1
}

trap rollback ERR

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
install_k6 || exit 1
log "Canary deployment for $APP_NAME ($RESOURCE_GROUP)"

# ---- 0. Pre-flight checks --------------------------------------------------
# Determine stable revision
STABLE_REVISION=$(az containerapp show \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.traffic[?weight==\`100\`].revisionName | [0]" -o tsv)
if [[ -z "$STABLE_REVISION" ]]; then
    err "No stable revision at 100 % traffic – cannot proceed"; exit 1
fi
log "Stable revision: $STABLE_REVISION"

# Verify it's running
REV_STATE=$(az containerapp revision show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --revision "$STABLE_REVISION" --query "properties.runningState" -o tsv)
if [[ "$REV_STATE" != "Running" ]]; then
    err "Stable revision not Running (state: $REV_STATE)"; exit 1
fi

# ---- 1. Create new revision at 0 % traffic ---------------------------------
REVISION_SUFFIX="r-${BUILD_ID}-${RANDOM}"
if [[ -n "$IMAGE" ]]; then
    log "Creating new revision with image: $IMAGE"
    retry_az containerapp update \
        --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --image "$IMAGE" \
        --revision-suffix "$REVISION_SUFFIX" \
        --set-active-revisions-mode multiple \
        --ingress-traffic-weight latest=0 --output none
else
    log "Creating new revision with MODEL_VERSION=$MODEL_VERSION"
    retry_az containerapp update \
        --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision-suffix "$REVISION_SUFFIX" \
        --set-active-revisions-mode multiple \
        --set-env-vars "MODEL_VERSION=$MODEL_VERSION" \
        --ingress-traffic-weight latest=0 --output none
fi

# ---- 2. Discover new revision ----------------------------------------------
NEW_REVISION=$(az containerapp revision list \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --all \
    --query "[?contains(name, '${REVISION_SUFFIX}')].name | [0]" -o tsv)
[[ -z "$NEW_REVISION" ]] && { err "Could not find revision with suffix $REVISION_SUFFIX"; exit 1; }
log "New revision: $NEW_REVISION"

# ---- 3. Private FQDN and readiness ----------------------------------------
PRIVATE_FQDN=$(get_revision_fqdn "$NEW_REVISION")
log "Private FQDN: $PRIVATE_FQDN"

if ! wait_for_ready "$PRIVATE_FQDN"; then
    err "Revision did not become ready"; exit 1
fi

# ---- 4. Model version check (if model change) ------------------------------
if [[ -n "$MODEL_VERSION" ]]; then
    reported_ver=$(curl -fsS "https://${PRIVATE_FQDN}/version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model_version',''))" 2>/dev/null || echo "")
    if [[ "$reported_ver" != "$MODEL_VERSION" ]]; then
        err "Model version mismatch: expected $MODEL_VERSION, got $reported_ver"; exit 1
    fi
    log "Model version verified: $reported_ver"
fi

# ---- 5. Baseline measurement (stable revision) -----------------------------
PUBLIC_FQDN=$(az containerapp show \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv)
run_k6 "$PUBLIC_FQDN" "baseline" "$K6_BASELINE_DURATION"
baseline_p95=$(python3 -c "import json; print(json.load(open('k6-summary-baseline.json'))['metrics']['http_req_duration']['p(95)'])")
baseline_err=$(python3 -c "import json; print(json.load(open('k6-summary-baseline.json'))['metrics']['http_req_failed']['value'])")
log "Baseline: p95=${baseline_p95}ms, error_rate=${baseline_err}"

THRESHOLD_P95=$(python3 -c "print(max($baseline_p95 * 1.5, $MAX_P95_LATENCY_MS))")
THRESHOLD_ERR=$(python3 -c "print(max($baseline_err * 2.0, $MAX_ERROR_RATE))")
log "Dynamic thresholds: p95<${THRESHOLD_P95}ms, error_rate<${THRESHOLD_ERR}"

# ---- 6. 0 % load test (private FQDN) ---------------------------------------
log "=== 0% traffic validation (private FQDN) ==="
run_k6 "$PRIVATE_FQDN" "zero-percent" "3m"
if ! evaluate_k6 "zero-percent" "$THRESHOLD_P95" "$THRESHOLD_ERR"; then
    log "0% test failed – deleting revision"
    az containerapp revision delete --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision "$NEW_REVISION" --yes --output none || true
    exit 1
fi

# ---- 7. Gradual traffic shifting -------------------------------------------
IFS=',' read -r -a STEPS <<< "$CANARY_STEPS"
for step in "${STEPS[@]}"; do
    if [[ $step -ge 100 ]]; then
        log "Skipping step >= 100 (promotion handled separately)"
        continue
    fi
    log "=== Shifting to $step% traffic ==="
    retry_az containerapp ingress traffic set \
        --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision-weight "${NEW_REVISION}=${step}" "${STABLE_REVISION}=$((100 - step))" \
        --output none

    run_k6 "$PUBLIC_FQDN" "canary-${step}" "$K6_CANARY_DURATION"
    if ! evaluate_k6 "canary-${step}" "$THRESHOLD_P95" "$THRESHOLD_ERR"; then
        log "Canary step $step% failed – rolling back"
        rollback  # will exit
    fi
done

# ---- 8. Promote to 100 % ---------------------------------------------------
log "=== Promoting to 100% ==="
retry_az containerapp ingress traffic set \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --revision-weight "${NEW_REVISION}=100" --output none

# ---- 9. Update MLflow alias (if model change) ------------------------------
if [[ -n "$MODEL_VERSION" && -n "$MLFLOW_TRACKING_URI" ]]; then
    log "Updating MLflow production alias to $MODEL_VERSION"
    python3 -c "
from mlflow.tracking import MlflowClient
client = MlflowClient(tracking_uri='$MLFLOW_TRACKING_URI')
client.set_registered_model_alias('acs_income_classifier', 'production', '$MODEL_VERSION')
print('Alias updated')
" || log "WARNING: MLflow alias update failed (model serving unaffected)"
fi

# ---- 10. Cleanup old revisions ---------------------------------------------
log "Pruning old revisions (keeping max $MAX_REVISIONS_TO_KEEP)..."
OLD_REVS=$(az containerapp revision list --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --all \
    --query "[?name!='$NEW_REVISION' && name!='$STABLE_REVISION'] | sort_by(@, &properties.createdTime) | [: -${MAX_REVISIONS_TO_KEEP}].name" -o tsv)
for rev in $OLD_REVS; do
    log "Deleting old revision: $rev"
    az containerapp revision delete --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --revision "$rev" --yes --output none || true
done

CANARY_SUCCEEDED=1
log "Canary deployment succeeded. New stable revision: $NEW_REVISION"