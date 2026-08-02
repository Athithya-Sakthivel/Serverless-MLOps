#!/usr/bin/env bash

# ==============================================================================
# One-time local development role assignments.
# Run this after bootstrap.sh completes successfully.
#
# Strictly idempotent – safe to run any number of times.
# Every role assignment is checked before creation.
#
# Grants:
#   1. Key Vault Secrets Officer on the bootstrap Key Vault
#      (so the bootstrap can write azdo-pat to Key Vault)
#   2. Storage Blob Data Contributor on the staging data lake
#      (so test_e2e_locally.sh and simulate_data_upload.py work)
# ==============================================================================

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
role_assignment_exists() {
  local assignee="$1"
  local role_name="$2"
  local scope="$3"

  # Use a subshell with timeout to prevent hanging on slow RBAC queries.
  # If the query takes more than 30 seconds, assume the assignment does not exist.
  local result
  result=$(timeout 30 az role assignment list \
    --assignee "$assignee" \
    --role "$role_name" \
    --scope "$scope" \
    --query "[0].id" -o tsv 2>/dev/null || true)

  [[ -n "$result" ]]
}

assign_role() {
  local assignee="$1"
  local role_name="$2"
  local scope="$3"

  echo "  Checking: ${role_name}..."
  if role_assignment_exists "$assignee" "$role_name" "$scope"; then
    echo "  [SKIP] Already assigned"
    return 0
  fi

  echo "  [CREATE] ${role_name} at scope ${scope}..."

  local attempt=0
  local max_attempts=3
  local delay=10

  while [[ $attempt -lt $max_attempts ]]; do
    if az role assignment create \
      --assignee "$assignee" \
      --role "$role_name" \
      --scope "$scope" \
      --output none 2>/dev/null; then
      echo "  [OK] Assigned"
      return 0
    fi

    attempt=$((attempt + 1))
    if [[ $attempt -lt $max_attempts ]]; then
      echo "  [RETRY] Attempt ${attempt} failed – retrying in ${delay}s..."
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done

  echo "  [ERROR] Failed to assign ${role_name} after ${max_attempts} attempts" >&2
  return 1
}

# ---------------------------------------------------------------------------
# Resolve Azure context
# ---------------------------------------------------------------------------
echo "=== Resolving Azure context ==="

AZURE_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
SUBSCRIPTION_SUFFIX="${AZURE_SUBSCRIPTION_ID: -6}"
STATE_RG="rg-sm-state-${SUBSCRIPTION_SUFFIX}"
PROJECT_NAME="azdo-bootstrap-${SUBSCRIPTION_SUFFIX}"
USER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"

echo "  Subscription: ${AZURE_SUBSCRIPTION_ID}"
echo "  Subscription suffix: ${SUBSCRIPTION_SUFFIX}"
echo "  State RG: ${STATE_RG}"
echo "  Project: ${PROJECT_NAME}"
echo "  User object ID: ${USER_OBJECT_ID}"
echo ""

# ---------------------------------------------------------------------------
# 1. Key Vault Secrets Officer on the bootstrap Key Vault
# ---------------------------------------------------------------------------
echo "=== 1. Key Vault Secrets Officer ==="

KV_ID="/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${STATE_RG}/providers/Microsoft.KeyVault/vaults/kv-${PROJECT_NAME}"
echo "  Scope: ${KV_ID}"

assign_role "$USER_OBJECT_ID" "Key Vault Secrets Officer" "$KV_ID"
echo ""

# ---------------------------------------------------------------------------
# 2. Storage Blob Data Contributor on the staging data lake
# ---------------------------------------------------------------------------
echo "=== 2. Storage Blob Data Contributor (staging data lake) ==="

ENV_ABBR="stg"
STORAGE_ACC_NAME="sm${ENV_ABBR}artifacts${SUBSCRIPTION_SUFFIX}"
STORAGE_SCOPE="/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/rg-sm-artifacts-stg/providers/Microsoft.Storage/storageAccounts/${STORAGE_ACC_NAME}"
echo "  Scope: ${STORAGE_SCOPE}"

assign_role "$USER_OBJECT_ID" "Storage Blob Data Contributor" "$STORAGE_SCOPE"
echo "sleep 30"
sleep 30

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo "=== All local development roles are ready ==="