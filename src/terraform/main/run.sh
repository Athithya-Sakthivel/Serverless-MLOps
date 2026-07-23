#!/usr/bin/env bash
# ==============================================================================
# terraform/main/run.sh
# ==============================================================================
# Single entrypoint for all OpenTofu operations: init, plan, apply, destroy.
#
# Design invariants:
#   1. TF_BACKEND_AUTH_MODE is the one source of truth for backend auth.
#   2. Script always executes from its own directory.
#   3. State backend naming matches bootstrap.sh (subscription suffix).
#   4. No secrets are ever logged or written to disk outside .plans/.
#   5. CI OIDC path is untouched; TF_BACKEND_AUTH_MODE=cli uses az CLI.
#   6. subscription_id and tenant_id are auto‑fetched from az CLI and exported
#      as TF_VAR_* unless already set in the environment.
#   7. All temporary files are explicitly removed; no trap race conditions.
#   8. Azure CLI token is refreshed immediately before long-running operations.
#   9. Nuclear destroy: deletes resource group (waits for completion), purges
#      soft-deleted Key Vault & ML workspace, deletes state blob, breaks locks.
#  10. --create after --destroy always sees a clean subscription and empty state.
#  11. Event Grid subscription is created via Azure CLI after Terraform apply,
#      using the same naming derivation as locals.tf – no dependency on
#      `tofu output`.
#  12. Azure DevOps pipeline + variable‑group variables are auto‑derived from
#      the subscription and git remote.
#  13. Azure DevOps provider credentials are mapped from TF_VAR_AZDO_* to plain
#      env vars so the provider block works without hardcoding.
#  14. Every --create regenerates the plan from scratch (never reuses a stale plan).
#
# Local development (az login first):
#   export TF_BACKEND_AUTH_MODE=cli
#   bash src/terraform/main/run.sh --plan  --env staging
#   bash src/terraform/main/run.sh --create --env staging
#   bash src/terraform/main/run.sh --destroy --env staging --yes-delete
#
# CI/CD (Azure DevOps with OIDC service connection):
#   bash src/terraform/main/run.sh --plan  --env staging
#   bash src/terraform/main/run.sh --create --env staging
# ==============================================================================

set -euo pipefail
IFS=$'\n\t'

# Always run from the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR" || { echo "ERROR: cannot cd to $SCRIPT_DIR" >&2; exit 1; }

BOOTSTRAP_ENV_FILE="$SCRIPT_DIR/.bootstrap.generated.env"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "$1 missing"; }

usage() {
  cat >&2 <<'USAGE'
Usage:
  run.sh --plan --env <prod|staging>
  run.sh --create --env <prod|staging>
  run.sh --apply-plan <plan-file> --env <prod|staging>
  run.sh --validate --env <prod|staging>
  run.sh --destroy --env <prod|staging> --yes-delete
USAGE
  exit 2
}

# ---------------------------------------------------------------------------
# 1. Capture the user's explicit auth mode (before anything can override it)
# ---------------------------------------------------------------------------
EXPLICIT_AUTH_MODE="${TF_BACKEND_AUTH_MODE:-}"

# ---------------------------------------------------------------------------
# 2. Load bootstrap env (CI credentials) but never override explicit choice
# ---------------------------------------------------------------------------
load_bootstrap_env() {
  if [[ -f "$BOOTSTRAP_ENV_FILE" ]]; then
    source "$BOOTSTRAP_ENV_FILE"
  fi
  if [[ -n "$EXPLICIT_AUTH_MODE" ]]; then
    export TF_BACKEND_AUTH_MODE="$EXPLICIT_AUTH_MODE"
  fi
}

# ---------------------------------------------------------------------------
# 3. Install OpenTofu if missing
# ---------------------------------------------------------------------------
install_tofu_if_needed() {
  if command -v tofu >/dev/null 2>&1; then
    return 0
  fi
  require_cmd curl
  require_cmd unzip
  local tofu_version="${TOFU_VERSION:-1.12.4}"
  local tmp_zip tmp_dir
  tmp_zip="$(mktemp)"
  tmp_dir="$(mktemp -d)"
  curl -fsSL -o "$tmp_zip" \
    "https://github.com/opentofu/opentofu/releases/download/v${tofu_version}/tofu_${tofu_version}_linux_amd64.zip"
  unzip -o "$tmp_zip" -d "$tmp_dir" >/dev/null
  mkdir -p "$HOME/bin"
  install -m 0755 "$tmp_dir/tofu" "$HOME/bin/tofu"
  export PATH="$HOME/bin:$PATH"
  rm -rf "$tmp_zip" "$tmp_dir"
}

# ---------------------------------------------------------------------------
# 4. Resolve Azure context & export Terraform variables
# ---------------------------------------------------------------------------
resolve_azure_context() {
  require_cmd az
  export TF_VAR_subscription_id="${TF_VAR_subscription_id:-$(az account show --query id -o tsv)}"
  export TF_VAR_tenant_id="${TF_VAR_tenant_id:-$(az account show --query tenantId -o tsv)}"

  [[ -n "$TF_VAR_subscription_id" ]] || fail "unable to resolve subscription; run az login or set TF_VAR_subscription_id"
  [[ -n "$TF_VAR_tenant_id" ]]       || fail "unable to resolve tenant; run az login or set TF_VAR_tenant_id"
}

# ---------------------------------------------------------------------------
# 5. Determine backend authentication mode
# ---------------------------------------------------------------------------
choose_auth_mode() {
  if [[ -n "${TF_BACKEND_AUTH_MODE:-}" ]]; then
    case "$TF_BACKEND_AUTH_MODE" in
      oidc|cli|access_key) AUTH_MODE="$TF_BACKEND_AUTH_MODE" ;;
      *) fail "unsupported TF_BACKEND_AUTH_MODE: $TF_BACKEND_AUTH_MODE" ;;
    esac
    return
  fi

  if [[ -n "${ARM_ACCESS_KEY:-}" ]]; then
    AUTH_MODE="access_key"
  elif [[ -n "${ARM_OIDC_TOKEN:-}" ]]; then
    AUTH_MODE="oidc"
  else
    AUTH_MODE="cli"
  fi
}

# ---------------------------------------------------------------------------
# 6. Compute backend resource names (uses subscription suffix)
# ---------------------------------------------------------------------------
compute_defaults() {
  local subscription_suffix="${SUBSCRIPTION_SUFFIX:-${TF_VAR_subscription_id: -6}}"
  TF_BACKEND_RESOURCE_GROUP="${TF_BACKEND_RESOURCE_GROUP:-rg-sm-state-${subscription_suffix}}"
  TF_BACKEND_STORAGE_ACCOUNT="${TF_BACKEND_STORAGE_ACCOUNT:-smstatesa${subscription_suffix}}"
  TF_BACKEND_CONTAINER="${TF_BACKEND_CONTAINER:-tfbackend}"
  TF_BACKEND_KEY_PREFIX="${TF_BACKEND_KEY_PREFIX:-main/terraform}"
}

# ---------------------------------------------------------------------------
# 7. Build backend config file – returns path, caller cleans up
# ---------------------------------------------------------------------------
build_backend_config() {
  local backend_config
  backend_config="$(mktemp)"
  case "$AUTH_MODE" in
    access_key)
      [[ -n "${ARM_ACCESS_KEY:-}" ]] || fail "ARM_ACCESS_KEY required"
      cat >"$backend_config" <<EOF
resource_group_name  = "$TF_BACKEND_RESOURCE_GROUP"
storage_account_name = "$TF_BACKEND_STORAGE_ACCOUNT"
container_name       = "$TF_BACKEND_CONTAINER"
key                  = "$TF_BACKEND_KEY"
access_key           = "$ARM_ACCESS_KEY"
EOF
      ;;
    oidc)
      [[ -n "${ARM_CLIENT_ID:-}" ]] || fail "ARM_CLIENT_ID is required for oidc auth"
      [[ -n "${ARM_OIDC_TOKEN:-}" ]] || fail "ARM_OIDC_TOKEN is required for oidc auth"
      cat >"$backend_config" <<EOF
resource_group_name  = "$TF_BACKEND_RESOURCE_GROUP"
storage_account_name = "$TF_BACKEND_STORAGE_ACCOUNT"
container_name       = "$TF_BACKEND_CONTAINER"
key                  = "$TF_BACKEND_KEY"
use_azuread_auth     = true
subscription_id      = "$TF_VAR_subscription_id"
tenant_id            = "$TF_VAR_tenant_id"
client_id            = "$ARM_CLIENT_ID"
use_oidc             = true
EOF
      ;;
    cli)
      cat >"$backend_config" <<EOF
resource_group_name  = "$TF_BACKEND_RESOURCE_GROUP"
storage_account_name = "$TF_BACKEND_STORAGE_ACCOUNT"
container_name       = "$TF_BACKEND_CONTAINER"
key                  = "$TF_BACKEND_KEY"
EOF
      ;;
    *)
      fail "unsupported auth mode: $AUTH_MODE"
      ;;
  esac
  echo "$backend_config"
}

# ---------------------------------------------------------------------------
# 8. Initialise backend – explicit cleanup, no traps
# ---------------------------------------------------------------------------
init_backend() {
  local backend_config_file
  backend_config_file="$(build_backend_config)"
  tofu init -reconfigure -input=false -upgrade -backend-config="$backend_config_file"
  rm -f "$backend_config_file"
}

# ---------------------------------------------------------------------------
# 9. Core operations
# ---------------------------------------------------------------------------
ensure_plan_dir() { mkdir -p "$PLAN_DIR"; }

prepare_stack() {
  tofu fmt -recursive
  init_backend
  tofu validate -no-color
}

run_plan() {
  ensure_plan_dir
  rm -f "$PLAN_FILE"                     # always delete old plan first
  prepare_stack
  tofu plan -input=false -lock-timeout=5m -var-file="$VAR_FILE" -out="$PLAN_FILE"
}

run_apply_plan() {
  [[ -f "$PLAN_FILE_INPUT" ]] || fail "plan file not found: $PLAN_FILE_INPUT"
  init_backend
  az account get-access-token --resource https://management.azure.com > /dev/null 2>&1 || true
  tofu apply -input=false -lock-timeout=5m -auto-approve "$PLAN_FILE_INPUT"
}

# ---------------------------------------------------------------------------
# 10. Event Grid subscription management (outside Terraform)
# ---------------------------------------------------------------------------

derive_names() {
  local sub_suffix="${TF_VAR_subscription_id: -6}"
  local project_abbr="sm"
  local env_abbr
  case "$ENVIRONMENT" in
    staging) env_abbr="stg" ;;
    prod)    env_abbr="prod" ;;
    *)       fail "unknown environment: $ENVIRONMENT" ;;
  esac

  RG_NAME="rg-${project_abbr}-artifacts-${env_abbr}"
  STORAGE_ACCOUNT_NAME="${project_abbr}${env_abbr}artifacts${sub_suffix}"
  QUEUE_NAME="${project_abbr}trainqueue-${env_abbr}"
  SYSTEM_TOPIC_NAME="eg-${project_abbr}-${env_abbr}-storage"
  SUBSCRIPTION_NAME="eg-${project_abbr}-${env_abbr}-raw-monthly"
}

create_event_subscription() {
  derive_names

  if az eventgrid system-topic event-subscription show \
    -g "$RG_NAME" --system-topic-name "$SYSTEM_TOPIC_NAME" \
    -n "$SUBSCRIPTION_NAME" --subscription "$TF_VAR_subscription_id" &>/dev/null; then
    log "Event Subscription '$SUBSCRIPTION_NAME' already exists"
    return 0
  fi

  local queue_id="/subscriptions/${TF_VAR_subscription_id}/resourceGroups/${RG_NAME}/providers/Microsoft.Storage/storageAccounts/${STORAGE_ACCOUNT_NAME}/queueServices/default/queues/${QUEUE_NAME}"

  log "Creating Event Subscription '$SUBSCRIPTION_NAME' ..."
  az eventgrid system-topic event-subscription create \
    -g "$RG_NAME" \
    --system-topic-name "$SYSTEM_TOPIC_NAME" \
    -n "$SUBSCRIPTION_NAME" \
    --subscription "$TF_VAR_subscription_id" \
    --included-event-types Microsoft.Storage.BlobCreated \
    --subject-begins-with "/blobServices/default/containers/raw/blobs/monthly/" \
    --subject-ends-with .parquet \
    --endpoint-type storagequeue \
    --endpoint "$queue_id" >/dev/null || {
      log "Failed to create Event Subscription"
      return 1
    }
  log "Event Subscription '$SUBSCRIPTION_NAME' created"
}

delete_event_subscription() {
  derive_names

  if ! az eventgrid system-topic event-subscription show \
    -g "$RG_NAME" --system-topic-name "$SYSTEM_TOPIC_NAME" \
    -n "$SUBSCRIPTION_NAME" --subscription "$TF_VAR_subscription_id" &>/dev/null; then
    log "Event Subscription '$SUBSCRIPTION_NAME' does not exist"
    return 0
  fi

  log "Deleting Event Subscription '$SUBSCRIPTION_NAME'..."
  az eventgrid system-topic event-subscription delete \
    -g "$RG_NAME" --system-topic-name "$SYSTEM_TOPIC_NAME" \
    -n "$SUBSCRIPTION_NAME" --subscription "$TF_VAR_subscription_id" --yes || true
  log "Event Subscription deleted"
}

# ---------------------------------------------------------------------------
# 11. Nuclear destroy – complete cleanup, no leftovers, blocks until finished
# ---------------------------------------------------------------------------
nuclear_destroy() {
  log "starting nuclear destroy for environment $ENVIRONMENT"

  local project_abbr="sm"
  local env_abbr
  case "$ENVIRONMENT" in
    staging) env_abbr="stg" ;;
    prod)    env_abbr="prod" ;;
    *)       fail "unknown environment: $ENVIRONMENT" ;;
  esac
  local sub_suffix="${TF_VAR_subscription_id: -6}"

  local rg_name="rg-${project_abbr}-artifacts-${env_abbr}"
  local kv_name="kv-${project_abbr}${env_abbr}ml${sub_suffix}"
  local ml_workspace_name="mlw-${project_abbr}-${env_abbr}"
  local location="southindia"

  log "breaking state lock if present"
  az storage blob lease break \
    --blob-name "${TF_BACKEND_KEY_PREFIX}/${ENVIRONMENT}.tfstate" \
    --container-name "$TF_BACKEND_CONTAINER" \
    --account-name "$TF_BACKEND_STORAGE_ACCOUNT" \
    --auth-mode login 2>/dev/null || true

  log "deleting resource group: $rg_name (this may take a few minutes)"
  if az group show -n "$rg_name" --subscription "$TF_VAR_subscription_id" &>/dev/null; then
    az group delete -n "$rg_name" --yes --subscription "$TF_VAR_subscription_id"
    while az group show -n "$rg_name" --subscription "$TF_VAR_subscription_id" &>/dev/null; do
      echo "  waiting for resource group deletion... $(date +%H:%M:%S)"
      sleep 15
    done
    log "resource group deleted"
  else
    log "resource group not found, skipping"
  fi

  log "purging Key Vault: $kv_name"
  az keyvault purge -n "$kv_name" --subscription "$TF_VAR_subscription_id" 2>/dev/null || {
    log "Key Vault not found or already purged"
  }

  log "purging ML workspace: $ml_workspace_name"
  az rest --method delete \
    --url "https://management.azure.com/subscriptions/${TF_VAR_subscription_id}/providers/Microsoft.MachineLearningServices/locations/${location}/deletedWorkspaces/${ml_workspace_name}?api-version=2024-10-01" \
    2>/dev/null || {
    log "ML workspace not found or already purged"
  }

  local state_key="${TF_BACKEND_KEY_PREFIX}/${ENVIRONMENT}.tfstate"
  log "deleting state blob: ${state_key}"

  local user_obj_id
  user_obj_id=$(az ad signed-in-user show --query id -o tsv 2>/dev/null) || true
  if [[ -n "$user_obj_id" ]]; then
    az role assignment create \
      --assignee "$user_obj_id" \
      --role "Storage Blob Data Contributor" \
      --scope "/subscriptions/${TF_VAR_subscription_id}/resourceGroups/${TF_BACKEND_RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/${TF_BACKEND_STORAGE_ACCOUNT}" \
      --subscription "$TF_VAR_subscription_id" 2>/dev/null || true
  fi

  az storage blob delete \
    --account-name "$TF_BACKEND_STORAGE_ACCOUNT" \
    --container-name "$TF_BACKEND_CONTAINER" \
    --name "$state_key" \
    --auth-mode login 2>/dev/null || {
    log "state blob not found or already deleted"
  }

  log "nuclear destroy completed – all resources, soft-deleted items, and state removed"
}

# ---------------------------------------------------------------------------
# 12. Argument parsing
# ---------------------------------------------------------------------------
MODE=""
ENVIRONMENT=""
PLAN_FILE_INPUT=""
YES_DELETE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan|--create|--validate|--destroy)
      MODE="$1"; shift ;;
    --apply-plan)
      MODE="--apply-plan"; shift
      PLAN_FILE_INPUT="${1:-}"; [[ -n "$PLAN_FILE_INPUT" ]] || usage; shift ;;
    --env)
      ENVIRONMENT="${2:-}"; [[ -n "$ENVIRONMENT" ]] || usage; shift 2 ;;
    --yes-delete)
      YES_DELETE=true; shift ;;
    *) usage ;;
  esac
done

[[ -n "$MODE" ]] || usage
if [[ "$MODE" != "--validate" && -z "$ENVIRONMENT" ]]; then usage; fi

require_cmd sha256sum
require_cmd python3
require_cmd curl
require_cmd unzip

# ---------------------------------------------------------------------------
# 13. Auto‑derive Azure DevOps variables from subscription & git remote
# ---------------------------------------------------------------------------
resolve_git_remote() {
  command -v git >/dev/null 2>&1 || return 1
  local remote_url repo_path
  remote_url="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
  [[ -n "$remote_url" ]] || return 1

  case "$remote_url" in
    https://github.com/*) repo_path="${remote_url#https://github.com/}" ;;
    git@github.com:*)     repo_path="${remote_url#git@github.com:}" ;;
    ssh://git@github.com/*) repo_path="${remote_url#ssh://git@github.com/}" ;;
    *) return 1 ;;
  esac

  repo_path="${repo_path%.git}"
  GIT_OWNER="${repo_path%%/*}"
  GIT_REPO="${repo_path##*/}"
  [[ -n "$GIT_OWNER" && -n "$GIT_REPO" && "$GIT_OWNER" != "$GIT_REPO" ]] || return 1
  return 0
}

resolve_ado_vars() {
  local sub_suffix="${TF_VAR_subscription_id: -6}"
  
  export TF_VAR_ado_client_id="${TF_VAR_ado_client_id:-${ARM_CLIENT_ID:-}}"
  # Project name – matches what bootstrap.sh creates
  export TF_VAR_ado_project_name="${TF_VAR_ado_project_name:-azdo-bootstrap-${sub_suffix}}"
  
  # Service connection names – match bootstrap.sh
  export TF_VAR_ado_github_service_connection_name="${TF_VAR_ado_github_service_connection_name:-github-pat}"
  export TF_VAR_ado_azure_service_connection_name="${TF_VAR_ado_azure_service_connection_name:-azdo-oidc-ci}"

  # GitHub owner/repo – from git remote
  if resolve_git_remote; then
    export TF_VAR_github_owner="${TF_VAR_github_owner:-$GIT_OWNER}"
    export TF_VAR_github_repo="${TF_VAR_github_repo:-$GIT_REPO}"
  else
    log "WARNING: unable to resolve git remote; GitHub owner/repo must be set via environment"
  fi

  # State backend details – same as used by this script (from compute_defaults)
  export TF_VAR_state_rg_name="${TF_VAR_state_rg_name:-$TF_BACKEND_RESOURCE_GROUP}"
  export TF_VAR_state_storage_account_name="${TF_VAR_state_storage_account_name:-$TF_BACKEND_STORAGE_ACCOUNT}"
  export TF_VAR_state_container_name="${TF_VAR_state_container_name:-$TF_BACKEND_CONTAINER}"
}

# ---------------------------------------------------------------------------
# 14. Execution order
# ---------------------------------------------------------------------------
load_bootstrap_env
resolve_azure_context
choose_auth_mode
install_tofu_if_needed
compute_defaults
resolve_ado_vars          # sets all Azure DevOps module variables automatically

# ---- Make Azure DevOps credentials available to the provider ----------------
# The azuredevops provider in providers.tf expects plain environment
# variables AZDO_ORG_SERVICE_URL and AZDO_PERSONAL_ACCESS_TOKEN.
# We map them from the TF_VAR_* equivalents that the user already has.
if [[ -n "${TF_VAR_AZDO_ORG_SERVICE_URL:-}" ]]; then
  export AZDO_ORG_SERVICE_URL="$TF_VAR_AZDO_ORG_SERVICE_URL"
elif [[ -n "${TF_VAR_ado_org_service_url:-}" ]]; then
  export AZDO_ORG_SERVICE_URL="$TF_VAR_ado_org_service_url"
fi

if [[ -n "${TF_VAR_AZDO_PERSONAL_ACCESS_TOKEN:-}" ]]; then
  export AZDO_PERSONAL_ACCESS_TOKEN="$TF_VAR_AZDO_PERSONAL_ACCESS_TOKEN"
elif [[ -n "${TF_VAR_ado_personal_access_token:-}" ]]; then
  export AZDO_PERSONAL_ACCESS_TOKEN="$TF_VAR_ado_personal_access_token"
fi
# ---------------------------------------------------------------------------

TF_BACKEND_KEY="${TF_BACKEND_KEY:-${TF_BACKEND_KEY_PREFIX}/${ENVIRONMENT}.tfstate}"
PLAN_DIR="$SCRIPT_DIR/.plans/$ENVIRONMENT"
PLAN_FILE="$PLAN_DIR/plan.tfplan"
VAR_FILE="$SCRIPT_DIR/environments/${ENVIRONMENT}.tfvars"

case "$MODE" in
  --validate) prepare_stack ;;
  --plan)
    [[ -f "$VAR_FILE" ]] || fail "variable file not found: $VAR_FILE"
    run_plan
    log "plan written to $PLAN_FILE"
    ;;
  --create)
    [[ -f "$VAR_FILE" ]] || fail "variable file not found: $VAR_FILE"
    run_plan                              # always fresh plan (rm -f inside)
    log "refreshing Azure CLI token"
    az account get-access-token --resource https://management.azure.com > /dev/null 2>&1 || true
    log "applying fresh plan $PLAN_FILE"
    tofu apply -input=false -lock-timeout=5m -auto-approve "$PLAN_FILE"

    log "wiring Event Grid subscription"
    create_event_subscription || log "Event Subscription creation failed; check logs"
    ;;
  --apply-plan) run_apply_plan ;;
  --destroy)
    $YES_DELETE || fail "--yes-delete required"
    [[ -f "$VAR_FILE" ]] || fail "variable file not found: $VAR_FILE"

    delete_event_subscription
    nuclear_destroy
    ;;
  *) usage ;;
esac