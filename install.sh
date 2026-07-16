#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────
# SteamCast — one-line installer
# Usage: curl -fsSL https://…/install.sh | bash
# ──────────────────────────────────────────────────────────

REPO="https://github.com/underagum/steamcast.git"
INSTALL_DIR="$HOME/.steamcast"
BIN_DIR="$HOME/.local/bin"
BIN_SCRIPT="$BIN_DIR/steamcast"

RED='\033[31m'; GREEN='\033[32m'; CYAN='\033[36m'; BOLD='\033[1m'; RESET='\033[0m'

echo -e "${BOLD}${CYAN}⚡ SteamCast Installer${RESET}\n"

# ── Check prerequisites ──
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ Python 3 not found. Install it first:${RESET}"
    echo "  sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  ${GREEN}✓${RESET} Python $PY_VER found"

if ! command -v git &>/dev/null; then
    echo -e "${RED}✗ git not found. Install it first:${RESET}"
    echo "  sudo apt install git"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} git found"

# ── Clone / update ──
if [ -d "$INSTALL_DIR" ]; then
    echo -e "\n  Updating SteamCast..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main
else
    echo -e "\n  Cloning SteamCast..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── Create venv + install deps ──
if [ ! -d "venv" ]; then
    echo -e "  Creating virtual environment..."
    python3 -m venv venv
fi

echo -e "  Installing dependencies..."
./venv/bin/pip install -q rich psutil 2>&1 | tail -1

# ── Create launcher ──
mkdir -p "$BIN_DIR"

cat > "$BIN_SCRIPT" << 'LAUNCHER'
#!/usr/bin/env bash
export STEAMCAST_HOME="${STEAMCAST_HOME:-$HOME/.steamcast}"
cd "$STEAMCAST_HOME" || exit 1
exec "$STEAMCAST_HOME/venv/bin/python3" steamcast.py "$@"
LAUNCHER

chmod 755 "$BIN_SCRIPT"

# ── Verify PATH ──
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$BIN_DIR"; then
    echo -e "\n  ${CYAN}⚠ Add this to your ~/.bashrc or ~/.zshrc:${RESET}"
    echo -e "    ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

echo ""
echo -e "${BOLD}${GREEN}✅ SteamCast installed!${RESET}"
echo ""
echo -e "  Run:  ${CYAN}steamcast${RESET}"
echo -e "  Daemon: ${CYAN}steamcast daemon start${RESET}"
echo -e "  Attach: ${CYAN}steamcast attach${RESET}"
echo ""
echo -e "  ${BOLD}Support us:${RESET}"
echo -e "  🛒 ${CYAN}https://store.steampowered.com/developer/DH/${RESET}"
echo -e "  ❤️  ${CYAN}https://store.steampowered.com/app/3411060/Broomstick_Exorcist/${RESET}"
echo -e "  👁️  ${CYAN}https://store.steampowered.com/app/3500810/DreadOut_3/${RESET}"
