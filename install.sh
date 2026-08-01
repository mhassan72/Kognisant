#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Kognisant CLI Installer
# https://github.com/mhassan72/Kognisant
#
# This script installs the Kognisant CLI tool into an isolated Python virtual
# environment. It does NOT modify your system Python or install packages globally.
#
# What this script does:
#   1. Checks for Python 3.10+ and git
#   2. Clones the repository to ~/.kognisant_install/
#   3. Creates a virtual environment at ~/.kognisant_install/venv/
#   4. Installs the package inside that venv
#   5. Creates a symlink in ~/.local/bin/ (or prints PATH instructions)
#
# What this script does NOT do:
#   - Install anything with sudo or as root
#   - Modify system Python packages
#   - Write outside of ~/.kognisant_install/ and ~/.local/bin/
#   - Send any data to the internet (beyond git clone)
#
# Uninstall: rm -rf ~/.kognisant_install ~/.local/bin/kognisant
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
#
# Or download and inspect first:
#   curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh -o install.sh
#   less install.sh
#   sh install.sh
#
# Alternative (pip from git, no script needed):
#   pip install git+https://github.com/mhassan72/Kognisant.git
#
# ─────────────────────────────────────────────────────────────────────────────
set -e

REPO="https://github.com/mhassan72/Kognisant.git"
INSTALL_DIR="${KOGNISANT_INSTALL_DIR:-$HOME/.kognisant_install}"
VENV_DIR="$INSTALL_DIR/venv"
SYMLINK_DIR="$HOME/.local/bin"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

# ─── Colors (disabled if not a terminal) ──────────────────────────────────────

if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' CYAN='' BOLD='' DIM='' RESET=''
fi

info()    { printf "${CYAN}▸${RESET} %s\n" "$1"; }
success() { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn()    { printf "${YELLOW}⚠${RESET} %s\n" "$1"; }
error()   { printf "${RED}✗ Error:${RESET} %s\n" "$1"; exit 1; }

# ─── Safety: refuse to run as root ────────────────────────────────────────────

if [ "$(id -u)" -eq 0 ]; then
    error "Do not run this installer as root. It installs to your home directory only."
fi

# ─── Platform Check ───────────────────────────────────────────────────────────

OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    MINGW*|MSYS*|CYGWIN*)
        error "Windows is not supported. Use WSL (Windows Subsystem for Linux) instead." ;;
    *)
        error "Unsupported platform: $OS. Kognisant requires Linux or macOS." ;;
esac

# ─── Python Check ─────────────────────────────────────────────────────────────

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        if [ -n "$version" ]; then
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
                PYTHON="$cmd"
                break
            fi
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but not found.
  Install from: https://www.python.org/downloads/
  macOS:  brew install python@3.12
  Ubuntu: sudo apt install python3.12 python3.12-venv
  Fedora: sudo dnf install python3.12"
fi

info "Found Python: $($PYTHON --version 2>&1) at $(command -v $PYTHON)"

# ─── Verify venv module is available ──────────────────────────────────────────

if ! "$PYTHON" -c "import venv" 2>/dev/null; then
    error "Python venv module is not available.
  Install it:
    Ubuntu/Debian: sudo apt install python3-venv
    Fedora: sudo dnf install python3-venv
    macOS: (included with python3 from Homebrew or python.org)"
fi

# ─── Git Check ────────────────────────────────────────────────────────────────

if ! command -v git >/dev/null 2>&1; then
    error "git is required but not found.
  Install from: https://git-scm.com/
  macOS:  xcode-select --install
  Ubuntu: sudo apt install git"
fi

# ─── Confirm Installation ─────────────────────────────────────────────────────

printf "\n${BOLD}🧠 Kognisant CLI Installer${RESET}\n\n"
printf "  Install location:  ${CYAN}%s${RESET}\n" "$INSTALL_DIR"
printf "  Virtual env:       ${CYAN}%s${RESET}\n" "$VENV_DIR"
printf "  Command symlink:   ${CYAN}%s/kognisant${RESET}\n" "$SYMLINK_DIR"
printf "  Python:            ${CYAN}%s${RESET}\n\n" "$($PYTHON --version 2>&1)"

# ─── Clone or Update Repository ───────────────────────────────────────────────

if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" fetch --quiet origin main 2>/dev/null || true
    git -C "$INSTALL_DIR" reset --quiet --hard origin/main 2>/dev/null || {
        warn "Update failed. Re-cloning..."
        rm -rf "$INSTALL_DIR"
        git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
    }
    success "Repository updated"
else
    if [ -d "$INSTALL_DIR" ]; then
        warn "Install directory exists but isn't a git repo. Removing and re-cloning..."
        rm -rf "$INSTALL_DIR"
    fi
    info "Cloning repository..."
    git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
    success "Repository cloned"
fi

# ─── Create or Reuse Virtual Environment ─────────────────────────────────────

if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
    info "Using existing virtual environment"
else
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "Virtual environment created at $VENV_DIR"
fi

# Activate venv for this script
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Upgrade pip inside venv (suppress output)
"$VENV_PYTHON" -m pip install --quiet --upgrade pip 2>/dev/null || true

# ─── Install Package in Venv ──────────────────────────────────────────────────

info "Installing Kognisant into virtual environment..."
"$VENV_PIP" install --quiet -e "$INSTALL_DIR" 2>&1 | grep -v "already satisfied" || true
success "Package installed"

# ─── Verify Installation Inside Venv ──────────────────────────────────────────

if [ ! -f "$VENV_DIR/bin/kognisant" ]; then
    error "Installation failed: kognisant command not found in venv.
  Try manually: $VENV_PIP install -e $INSTALL_DIR"
fi

# Test it works
"$VENV_DIR/bin/kognisant" --help >/dev/null 2>&1 || {
    error "Installation failed: kognisant command exits with error.
  Try manually: $VENV_DIR/bin/kognisant --help"
}

# ─── Create Symlink ───────────────────────────────────────────────────────────

mkdir -p "$SYMLINK_DIR"

# Remove old symlink if it points somewhere else
if [ -L "$SYMLINK_DIR/kognisant" ]; then
    rm -f "$SYMLINK_DIR/kognisant"
fi

ln -sf "$VENV_DIR/bin/kognisant" "$SYMLINK_DIR/kognisant"
success "Symlink created: $SYMLINK_DIR/kognisant → $VENV_DIR/bin/kognisant"

# ─── PATH Check ──────────────────────────────────────────────────────────────

if command -v kognisant >/dev/null 2>&1; then
    success "kognisant is available in your PATH"
elif echo "$PATH" | grep -q "$SYMLINK_DIR"; then
    success "kognisant installed (restart your shell to use it)"
else
    warn "$SYMLINK_DIR is not in your PATH"
    printf "\n  Add this to your shell config (~/.zshrc, ~/.bashrc, or ~/.profile):\n\n"
    printf "    ${CYAN}export PATH=\"%s:\$PATH\"${RESET}\n\n" "$SYMLINK_DIR"
    printf "  Then restart your shell or run: ${CYAN}source ~/.zshrc${RESET}\n\n"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

printf "\n${GREEN}${BOLD}Installation complete!${RESET}\n\n"
printf "  ${BOLD}Get started:${RESET}\n"
printf "    ${CYAN}kognisant login${RESET}     Authenticate with Kognisant Cloud\n"
printf "    ${CYAN}kognisant init${RESET}      Initialize project memory\n"
printf "    ${CYAN}kognisant chat${RESET}      Start an AI session\n"
printf "    ${CYAN}kognisant status${RESET}    Check workspace health\n"
printf "\n"
printf "  ${DIM}Uninstall: rm -rf %s %s/kognisant${RESET}\n" "$INSTALL_DIR" "$SYMLINK_DIR"
printf "  ${DIM}Support:   support@kognisant.xyz${RESET}\n"
printf "\n"
