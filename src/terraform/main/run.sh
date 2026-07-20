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

# --- Always run from the script's directory ----------------------------------
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
  rm -f "$PLAN_FILE"
  prepare_stack
  tofu plan -input=false -lock-timeout=5m -var-file="$VAR_FILE" -out="$PLAN_FILE"
}

run_apply_plan() {
  [[ -f "$PLAN_FILE_INPUT" ]] || fail "plan file not found: $PLAN_FILE_INPUT"
  init_backend
  tofu apply -input=false -lock-timeout=5m -auto-approve "$PLAN_FILE_INPUT"
}

run_destroy() {
  init_backend
  tofu destroy -input=false -lock-timeout=5m -auto-approve -var-file="$VAR_FILE"
}

# ---------------------------------------------------------------------------
# 10. Argument parsing
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
# 11. Execution order – subscription resolved before backend naming
# ---------------------------------------------------------------------------
load_bootstrap_env
resolve_azure_context        # sets TF_VAR_subscription_id, TF_VAR_tenant_id
choose_auth_mode
install_tofu_if_needed
compute_defaults              # needs TF_VAR_subscription_id for suffix

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
    run_plan
    log "applying plan $PLAN_FILE"
    tofu apply -input=false -lock-timeout=5m -auto-approve "$PLAN_FILE"
    ;;
  --apply-plan) run_apply_plan ;;
  --destroy)
    $YES_DELETE || fail "--yes-delete required"
    [[ -f "$VAR_FILE" ]] || fail "variable file not found: $VAR_FILE"
    run_destroy
    ;;
  *) usage ;;
esac