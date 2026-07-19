#!/usr/bin/env bash
# set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUNBUFFERED=1

AZURE_CLI_VERSION="2.88.0"
PYENV_TAG="v2.7.3"
PYTHON_VERSION="3.14.6"
PYTEST_VERSION="9.0.3"
PRE_COMMIT_VERSION="4.2.0"
OPENTOFU_VERSION="1.12.0"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

log() {
    printf '\n[%s] %s\n' "$(date +'%H:%M:%S')" "$*"
}

die() {
    printf '[%s] ERROR: %s\n' "$(date +'%H:%M:%S')" "$*" >&2
    exit 1
}

trap 'die "Command failed at line $LINENO: $BASH_COMMAND"' ERR

ensure_line() {
    local file="$1"
    local line="$2"

    mkdir -p "$(dirname "$file")"
    touch "$file"
    grep -qxF "$line" "$file" || printf '%s\n' "$line" >>"$file"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

install_base_packages() {
    log "Installing base packages..."

    "${SUDO[@]}" apt-get update -qq
    "${SUDO[@]}" apt-get install -y -qq --no-install-recommends \
        apt-transport-https \
        build-essential \
        ca-certificates \
        curl \
        git \
        gnupg \
        jq \
        libbz2-dev \
        libffi-dev \
        libgdbm-dev \
        liblzma-dev \
        libncursesw5-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        lsb-release \
        make \
        pkg-config \
        tk-dev \
        tree \
        unzip \
        uuid-dev \
        vim \
        xz-utils \
        zlib1g-dev \
        zstd
}

install_azure_cli() {
    local dist arch current
    local keyring="/etc/apt/keyrings/microsoft.gpg"
    local sources="/etc/apt/sources.list.d/azure-cli.sources"
    local expected_pkg_version="${AZURE_CLI_VERSION}-1~"

    . /etc/os-release
    dist="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
    [[ -n "$dist" ]] || die "Unable to determine distro codename from /etc/os-release"
    arch="$(dpkg --print-architecture)"

    if command -v az >/dev/null 2>&1; then
        current="$(az version --query '"azure-cli"' -o tsv 2>/dev/null || true)"
        if [[ "$current" == "$AZURE_CLI_VERSION" ]]; then
            log "Azure CLI ${AZURE_CLI_VERSION} already installed."
            return
        fi
    fi

    log "Installing Azure CLI ${AZURE_CLI_VERSION}..."

    "${SUDO[@]}" install -d -m 0755 /etc/apt/keyrings
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc |
        gpg --dearmor |
        "${SUDO[@]}" tee "$keyring" >/dev/null
    "${SUDO[@]}" chmod 0644 "$keyring"

    "${SUDO[@]}" tee "$sources" >/dev/null <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: ${dist}
Components: main
Architectures: ${arch}
Signed-By: ${keyring}
EOF
    "${SUDO[@]}" chmod 0644 "$sources"

    "${SUDO[@]}" apt-get update -qq

    if ! apt-cache madison azure-cli | awk -v want="$expected_pkg_version" '$3 ~ "^" want "[0-9A-Za-z.~:-]*$" { found=1 } END { exit(found ? 0 : 1) }'; then
        die "Azure CLI ${AZURE_CLI_VERSION} package not found for ${dist}"
    fi

    "${SUDO[@]}" apt-get install -y \
        --allow-downgrades \
        --allow-change-held-packages \
        "azure-cli=${AZURE_CLI_VERSION}-1~${dist}"

    current="$(az version --query '"azure-cli"' -o tsv)"
    [[ "$current" == "$AZURE_CLI_VERSION" ]] || die "Azure CLI version check failed: expected ${AZURE_CLI_VERSION}, got ${current}"

    log "Azure CLI ${current} installed."
}

install_pyenv() {
    local pyenv_root="${PYENV_ROOT:-$HOME/.pyenv}"

    if [[ -d "$pyenv_root/.git" ]]; then
        log "Updating pyenv to ${PYENV_TAG}..."
        git -C "$pyenv_root" fetch --tags --force origin
        git -C "$pyenv_root" checkout -q "$PYENV_TAG"
        git -C "$pyenv_root" reset --hard "$PYENV_TAG" >/dev/null
    else
        log "Installing pyenv ${PYENV_TAG}..."
        git clone --branch "$PYENV_TAG" --depth 1 https://github.com/pyenv/pyenv.git "$pyenv_root"
    fi

    export PYENV_ROOT="$pyenv_root"
    export PATH="$PYENV_ROOT/bin:$PATH"

    require_cmd pyenv
    eval "$(pyenv init - bash)"

    ensure_line "$HOME/.bashrc" 'export PYENV_ROOT="$HOME/.pyenv"'
    ensure_line "$HOME/.bashrc" '[ -d "$PYENV_ROOT/bin" ] && export PATH="$PYENV_ROOT/bin:$PATH"'
    ensure_line "$HOME/.bashrc" 'eval "$(pyenv init - bash)"'

    ensure_line "$HOME/.profile" '[ -n "$BASH_VERSION" ] && [ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"'

    log "pyenv ready: $(pyenv --version)"
}

install_python() {
    local python_bin

    log "Installing Python ${PYTHON_VERSION} with pyenv..."

    pyenv install -s "$PYTHON_VERSION"

    python_bin="$PYENV_ROOT/versions/$PYTHON_VERSION/bin/python"
    [[ -x "$python_bin" ]] || die "Python binary not found after install: $python_bin"

    export PATH="$(dirname "$python_bin"):$PATH"
    export PYTHON_BIN="$python_bin"

    "$PYTHON_BIN" --version | grep -qx "Python ${PYTHON_VERSION}" || die "Installed Python does not match ${PYTHON_VERSION}"
    pyenv global "$PYTHON_VERSION"
    "$PYTHON_BIN" -m ensurepip --upgrade
    "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
    pyenv rehash

    log "Python $($PYTHON_BIN --version 2>&1) ready."
}

install_python_tools() {
    log "Installing Python tools..."

    "$PYTHON_BIN" -m pip install \
        "pytest==${PYTEST_VERSION}" \
        "pre-commit==${PRE_COMMIT_VERSION}"

    require_cmd pytest
    require_cmd pre-commit
}

configure_precommit() {
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "Installing pre-commit hooks..."
        pre-commit install --install-hooks
    else
        log "Not a Git repository; skipping pre-commit."
    fi
}

install_opentofu() {
    local current pkg_version
    local keyring1="/etc/apt/keyrings/opentofu.gpg"
    local keyring2="/etc/apt/keyrings/opentofu-repo.gpg"
    local sources="/etc/apt/sources.list.d/opentofu.list"

    if command -v tofu >/dev/null 2>&1; then
        current="$(tofu version | head -1 | awk '{print $NF}' | sed 's/^v//')"
        if [[ "$current" == "$OPENTOFU_VERSION" ]]; then
            log "OpenTofu ${OPENTOFU_VERSION} already installed."
            return
        fi
    fi

    log "Installing OpenTofu ${OPENTOFU_VERSION}..."

    "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://get.opentofu.org/opentofu.gpg | "${SUDO[@]}" tee "$keyring1" >/dev/null
    curl -fsSL https://packages.opentofu.org/opentofu/tofu/gpgkey |
        "${SUDO[@]}" gpg --no-tty --batch --dearmor -o "$keyring2" >/dev/null
    "${SUDO[@]}" chmod a+r "$keyring1" "$keyring2"

    "${SUDO[@]}" tee "$sources" >/dev/null <<EOF
deb [signed-by=${keyring1},${keyring2}] https://packages.opentofu.org/opentofu/tofu/any/ any main
deb-src [signed-by=${keyring1},${keyring2}] https://packages.opentofu.org/opentofu/tofu/any/ any main
EOF
    "${SUDO[@]}" chmod a+r "$sources"

    "${SUDO[@]}" apt-get update -qq

    pkg_version="$(apt-cache madison tofu | awk -v v="$OPENTOFU_VERSION" '$3 ~ "^" v "(-|$)" {print $3; exit}')"
    [[ -n "$pkg_version" ]] || die "OpenTofu ${OPENTOFU_VERSION} not found in apt cache"

    "${SUDO[@]}" apt-get install -y "tofu=${pkg_version}"

    current="$(tofu version | head -1 | awk '{print $NF}' | sed 's/^v//')"
    [[ "$current" == "$OPENTOFU_VERSION" ]] || die "OpenTofu version check failed: expected ${OPENTOFU_VERSION}, got ${current}"

    log "OpenTofu ${current} installed."
}

print_versions() {
    echo
    echo "Versions:"
    echo "  Python     : $($PYTHON_BIN --version 2>&1)"
    echo "  Pip        : $($PYTHON_BIN -m pip --version | awk '{print $2}')"
    echo "  Azure CLI  : $(az version --query '"azure-cli"' -o tsv)"
    echo "  OpenTofu   : $(tofu version | head -1)"
    echo "  Pytest     : $(pytest --version)"
    echo "  Pre-commit : $(pre-commit --version)"
}

main() {
    install_base_packages
    install_azure_cli
    install_pyenv
    install_python
    install_python_tools
    configure_precommit
    install_opentofu

    log "Bootstrap completed successfully."
    print_versions
}

main "$@"