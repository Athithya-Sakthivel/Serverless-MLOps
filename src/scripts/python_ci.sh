#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# python_ci.sh – Single execution layer for Python CI.
#
# This script is the ONLY place where CI commands live. The pipeline YAML
# should do orchestration only: select Python, restore caches, and call one
# of the subcommands below.
#
# Design decisions:
#   - Two cache layers: pip download cache (saves network) and virtualenv
#     cache (saves install time). They are stored separately so that a
#     virtualenv hit does not force re-downloading, and vice versa.
#   - Dependency signature computed from the actual content of requirements
#     files. When the signature matches a cached virtualenv, the install
#     step becomes a ~1-second no-op.
#   - No `pip install --upgrade pip` on every run. Those upgrades add work
#     without changing the dependency graph.
#   - All subcommands are idempotent and fail-fast with clear error messages.
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Required environment variables (set by the pipeline YAML)
# ---------------------------------------------------------------------------
ROOT_DIR="${BUILD_SOURCESDIRECTORY:?BUILD_SOURCESDIRECTORY is required}"
ARTIFACT_STAGING_DIR="${BUILD_ARTIFACTSTAGINGDIRECTORY:?BUILD_ARTIFACTSTAGINGDIRECTORY is required}"
PIPELINE_WORKSPACE="${PIPELINE_WORKSPACE:?PIPELINE_WORKSPACE is required}"
SERVICE_DIRECTORY="${SERVICE_DIRECTORY:?SERVICE_DIRECTORY is required}"

# Optional overrides
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-}"
DEV_REQUIREMENTS_FILE="${DEV_REQUIREMENTS_FILE:-}"
SECURITY_DIR="${SECURITY_DIR:-${ARTIFACT_STAGING_DIR}/security}"
TEST_RESULTS_FILE="${TEST_RESULTS_FILE:-${ROOT_DIR}/test-results.xml}"
CONTAINER_REGISTRY="${CONTAINER_REGISTRY:-}"
CONTAINER_REPOSITORY="${CONTAINER_REPOSITORY:-}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-}"
BUILD_SOURCE_BRANCH="${BUILD_SOURCEBRANCH:-}"
BUILD_SOURCE_VERSION="${BUILD_SOURCEVERSION:-}"
BUILD_BUILD_ID="${BUILD_BUILDID:-}"
MANIFEST_DIR="${MANIFEST_DIR:-${ARTIFACT_STAGING_DIR}/deploy-manifest}"

# Cache directories live outside the source tree so the checkout stays clean.
PIP_CACHE_DIR="${PIP_CACHE_DIR:-${PIPELINE_WORKSPACE}/pip-cache}"
VENV_DIR="${VENV_DIR:-${PIPELINE_WORKSPACE}/.venv}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: python_ci.sh <command>

Commands:
  install-deps              Create/verify virtualenv with all dependencies.
  ruff-lint                 Run ruff linter.
  ruff-format               Check formatting with ruff.
  typecheck                 Run basedpyright type checker.
  run-tests                 Run pytest and produce JUnit XML.
  pip-audit                 Audit dependencies for known vulnerabilities.
  trivy-fs                  Scan filesystem for vulnerabilities.
  docker-login              Authenticate to Azure Container Registry.
  build-image               Build Docker image.
  scan-image                Scan Docker image with Trivy.
  push-image-and-manifest   Push image to ACR and publish deploy manifest.
EOF
}

require_file() {
    local file_path="$1"
    [[ -f "$file_path" ]] || die "Missing required file: ${file_path}"
}

# Return the full list of requirement files. If dev requirements are the
# same file as runtime requirements, emit it only once to avoid duplicate
# pip arguments and duplicate hashing work.
dependency_files() {
    local base_requirements="${ROOT_DIR}/${REQUIREMENTS_FILE}"
    require_file "${base_requirements}"
    printf '%s\n' "${base_requirements}"

    if [[ -n "${DEV_REQUIREMENTS_FILE}" && "${DEV_REQUIREMENTS_FILE}" != "${REQUIREMENTS_FILE}" ]]; then
        local dev_requirements="${ROOT_DIR}/${DEV_REQUIREMENTS_FILE}"
        require_file "${dev_requirements}"
        printf '%s\n' "${dev_requirements}"
    fi
}

# Build a SHA-256 digest over the concatenated contents of all requirement
# files. This is the local equivalent of Azure Cache@2 file-content-based
# key segments. When the digest matches, the cached virtualenv is still valid.
dependency_signature() {
    local -a files=()
    mapfile -t files < <(dependency_files)

    python - "${files[@]}" <<'PY'
import hashlib
import pathlib
import sys

paths = []
for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path)
    if path not in paths:
        paths.append(path)

digest = hashlib.sha256()
for path in paths:
    digest.update(path.read_bytes())
    digest.update(b"\0")

print(digest.hexdigest())
PY
}

# Activate the virtualenv. Fails loudly if the venv does not exist yet.
activate_venv() {
    local venv_activate="${VENV_DIR}/bin/activate"
    if [[ ! -f "${venv_activate}" ]]; then
        die "Virtual environment not found at ${VENV_DIR}. Run install-deps first."
    fi
    # shellcheck disable=SC1090
    source "${venv_activate}"
}

# Download Trivy on first use. Hosted agents do not guarantee it is present.
ensure_trivy() {
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
    [[ -n "${CONTAINER_REGISTRY}" ]] || die "CONTAINER_REGISTRY is required for Docker operations."
    [[ -n "${CONTAINER_REPOSITORY}" ]] || die "CONTAINER_REPOSITORY is required for Docker operations."
    [[ -n "${DOCKERFILE_PATH}" ]] || die "DOCKERFILE_PATH is required for Docker operations."
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

install_deps() {
    [[ -n "${REQUIREMENTS_FILE}" ]] || die "REQUIREMENTS_FILE is required."
    [[ -n "${DEV_REQUIREMENTS_FILE}" ]] || die "DEV_REQUIREMENTS_FILE is required."

    mkdir -p "${PIP_CACHE_DIR}" "${VENV_DIR}"

    local signature_file="${VENV_DIR}/.dependency-signature"
    local expected_signature
    expected_signature="$(dependency_signature)"

    # Fast path: if a cached virtualenv matches the current dependency
    # signature, do not reinstall anything. Just validate and return.
    if [[ -x "${VENV_DIR}/bin/python" && -f "${signature_file}" ]]; then
        local cached_signature
        cached_signature="$(tr -d '\n' < "${signature_file}")"
        if [[ "${cached_signature}" == "${expected_signature}" ]]; then
            echo "Virtualenv matches dependency signature; skipping reinstall."
            activate_venv
            python -m pip check
            return
        fi
    fi

    # Slow path: rebuild the virtualenv from scratch. pip's download cache
    # is kept so that future installs (even on cache miss) are faster.
    rm -rf "${VENV_DIR}"
    python -m venv "${VENV_DIR}"
    activate_venv

    local -a pip_requirements=()
    while IFS= read -r requirement_file; do
        pip_requirements+=(-r "${requirement_file}")
    done < <(dependency_files)

    python -m pip install \
        --disable-pip-version-check \
        --no-input \
        --prefer-binary \
        --cache-dir "${PIP_CACHE_DIR}" \
        "${pip_requirements[@]}"

    python -m pip check
    printf '%s\n' "${expected_signature}" > "${signature_file}"
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

    # Use a dedicated builder so the job does not depend on host state.
    docker buildx rm ci-builder >/dev/null 2>&1 || true
    docker buildx create --name ci-builder --use >/dev/null
    docker buildx inspect --bootstrap >/dev/null

    if [[ "${BUILD_SOURCE_BRANCH}" == "refs/heads/main" ]]; then
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
    [[ -n "${BUILD_BUILD_ID}" ]] || die "BUILD_BUILD_ID is required."
    [[ -n "${BUILD_SOURCE_VERSION}" ]] || die "BUILD_SOURCE_VERSION is required."

    local image="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}:${BUILD_SOURCE_VERSION}"
    az acr login --name "${CONTAINER_REGISTRY}"
    docker push "${image}"

    mkdir -p "${MANIFEST_DIR}"

    # Try local Docker digest first; fall back to ACR manifest lookup.
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
import json, os, sys
manifests = json.loads(os.environ["MANIFEST_JSON"])
image_tag = os.environ["IMAGE_TAG"]
for manifest in manifests:
    if image_tag in (manifest.get("tags") or []):
        print(manifest["digest"])
        break
else:
    sys.exit("Could not resolve pushed image digest from ACR")
PY
        )"
        image_reference="${CONTAINER_REGISTRY}.azurecr.io/${CONTAINER_REPOSITORY}@${digest}"
    fi

    # Write the manifest using Python for reliable JSON formatting.
    IMAGE_REFERENCE="${image_reference}" BUILD_ID="${BUILD_BUILD_ID}" MANIFEST_DIR="${MANIFEST_DIR}" CONTAINER_REPOSITORY="${CONTAINER_REPOSITORY}" python - <<'PY'
import json, os
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

# ---------------------------------------------------------------------------
# Entry point – dispatch on first argument
# ---------------------------------------------------------------------------
command="${1:-}"
[[ -n "${command}" ]] || { usage >&2; exit 1; }
shift || true

case "${command}" in
    install-deps)              install_deps ;;
    ruff-lint)                 ruff_lint ;;
    ruff-format)               ruff_format ;;
    typecheck)                 typecheck ;;
    run-tests)                 run_tests ;;
    pip-audit)                 pip_audit ;;
    trivy-fs)                  trivy_fs ;;
    docker-login)              docker_login ;;
    build-image)               build_image ;;
    scan-image)                scan_image ;;
    push-image-and-manifest)   push_image_and_manifest ;;
    *)                         usage >&2; exit 1 ;;
esac