#!/usr/bin/env bash
set -euo pipefail

# This script is intentionally split into named subcommands so the Azure
# Pipeline YAML stays small and readable.
#
# Each subcommand depends on environment variables injected by the pipeline.
# That keeps the script reusable while avoiding hardcoded repo-specific paths.

ROOT_DIR="${BUILD_SOURCES_DIR:?BUILD_SOURCES_DIR is required}"
ARTIFACT_STAGING_DIR="${BUILD_ARTIFACT_STAGING_DIR:?BUILD_ARTIFACT_STAGING_DIR is required}"
PIPELINE_WORKSPACE="${PIPELINE_WORKSPACE:?PIPELINE_WORKSPACE is required}"
SERVICE_DIRECTORY="${SERVICE_DIRECTORY:?SERVICE_DIRECTORY is required}"

REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-}"
DEV_REQUIREMENTS_FILE="${DEV_REQUIREMENTS_FILE:-}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

SECURITY_DIR="${SECURITY_DIR:-${ARTIFACT_STAGING_DIR}/security}"
TEST_RESULTS_FILE="${TEST_RESULTS_FILE:-${ROOT_DIR}/test-results.xml}"

CONTAINER_REGISTRY="${CONTAINER_REGISTRY:-}"
CONTAINER_REPOSITORY="${CONTAINER_REPOSITORY:-}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-}"

BUILD_SOURCE_BRANCH="${BUILD_SOURCE_BRANCH:-}"
BUILD_SOURCE_VERSION="${BUILD_SOURCE_VERSION:-}"
BUILD_BUILD_ID="${BUILD_BUILD_ID:-}"
MANIFEST_DIR="${MANIFEST_DIR:-${ARTIFACT_STAGING_DIR}/deploy-manifest}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: python_ci.sh <install-deps|ruff-lint|ruff-format|typecheck|run-tests|pip-audit|trivy-fs|docker-login|build-image|scan-image|push-image-and-manifest>
EOF
}

activate_venv() {
  local venv_activate="${ROOT_DIR}/.venv/bin/activate"

  # The template creates the venv during install-deps.
  # Later steps fail fast if that setup step was skipped or failed.
  if [[ ! -f "$venv_activate" ]]; then
    die "Virtual environment not found at ${ROOT_DIR}/.venv. Run install-deps first."
  fi

  # shellcheck disable=SC1090
  source "$venv_activate"
}

ensure_trivy() {
  # Trivy is downloaded on demand because hosted agents do not guarantee it.
  if [[ -x /tmp/trivy ]]; then
    return
  fi

  local trivy_version="0.72.0"

  curl -fsSL --retry 3 --retry-all-errors --connect-timeout 10 \
    -o /tmp/trivy.tar.gz \
    "https://github.com/aquasecurity/trivy/releases/download/v${trivy_version}/trivy_${trivy_version}_Linux-64bit.tar.gz"

  tar -xzf /tmp/trivy.tar.gz -C /tmp trivy
  chmod +x /tmp/trivy
}

require_container_vars() {
  # Docker-related subcommands need a registry, repository, and Dockerfile path.
  [[ -n "$CONTAINER_REGISTRY" ]] || die "CONTAINER_REGISTRY is required for Docker operations."
  [[ -n "$CONTAINER_REPOSITORY" ]] || die "CONTAINER_REPOSITORY is required for Docker operations."
  [[ -n "$DOCKERFILE_PATH" ]] || die "DOCKERFILE_PATH is required for Docker operations."
}

install_deps() {
  [[ -n "$REQUIREMENTS_FILE" ]] || die "REQUIREMENTS_FILE is required."
  [[ -n "$DEV_REQUIREMENTS_FILE" ]] || die "DEV_REQUIREMENTS_FILE is required."

  mkdir -p "${PIPELINE_WORKSPACE}/.cache/pip"

  # Create an isolated environment so dependency resolution is reproducible.
  python -m venv "${ROOT_DIR}/.venv"
  # shellcheck disable=SC1090
  source "${ROOT_DIR}/.venv/bin/activate"

  python -m pip install --upgrade pip setuptools wheel

  pip install --cache-dir "${PIPELINE_WORKSPACE}/.cache/pip" \
    -r "${ROOT_DIR}/${REQUIREMENTS_FILE}" \
    -r "${ROOT_DIR}/${DEV_REQUIREMENTS_FILE}"

  # Validate that installed packages have consistent metadata.
  python -m pip check
}

ruff_lint() {
  activate_venv
  ruff check "${ROOT_DIR}/${SERVICE_DIRECTORY}"
}

ruff_format() {
  activate_venv
  ruff format --check "${ROOT_DIR}/${SERVICE_DIRECTORY}"
}

typecheck() {
  activate_venv
  basedpyright "${ROOT_DIR}/${SERVICE_DIRECTORY}"
}

run_tests() {
  activate_venv
  pytest "${ROOT_DIR}/${SERVICE_DIRECTORY}/tests" \
    -v \
    --junitxml="${TEST_RESULTS_FILE}"
}

pip_audit() {
  activate_venv
  mkdir -p "${SECURITY_DIR}"
  pip-audit -f json -o "${SECURITY_DIR}/pip-audit.json"
}

trivy_fs() {
  ensure_trivy
  mkdir -p "${SECURITY_DIR}"

  /tmp/trivy fs \
    --scanners vuln \
    --format sarif \
    --output "${SECURITY_DIR}/trivy-fs.sarif" \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    "${ROOT_DIR}/${SERVICE_DIRECTORY}"
}

docker_login() {
  require_container_vars
  az acr login --name "${CONTAINER_REGISTRY}"
}

build_image() {
  require_container_vars

  export DOCKER_BUILDKIT=1

  local context_dir="${ROOT_DIR}/${SERVICE_DIRECTORY}"
  local image="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}:${BUILD_SOURCE_VERSION}"

  # Use a dedicated buildx builder so the job does not depend on the host state.
  docker buildx rm ci-builder >/dev/null 2>&1 || true
  docker buildx create --name ci-builder --use >/dev/null
  docker buildx inspect --bootstrap >/dev/null

  if [[ "${BUILD_SOURCE_BRANCH}" == "refs/heads/main" ]]; then
    # On main, publish and consume registry-backed cache layers.
    local cache_ref="${CONTAINER_REGISTRY}.azurecr.io/build-cache/${CONTAINER_REPOSITORY}:buildcache"

    docker buildx build \
      --platform linux/amd64 \
      --pull \
      --load \
      --cache-from type=registry,ref="${cache_ref}" \
      --cache-to type=registry,ref="${cache_ref}",mode=max \
      --label org.opencontainers.image.revision="${BUILD_SOURCE_VERSION}" \
      --file "${ROOT_DIR}/${DOCKERFILE_PATH}" \
      --tag "${image}" \
      "${context_dir}"
  else
    docker buildx build \
      --platform linux/amd64 \
      --pull \
      --load \
      --label org.opencontainers.image.revision="${BUILD_SOURCE_VERSION}" \
      --file "${ROOT_DIR}/${DOCKERFILE_PATH}" \
      --tag "${image}" \
      "${context_dir}"
  fi
}

scan_image() {
  require_container_vars
  ensure_trivy

  local image="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}:${BUILD_SOURCE_VERSION}"

  mkdir -p "${SECURITY_DIR}"

  /tmp/trivy image \
    --scanners vuln \
    --format sarif \
    --output "${SECURITY_DIR}/trivy-image.sarif" \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    "${image}"
}

push_image_and_manifest() {
  require_container_vars

  [[ -n "$BUILD_BUILD_ID" ]] || die "BUILD_BUILD_ID is required."
  [[ -n "$BUILD_SOURCE_VERSION" ]] || die "BUILD_SOURCE_VERSION is required."

  local image="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}:${BUILD_SOURCE_VERSION}"

  az acr login --name "${CONTAINER_REGISTRY}"
  docker push "${image}"

  mkdir -p "${MANIFEST_DIR}"

  # Try to get the digest from local Docker metadata first.
  # If the digest is absent, query ACR and resolve it from the pushed tag.
  local local_digest=""
  local_digest="$(docker image inspect "${image}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"

  local image_reference=""
  if [[ -n "${local_digest}" ]]; then
    image_reference="${local_digest}"
  else
    local manifest_json=""
    manifest_json="$(az acr repository show-manifests \
      --name "${CONTAINER_REGISTRY}" \
      --repository "${CONTAINER_REPOSITORY}" \
      --output json)"

    local digest=""
    digest="$(
      MANIFEST_JSON="${manifest_json}" IMAGE_TAG="${BUILD_SOURCE_VERSION}" python - <<'PY'
import json
import os

manifests = json.loads(os.environ["MANIFEST_JSON"])
image_tag = os.environ["IMAGE_TAG"]

for manifest in manifests:
    if image_tag in (manifest.get("tags") or []):
        print(manifest["digest"])
        break
else:
    raise SystemExit("Could not resolve pushed image digest from ACR")
PY
)"

    image_reference="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}@${digest}"
  fi

  # Build the manifest in Python so JSON formatting and quoting are reliable.
  IMAGE_REFERENCE="${image_reference}" BUILD_ID="${BUILD_BUILD_ID}" MANIFEST_DIR="${MANIFEST_DIR}" CONTAINER_REPOSITORY="${CONTAINER_REPOSITORY}" python - <<'PY'
import json
import os
from pathlib import Path

manifest_dir = Path(os.environ["MANIFEST_DIR"])
manifest_dir.mkdir(parents=True, exist_ok=True)

manifest = {
    "serviceName": os.environ["CONTAINER_REPOSITORY"],
    "imageReference": os.environ["IMAGE_REFERENCE"],
    "buildId": os.environ["BUILD_ID"],
}

(manifest_dir / "deploy-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
}

command="${1:-}"
[[ -n "${command}" ]] || { usage >&2; exit 1; }
shift || true

case "${command}" in
  install-deps)
    install_deps
    ;;
  ruff-lint)
    ruff_lint
    ;;
  ruff-format)
    ruff_format
    ;;
  typecheck)
    typecheck
    ;;
  run-tests)
    run_tests
    ;;
  pip-audit)
    pip_audit
    ;;
  trivy-fs)
    trivy_fs
    ;;
  docker-login)
    docker_login
    ;;
  build-image)
    build_image
    ;;
  scan-image)
    scan_image
    ;;
  push-image-and-manifest)
    push_image_and_manifest
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac