#!/usr/bin/env bash
# ==============================================================================
# update_variable_group.sh
#
# Idempotent Azure DevOps variable group update.
# Reads config from environment variables (with strong defaults).
# Only updates variables whose values differ from current state.
#
# Usage:
#   export AZDO_ORG_SERVICE_URL="https://dev.azure.com/contoso"
#   export CANARY_STEPS="5,10,25,50"
#   bash src/scripts/update_variable_group.sh
#   bash src/scripts/update_variable_group.sh --dry-run
#   bash src/scripts/update_variable_group.sh --set KEY=VALUE --set KEY2=VALUE2
# ==============================================================================

set -uo pipefail

# ── defaults for every supported variable ────────────────────────────────────
AZDO_ORG_SERVICE_URL="${AZDO_ORG_SERVICE_URL:-}"
AZDO_PROJECT="${AZDO_PROJECT:-azdo-bootstrap-f41930}"
VARIABLE_GROUP_NAME="${VARIABLE_GROUP_NAME:-sm-all-vars}"

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

AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME:-}"
MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-}"
RAW_CONTAINER_NAME="${RAW_CONTAINER_NAME:-raw}"
CLEAN_CONTAINER_NAME="${CLEAN_CONTAINER_NAME:-clean}"
CHECKPOINT_CONTAINER_NAME="${CHECKPOINT_CONTAINER_NAME:-checkpoints}"
CONTAINER_REGISTRY="${CONTAINER_REGISTRY:-}"
CONTAINER_APP_JOB_NAME="${CONTAINER_APP_JOB_NAME:-}"
CONTAINER_APP_NAME="${CONTAINER_APP_NAME:-}"
AZURE_SERVICE_CONNECTION="${AZURE_SERVICE_CONNECTION:-azdo-oidc-cd}"
STAGING_RG="${STAGING_RG:-}"
PROD_RG="${PROD_RG:-}"

DRY_RUN=false
declare -A CLI_VARS

# ── helpers ──────────────────────────────────────────────────────────────────
log()    { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail()   { log "ERROR: $*" >&2; exit 1; }
usage()  {
    sed -n '2,/^$/s/^# //p' "$0"
    exit 0
}

require_az() {
    command -v az >/dev/null 2>&1 || fail "Azure CLI (az) is required"
    az account show >/dev/null 2>&1 || fail "Run 'az login' first"
}

# ── parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --group-name) VARIABLE_GROUP_NAME="$2"; shift 2 ;;
        --project)    AZDO_PROJECT="$2";           shift 2 ;;
        --org)        AZDO_ORG_SERVICE_URL="$2";   shift 2 ;;
        --set)
            [[ "$2" =~ ^([^=]+)=(.*)$ ]] || fail "--set requires KEY=VALUE"
            CLI_VARS["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
            shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help)    usage ;;
        *)         fail "Unknown option: $1" ;;
    esac
done

require_az

# ── build desired state from env + --set overrides ───────────────────────────
declare -A DESIRED

desired() { DESIRED["$1"]="$2"; }

desired CANARY_STEPS               "$CANARY_STEPS"
desired K6_SCRIPT                  "$K6_SCRIPT"
desired K6_BASELINE_DURATION       "$K6_BASELINE_DURATION"
desired K6_CANARY_DURATION         "$K6_CANARY_DURATION"
desired HEALTH_PATH                "$HEALTH_PATH"
desired READY_PATH                 "$READY_PATH"
desired MAX_REVISIONS_TO_KEEP      "$MAX_REVISIONS_TO_KEEP"
desired MAX_P95_LATENCY_MS         "$MAX_P95_LATENCY_MS"
desired MAX_ERROR_RATE             "$MAX_ERROR_RATE"
desired ENABLE_ROLLBACK_ALERT      "$ENABLE_ROLLBACK_ALERT"

[[ -n "$AZURE_STORAGE_ACCOUNT_NAME" ]] && desired AZURE_STORAGE_ACCOUNT_NAME "$AZURE_STORAGE_ACCOUNT_NAME"
[[ -n "$MLFLOW_TRACKING_URI" ]]       && desired MLFLOW_TRACKING_URI       "$MLFLOW_TRACKING_URI"
[[ -n "$RAW_CONTAINER_NAME" ]]        && desired RAW_CONTAINER_NAME        "$RAW_CONTAINER_NAME"
[[ -n "$CLEAN_CONTAINER_NAME" ]]      && desired CLEAN_CONTAINER_NAME      "$CLEAN_CONTAINER_NAME"
[[ -n "$CHECKPOINT_CONTAINER_NAME" ]] && desired CHECKPOINT_CONTAINER_NAME "$CHECKPOINT_CONTAINER_NAME"
[[ -n "$CONTAINER_REGISTRY" ]]        && desired containerRegistry         "$CONTAINER_REGISTRY"
[[ -n "$CONTAINER_APP_JOB_NAME" ]]    && desired CONTAINER_APP_JOB_NAME    "$CONTAINER_APP_JOB_NAME"
[[ -n "$CONTAINER_APP_NAME" ]]        && desired CONTAINER_APP_NAME        "$CONTAINER_APP_NAME"
[[ -n "$AZURE_SERVICE_CONNECTION" ]]  && desired azureServiceConnection    "$AZURE_SERVICE_CONNECTION"
[[ -n "$STAGING_RG" ]]               && desired STAGING_RG                "$STAGING_RG"
[[ -n "$PROD_RG" ]]                  && desired PROD_RG                   "$PROD_RG"

for k in "${!CLI_VARS[@]}"; do desired "$k" "${CLI_VARS[$k]}"; done

[[ ${#DESIRED[@]} -eq 0 ]] && { log "Nothing to update."; exit 0; }

# ── fetch group ──────────────────────────────────────────────────────────────
[[ -z "$AZDO_ORG_SERVICE_URL" ]] && fail "AZDO_ORG_SERVICE_URL is required"

GROUP_ID=$(az pipelines variable-group list \
    --org "$AZDO_ORG_SERVICE_URL" \
    --project "$AZDO_PROJECT" \
    --group-name "$VARIABLE_GROUP_NAME" \
    --query "[0].id" -o tsv) || fail "Variable group '$VARIABLE_GROUP_NAME' not found"

# ── fetch current values ─────────────────────────────────────────────────────
CURRENT_JSON=$(az pipelines variable-group variable list \
    --org "$AZDO_ORG_SERVICE_URL" \
    --project "$AZDO_PROJECT" \
    --group-id "$GROUP_ID" \
    -o json)

declare -A CURRENT
while IFS=$'\t' read -r name value; do
    CURRENT["$name"]="$value"
done < <(echo "$CURRENT_JSON" | python3 -c "
import json,sys
for v in json.load(sys.stdin):
    print(v['name']+'\t'+str(v.get('value','')))
")

# ── apply changes ────────────────────────────────────────────────────────────
CHANGES=0
for var_name in "${!DESIRED[@]}"; do
    new_val="${DESIRED[$var_name]}"
    old_val="${CURRENT[$var_name]:-}"
    exists=$([[ -v CURRENT[$var_name] ]] && echo true || echo false)

    [[ "$old_val" == "$new_val" ]] && continue
    ((CHANGES++))

    if $DRY_RUN; then
        log "DRY-RUN: $($exists && echo update || echo create) $var_name = $new_val"
        continue
    fi

    log "APPLY: $($exists && echo update || echo create) $var_name"
    if $exists; then
        az pipelines variable-group variable update \
            --org "$AZDO_ORG_SERVICE_URL" \
            --project "$AZDO_PROJECT" \
            --group-id "$GROUP_ID" \
            --name "$var_name" \
            --value "$new_val" \
            --output none
    else
        az pipelines variable-group variable create \
            --org "$AZDO_ORG_SERVICE_URL" \
            --project "$AZDO_PROJECT" \
            --group-id "$GROUP_ID" \
            --name "$var_name" \
            --value "$new_val" \
            --output none
    fi
done

log "${DRY_RUN:+DRY-RUN: }Updated $CHANGES variable(s)."