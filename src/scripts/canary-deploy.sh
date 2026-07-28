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
# ==============================================================================

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Defaults & argument parsing
# ---------------------------------------------------------------------------
CANARY_STEPS=(10 50)
K6_SCRIPT="tests/load/k6-script.js"
HEALTH_PATH="/health"
READY_PATH="/ready"
IMAGE=""
MODEL_VERSION=""
APP_NAME=""
RESOURCE_GROUP=""
BUILD_ID="${BUILD_BUILDID:-$(date +%s)}"

usage() {
    echo "Usage: $0 --app-name <name> --resource-group <rg> (--image <img> | --model-version <ver>) [options]"
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-name) APP_NAME="$2"; shift 2 ;;
        --resource-group) RESOURCE_GROUP="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --model-version) MODEL_VERSION="$2"; shift 2 ;;
        --canary-steps) IFS=',' read -r -a CANARY_STEPS <<< "$2"; shift 2 ;;
        --k6-script) K6_SCRIPT="$2"; shift 2 ;;
        --health-path) HEALTH_PATH="$2"; shift 2 ;;
        --ready-path) READY_PATH="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[[ -z "$APP_NAME" || -z "$RESOURCE_GROUP" ]] && usage
[[ -z "$IMAGE" && -z "$MODEL_VERSION" ]] && { echo "Either --image or --model-version required"; exit 2; }
[[ -n "$IMAGE" && -n "$MODEL_VERSION" ]] && { echo "Specify either --image or --model-version, not both"; exit 2; }

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

install_k6() {
    if command -v k6 >/dev/null 2>&1; then return 0; fi
    log "Installing k6..."
    curl -sL https://github.com/grafana/k6/releases/download/v0.47.0/k6-v0.47.0-linux-amd64.tar.gz | tar xz
    sudo mv k6-v0.47.0-linux-amd64/k6 /usr/local/bin/k6
    command -v k6 >/dev/null || fail "k6 installation failed"
}

get_revision_fqdn() {
    local revision_name="$1"
    az containerapp revision show \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --revision "$revision_name" \
        --query "properties.fqdn" -o tsv
}

wait_for_revision_ready() {
    local fqdn="$1"
    local max_attempts=60
    local attempt=0
    log "Waiting for revision to become ready (private FQDN: $fqdn)..."
    until curl -fsS "https://${fqdn}${READY_PATH}" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            fail "Revision did not become ready after $max_attempts attempts"
        fi
        sleep 5
    done
    log "Revision is ready"
}

run_k6_test() {
    local fqdn="$1"
    local stage_label="$2"
    log "Running k6 test for $stage_label (target: $fqdn)..."
    k6 run "$K6_SCRIPT" \
        -e APP_FQDN="$fqdn" \
        --summary-export="k6-summary-${stage_label}.json" \
        --quiet 2>&1 | tail -5
    # Check thresholds – exit non-zero if thresholds violated
    python3 -c "
import json, sys
with open('k6-summary-${stage_label}.json') as f:
    data = json.load(f)
    thresholds = data.get('metrics', {})
    req_failed = thresholds.get('http_req_failed', {}).get('value', 1)
    p95 = thresholds.get('http_req_duration', {}).get('p(95)', 9999)
    if req_failed > 0.01:
        print(f'FAIL: error rate {req_failed:.4f} > 1%')
        sys.exit(1)
    if p95 > 200:
        print(f'FAIL: P95 latency {p95:.1f}ms > 200ms')
        sys.exit(1)
    print('k6 thresholds passed')
"
}

rollback() {
    log "ROLLING BACK to previous stable revision: $STABLE_REVISION"
    az containerapp ingress traffic set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --revision-weight "${STABLE_REVISION}=100" \
        --output none || true
    exit 1
}

# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
install_k6
log "Starting canary deployment for $APP_NAME (resource group: $RESOURCE_GROUP)"

# 1. Determine current stable revision (100 % traffic)
STABLE_REVISION=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.traffic[?weight==\`100\`].revisionName | [0]" \
    -o tsv || true)

REVISION_SUFFIX="r-${BUILD_ID}"

# 2. Create new revision at 0 % traffic
if [[ -n "$IMAGE" ]]; then
    log "Creating new revision with image: $IMAGE"
    az containerapp update \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --image "$IMAGE" \
        --revision-suffix "$REVISION_SUFFIX" \
        --set-active-revisions-mode multiple \
        --ingress-traffic-weight latest=0 \
        --output none
else
    log "Creating new revision with MODEL_VERSION=$MODEL_VERSION"
    az containerapp update \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --revision-suffix "$REVISION_SUFFIX" \
        --set-active-revisions-mode multiple \
        --set-env-vars "MODEL_VERSION=$MODEL_VERSION" \
        --ingress-traffic-weight latest=0 \
        --output none
fi

# 3. Discover the new revision name
NEW_REVISION=$(az containerapp revision list \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --all \
    --query "[?contains(name, '${REVISION_SUFFIX}')].name | [0]" \
    -o tsv)

[[ -z "$NEW_REVISION" ]] && fail "Could not find new revision with suffix $REVISION_SUFFIX"

# 4. Get private FQDN and wait for /ready
PRIVATE_FQDN=$(get_revision_fqdn "$NEW_REVISION")
log "New revision: $NEW_REVISION (private FQDN: $PRIVATE_FQDN)"
wait_for_revision_ready "$PRIVATE_FQDN"

# 5. Run full k6 load test at 0 % traffic (private FQDN)
log "=== 0% traffic validation (private FQDN) ==="
if ! run_k6_test "$PRIVATE_FQDN" "zero-percent"; then
    log "0% traffic test failed – deleting revision and aborting"
    az containerapp revision delete \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --revision "$NEW_REVISION" \
        --yes --output none || true
    exit 1
fi

# 6. Gradual traffic shifting
PUBLIC_FQDN=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv)

for step in "${CANARY_STEPS[@]}"; do
    log "=== Shifting to $step% traffic ==="
    az containerapp ingress traffic set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --revision-weight "${NEW_REVISION}=${step}" "${STABLE_REVISION}=$((100 - step))" \
        --output none

    # Short k6 check on public FQDN
    if ! run_k6_test "$PUBLIC_FQDN" "canary-${step}"; then
        log "Canary step $step% failed – rolling back"
        rollback
    fi
done

# 7. Promote to 100 %
log "=== Promoting to 100% ==="
az containerapp ingress traffic set \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --revision-weight "${NEW_REVISION}=100" \
    --output none

log "Canary deployment succeeded. New revision: $NEW_REVISION (private FQDN: $PRIVATE_FQDN)"
log "Previous stable revision ($STABLE_REVISION) remains available for manual rollback."