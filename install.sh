#!/bin/sh
# Kognisant Installer
# https://github.com/mhassan72/Kognisant
#
# Usage: curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
set -e

REPO="https://github.com/mhassan72/Kognisant.git"
INSTALL_DIR="${KOGNISANT_INSTALL_DIR:-$HOME/.kognisant_install}"
MIN_PYTHON_VERSION="3.10"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info() { printf "${CYAN}▸${RESET} %s\n" "$1"; }
success() { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}!${RESET} %s\n" "$1"; }
error() { printf "${RED}✗${RESET} %s\n" "$1"; exit 1; }

# ─── Platform Check ───────────────────────────────────────────

OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) error "Unsupported platform: $OS. Kognisant requires Linux or macOS." ;;
esac

# ─── Python Check ─────────────────────────────────────────────

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.10+ is required but not found. Install it from https://www.python.org/downloads/"
fi

info "Found $PYTHON ($version)"

# ─── pip Check ────────────────────────────────────────────────

PIP=""
for cmd in pip3 pip; do
    if command -v "$cmd" >/dev/null 2>&1; then
        PIP="$cmd"
        break
    fi
done

if [ -z "$PIP" ]; then
    # Try the module approach
    if "$PYTHON" -m pip --version >/dev/null 2>&1; then
        PIP="$PYTHON -m pip"
    else
        error "pip is required but not found. Install it: $PYTHON -m ensurepip --upgrade"
    fi
fi

# ─── Git Check ────────────────────────────────────────────────

if ! command -v git >/dev/null 2>&1; then
    error "git is required but not found. Install it from https://git-scm.com/"
fi

# ─── Install ──────────────────────────────────────────────────

printf "\n${BOLD}🧠 Installing Kognisant${RESET}\n\n"

if [ -d "$INSTALL_DIR" ]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --quiet origin main 2>/dev/null || {
        warn "Pull failed. Re-cloning..."
        rm -rf "$INSTALL_DIR"
        git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
    }
else
    info "Cloning repository..."
    git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
fi

info "Installing package..."
$PIP install --quiet --user -e "$INSTALL_DIR" 2>/dev/null || \
$PIP install --quiet -e "$INSTALL_DIR"

# ─── Verify ───────────────────────────────────────────────────

if command -v kognisant >/dev/null 2>&1; then
    success "Kognisant installed successfully"
else
    # Check if user site-packages bin is in PATH
    USER_BIN=$("$PYTHON" -m site --user-base 2>/dev/null)/bin
    if [ -f "$USER_BIN/kognisant" ]; then
        warn "Installed but not in PATH. Add this to your shell config:"
        printf "\n  export PATH=\"%s:\$PATH\"\n\n" "$USER_BIN"
        success "Kognisant installed at $USER_BIN/kognisant"
    else
        success "Kognisant installed. You may need to restart your shell."
    fi
fi

# ─── Done ─────────────────────────────────────────────────────

printf "\n${BOLD}Get started:${RESET}\n"
printf "  ${CYAN}kognisant init${RESET}    Initialize project memory\n"
printf "  ${CYAN}kognisant chat${RESET}    Start an AI session\n"
printf "\n"
