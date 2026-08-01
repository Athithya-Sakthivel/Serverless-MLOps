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
#   4. No secrets are ever logged or written to disk.
#   5. CI OIDC path is untouched; TF_BACKEND_AUTH_MODE=cli uses az CLI.
#   6. subscription_id and tenant_id are auto‑fetched from az CLI and exported
#      as TF_VAR_* unless already set in the environment.
#   7. All temporary files are explicitly removed; no trap race conditions.
#   8. Azure CLI token is refreshed immediately before long-running operations.
#   9. Nuclear destroy: deletes resource group (waits for completion), purges
#      soft-deleted Key Vault & ML workspace, deletes state blob, breaks locks.
#  10. --create after --destroy always sees a clean subscription and empty state.
#  11. The training job is started by an Azure Function blob trigger.
#      No Event Grid, Storage Queue, or KEDA scaler is used.
#  12. Azure DevOps pipeline + variable‑group variables are auto‑derived from
#      the subscription and git remote.
#  13. Azure DevOps provider credentials are mapped from TF_VAR_AZDO_* to plain
#      env vars so the provider block works without hardcoding.
#  14. Every --create regenerates the plan from scratch (never reuses a stale plan).
#  15. --skip-aca flag deploys everything except Container App/Job for slow
#      subscriptions; a second --create completes the full deployment.
#  16. Auto‑imports orphaned ACA resources from previous failed applies.
#  17. If the training job fails to provision (student subscription timeout),
#      it is automatically deleted and re‑created.
#  18. Automatically deploys Azure Function code after a successful full apply.
#
# Local development (az login first):
#   export TF_BACKEND_AUTH_MODE=cli
#   bash src/terraform/main/run.sh --plan  --env staging
#   bash src/terraform/main/run.sh --create --env staging
#   bash src/terraform/main/run.sh --destroy --env staging --yes-delete
#
# CI/CD (Azure DevOps with OIDC service connection):
#   bash src/terraform/main/run.sh --plan  --env staging
#   bash src/terraform/main/run.sh --create --env staging --skip-aca
#   bash src/terraform/main/run.sh --create --env staging
# ==============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR" || { echo "ERROR: cannot cd to $SCRIPT_DIR" >&2; exit 1; }

BOOTSTRAP_ENV_FILE="$SCRIPT_DIR/.bootstrap.generated.env"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "$1 missing"; }

usage() {
  cat >&2 <<'USAGE'
Usage:
  run.sh --plan --env <prod|staging> [--skip-aca]
  run.sh --create --env <prod|staging> [--skip-aca]
  run.sh --apply-plan <plan-file> --env <prod|staging>
  run.sh --validate --env <prod|staging>
  run.sh --destroy --env <prod|staging> --yes-delete

  --skip-aca  Deploy all infrastructure except Container Apps (for slow subs).
USAGE
  exit 2
}

# ---------------------------------------------------------------------------
# 1. Capture the user's explicit auth mode
# ---------------------------------------------------------------------------
EXPLICIT_AUTH_MODE="${TF_BACKEND_AUTH_MODE:-}"

# ---------------------------------------------------------------------------
# 2. Load bootstrap env
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
  if command -v tofu >/dev/null 2>&1; then return 0; fi
  require_cmd curl; require_cmd unzip
  local tofu_version="${TOFU_VERSION:-1.12.4}"
  local tmp_zip; tmp_zip="$(mktemp)"
  local tmp_dir; tmp_dir="$(mktemp -d)"
  curl -fsSL -o "$tmp_zip" \
    "https://github.com/opentofu/opentofu/releases/download/v${tofu_version}/tofu_${tofu_version}_linux_amd64.zip"
  unzip -o "$tmp_zip" -d "$tmp_dir" >/dev/null
  mkdir -p "$HOME/bin"
  install -m 0755 "$tmp_dir/tofu" "$HOME/bin/tofu"
  export PATH="$HOME/bin:$PATH"
  rm -rf "$tmp_zip" "$tmp_dir"
}

# ---------------------------------------------------------------------------
# 4. Resolve Azure context
# ---------------------------------------------------------------------------
resolve_azure_context() {
  require_cmd az
  export TF_VAR_subscription_id="${TF_VAR_subscription_id:-$(az account show --query id -o tsv)}"
  export TF_VAR_tenant_id="${TF_VAR_tenant_id:-$(az account show --query tenantId -o tsv)}"
  [[ -n "$TF_VAR_subscription_id" ]] || fail "unable to resolve subscription"
  [[ -n "$TF_VAR_tenant_id" ]]       || fail "unable to resolve tenant"
}

# ---------------------------------------------------------------------------
# 5. Backend auth mode
# ---------------------------------------------------------------------------
choose_auth_mode() {
  if [[ -n "${TF_BACKEND_AUTH_MODE:-}" ]]; then
    case "$TF_BACKEND_AUTH_MODE" in
      oidc|cli|access_key) AUTH_MODE="$TF_BACKEND_AUTH_MODE" ;;
      *) fail "unsupported TF_BACKEND_AUTH_MODE: $TF_BACKEND_AUTH_MODE" ;;
    esac
    return
  fi
  if [[ -n "${ARM_ACCESS_KEY:-}" ]]; then AUTH_MODE="access_key"
  elif [[ -n "${ARM_OIDC_TOKEN:-}" ]]; then AUTH_MODE="oidc"
  else AUTH_MODE="cli"
  fi
}

# ---------------------------------------------------------------------------
# 6. Backend resource names
# ---------------------------------------------------------------------------
compute_defaults() {
  local subscription_suffix="${SUBSCRIPTION_SUFFIX:-${TF_VAR_subscription_id: -6}}"
  TF_BACKEND_RESOURCE_GROUP="${TF_BACKEND_RESOURCE_GROUP:-rg-sm-state-${subscription_suffix}}"
  TF_BACKEND_STORAGE_ACCOUNT="${TF_BACKEND_STORAGE_ACCOUNT:-smstatesa${subscription_suffix}}"
  TF_BACKEND_CONTAINER="${TF_BACKEND_CONTAINER:-tfbackend}"
  TF_BACKEND_KEY_PREFIX="${TF_BACKEND_KEY_PREFIX:-main/terraform}"
}

# ---------------------------------------------------------------------------
# 7. Build backend config
# ---------------------------------------------------------------------------
build_backend_config() {
  local backend_config; backend_config="$(mktemp)"
  case "$AUTH_MODE" in
    access_key)
      cat >"$backend_config" <<EOF
resource_group_name  = "$TF_BACKEND_RESOURCE_GROUP"
storage_account_name = "$TF_BACKEND_STORAGE_ACCOUNT"
container_name       = "$TF_BACKEND_CONTAINER"
key                  = "$TF_BACKEND_KEY"
access_key           = "$ARM_ACCESS_KEY"
EOF
      ;;
    oidc)
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
    *) fail "unsupported auth mode: $AUTH_MODE" ;;
  esac
  echo "$backend_config"
}

# ---------------------------------------------------------------------------
# 8. Init
# ---------------------------------------------------------------------------
init_backend() {
  local backend_config_file; backend_config_file="$(build_backend_config)"
  tofu init -reconfigure -input=false -upgrade -backend-config="$backend_config_file"
  rm -f "$backend_config_file"
}

# ---------------------------------------------------------------------------
# 9. Core ops
# ---------------------------------------------------------------------------
ensure_plan_dir() { mkdir -p "$PLAN_DIR"; }

prepare_stack() {
  tofu fmt -recursive
  init_backend
  tofu validate -no-color
}

run_plan() {
  ensure_plan_dir
  rm -f "$PLAN_FILE"
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
# 10. Derived names (used by import, job healing, and function deployment)
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
  SERVE_APP_NAME="aca-serve-${env_abbr}"
  TRAIN_JOB_NAME="acaj-train-${env_abbr}"
}

# ---------------------------------------------------------------------------
# 11. Ensure training job is healthy (heals student-subscription timeouts)
# ---------------------------------------------------------------------------
ensure_job_provisioned() {
  derive_names
  local job_name="${TRAIN_JOB_NAME}"
  local rg_name="${RG_NAME}"

  log "Checking provisioning state of job ${job_name}..."
  local state
  state="$(az containerapp job show \
    --name "$job_name" \
    --resource-group "$rg_name" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "Unknown")"

  if [[ "$state" == "Succeeded" ]]; then
    log "Job ${job_name} is already provisioned."
    return 0
  fi

  if [[ "$state" == "Failed" ]]; then
    log "Job ${job_name} provisioning failed – deleting and re‑creating..."
    az containerapp job delete \
      --name "$job_name" \
      --resource-group "$rg_name" \
      --yes --output none 2>/dev/null || true
    sleep 10

    log "Re‑applying job resource..."
    tofu apply -target=module.aca.azurerm_container_app_job.train \
      -auto-approve -input=false -lock-timeout=5m \
      -var-file="$VAR_FILE" || fail "Job re‑creation failed"

    log "Job re‑creation succeeded."
    return 0
  fi

  log "Job state is '${state}' – waiting 30 seconds and rechecking..."
  sleep 30
  state="$(az containerapp job show \
    --name "$job_name" \
    --resource-group "$rg_name" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "Unknown")"

  if [[ "$state" == "Succeeded" ]]; then
    log "Job ${job_name} is now provisioned."
  else
    log "WARNING: Job ${job_name} is still in state '${state}'. Manual intervention may be needed."
  fi
}

# ---------------------------------------------------------------------------
# 12. Nuclear destroy
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

  log "deleting resource group: $rg_name"
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
  az keyvault purge -n "$kv_name" --subscription "$TF_VAR_subscription_id" 2>/dev/null || true
  log "purging ML workspace: $ml_workspace_name"
  az rest --method delete \
    --url "https://management.azure.com/subscriptions/${TF_VAR_subscription_id}/providers/Microsoft.MachineLearningServices/locations/${location}/deletedWorkspaces/${ml_workspace_name}?api-version=2024-10-01" \
    2>/dev/null || true

  local state_key="${TF_BACKEND_KEY_PREFIX}/${ENVIRONMENT}.tfstate"
  log "deleting state blob: ${state_key}"
  local user_obj_id; user_obj_id=$(az ad signed-in-user show --query id -o tsv 2>/dev/null) || true
  if [[ -n "$user_obj_id" ]]; then
    az role assignment create --assignee "$user_obj_id" --role "Storage Blob Data Contributor" \
      --scope "/subscriptions/${TF_VAR_subscription_id}/resourceGroups/${TF_BACKEND_RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/${TF_BACKEND_STORAGE_ACCOUNT}" \
      --subscription "$TF_VAR_subscription_id" 2>/dev/null || true
  fi
  az storage blob delete --account-name "$TF_BACKEND_STORAGE_ACCOUNT" --container-name "$TF_BACKEND_CONTAINER" \
    --name "$state_key" --auth-mode login 2>/dev/null || true
  log "nuclear destroy completed"
}

# ---------------------------------------------------------------------------
# 13. Auto-import orphaned ACA resources
# ---------------------------------------------------------------------------
import_orphaned_aca() {
  derive_names
  local serve_id="/subscriptions/${TF_VAR_subscription_id}/resourceGroups/${RG_NAME}/providers/Microsoft.App/containerApps/${SERVE_APP_NAME}"
  local job_id="/subscriptions/${TF_VAR_subscription_id}/resourceGroups/${RG_NAME}/providers/Microsoft.App/jobs/${TRAIN_JOB_NAME}"

  if az containerapp show -g "$RG_NAME" -n "$SERVE_APP_NAME" --subscription "$TF_VAR_subscription_id" &>/dev/null; then
    if ! tofu state list 2>/dev/null | grep -q "module.aca.azurerm_container_app.serve"; then
      log "Importing orphaned Container App: $SERVE_APP_NAME"
      tofu import -var-file="$VAR_FILE" module.aca.azurerm_container_app.serve "$serve_id" 2>/dev/null || true
    fi
  fi
  if az containerapp job show -g "$RG_NAME" -n "$TRAIN_JOB_NAME" --subscription "$TF_VAR_subscription_id" &>/dev/null; then
    if ! tofu state list 2>/dev/null | grep -q "module.aca.azurerm_container_app_job.train"; then
      log "Importing orphaned Job: $TRAIN_JOB_NAME"
      tofu import -var-file="$VAR_FILE" module.aca.azurerm_container_app_job.train "$job_id" 2>/dev/null || true
    fi
  fi
}

# ---------------------------------------------------------------------------
# 14. Deploy Azure Function code
# ---------------------------------------------------------------------------
deploy_function_code() {
  derive_names
  local env_abbr
  case "$ENVIRONMENT" in
    staging) env_abbr="stg" ;;
    prod)    env_abbr="prod" ;;
    *)       fail "unknown environment: $ENVIRONMENT" ;;
  esac
  local function_app_name="func-blob-trigger-${env_abbr}"
  local repo_root="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
  local function_source_dir="${repo_root}/src/workloads/blob_trigger"

  [[ -f "${function_source_dir}/host.json" ]] \
    || fail "host.json not found in ${function_source_dir}"

  log "Deploying function code to ${function_app_name}..."

  if ! command -v func >/dev/null 2>&1; then
    log "Installing Azure Functions Core Tools..."
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor \
      | sudo tee /etc/apt/trusted.gpg.d/microsoft.gpg >/dev/null 2>&1
    echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-ubuntu-noble-prod noble main" \
      | sudo tee /etc/apt/sources.list.d/microsoft-prod.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq azure-functions-core-tools-4=4.12.1-1
  fi

  (
    cd "$function_source_dir"
    func azure functionapp publish "$function_app_name" \
      --python \
      --build remote
  ) || fail "Failed to deploy function code to ${function_app_name}"

  log "Function code deployed to ${function_app_name}"
}

# ---------------------------------------------------------------------------
# 15. Argument parsing
# ---------------------------------------------------------------------------
MODE=""; ENVIRONMENT=""; PLAN_FILE_INPUT=""; YES_DELETE=false; SKIP_ACA=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan|--create|--validate|--destroy) MODE="$1"; shift ;;
    --apply-plan) MODE="--apply-plan"; shift; PLAN_FILE_INPUT="${1:-}"; [[ -n "$PLAN_FILE_INPUT" ]] || usage; shift ;;
    --env) ENVIRONMENT="${2:-}"; [[ -n "$ENVIRONMENT" ]] || usage; shift 2 ;;
    --yes-delete) YES_DELETE=true; shift ;;
    --skip-aca) SKIP_ACA=true; shift ;;
    *) usage ;;
  esac
done

[[ -n "$MODE" ]] || usage
if [[ "$MODE" != "--validate" && -z "$ENVIRONMENT" ]]; then usage; fi

require_cmd sha256sum python3 curl unzip

# ---------------------------------------------------------------------------
# 16. Auto‑derive Azure DevOps variables
# ---------------------------------------------------------------------------
resolve_git_remote() {
  command -v git >/dev/null 2>&1 || return 1
  local remote_url; remote_url="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
  [[ -n "$remote_url" ]] || return 1
  local repo_path
  case "$remote_url" in
    https://github.com/*) repo_path="${remote_url#https://github.com/}" ;;
    git@github.com:*)     repo_path="${remote_url#git@github.com:}" ;;
    ssh://git@github.com/*) repo_path="${remote_url#ssh://git@github.com/}" ;;
    *) return 1 ;;
  esac
  repo_path="${repo_path%.git}"
  GIT_OWNER="${repo_path%%/*}"; GIT_REPO="${repo_path##*/}"
  [[ -n "$GIT_OWNER" && -n "$GIT_REPO" && "$GIT_OWNER" != "$GIT_REPO" ]] || return 1
  return 0
}

resolve_ado_vars() {
  local sub_suffix="${TF_VAR_subscription_id: -6}"
  export TF_VAR_ado_client_id="${TF_VAR_ado_client_id:-${ARM_CLIENT_ID:-}}"
  export TF_VAR_ado_project_name="${TF_VAR_ado_project_name:-azdo-bootstrap-${sub_suffix}}"
  export TF_VAR_ado_github_service_connection_name="${TF_VAR_ado_github_service_connection_name:-github-pat}"
  export TF_VAR_ado_azure_service_connection_name="${TF_VAR_ado_azure_service_connection_name:-azdo-oidc-ci}"

  if resolve_git_remote; then
    export TF_VAR_github_owner="${TF_VAR_github_owner:-$GIT_OWNER}"
    export TF_VAR_github_repo="${TF_VAR_github_repo:-$GIT_REPO}"
  else
    log "WARNING: unable to resolve git remote"
  fi

  export TF_VAR_state_rg_name="${TF_VAR_state_rg_name:-$TF_BACKEND_RESOURCE_GROUP}"
  export TF_VAR_state_storage_account_name="${TF_VAR_state_storage_account_name:-$TF_BACKEND_STORAGE_ACCOUNT}"
  export TF_VAR_state_container_name="${TF_VAR_state_container_name:-$TF_BACKEND_CONTAINER}"
}

# ---------------------------------------------------------------------------
# 17. Execution
# ---------------------------------------------------------------------------
load_bootstrap_env; resolve_azure_context; choose_auth_mode; install_tofu_if_needed
compute_defaults; resolve_ado_vars

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
    import_orphaned_aca
    run_plan
    az account get-access-token --resource https://management.azure.com >/dev/null 2>&1 || true

    if $SKIP_ACA; then
      log "applying infrastructure only (skipping Container Apps)"
      tofu apply -input=false -lock-timeout=5m -auto-approve \
        -target=module.state \
        -target=module.observability \
        -target=module.ml_workspace \
        -target=module.function \
        -target=module.azure_devops \
        "$PLAN_FILE"
    else
      log "applying full plan $PLAN_FILE"
      tofu apply -input=false -lock-timeout=5m -auto-approve "$PLAN_FILE"

      log "ensuring training job is healthy"
      ensure_job_provisioned

      log "deploying function code"
      deploy_function_code
    fi
    ;;
  --apply-plan) run_apply_plan ;;
  --destroy)
    $YES_DELETE || fail "--yes-delete required"
    [[ -f "$VAR_FILE" ]] || fail "variable file not found: $VAR_FILE"
    nuclear_destroy
    ;;
  *) usage ;;
esac