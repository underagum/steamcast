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
for cmd in python3 git; do
    if ! command -v "$cmd" &>/dev/null; then
        echo -e "${RED}✗ $cmd not found. Install it first.${RESET}"
        exit 1
    fi
done
echo -e "  ${GREEN}✓${RESET} python3 + git found"

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

# ── Install dependencies ──
echo -e "  Installing dependencies..."
pip3 install --break-system-packages -q -r requirements.txt 2>/dev/null || \
    pip3 install --user -q -r requirements.txt 2>/dev/null || \
    pip3 install -q -r requirements.txt

# ── Create launcher ──
mkdir -p "$BIN_DIR"

cat > "$BIN_SCRIPT" << 'LAUNCHER'
#!/usr/bin/env bash
export STEAMCAST_HOME="${STEAMCAST_HOME:-$HOME/.steamcast}"
cd "$STEAMCAST_HOME" || exit 1
exec python3 steamcast.py "$@"
LAUNCHER

chmod 755 "$BIN_SCRIPT"

# ── Verify PATH ──
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$BIN_DIR"; then
    echo -e "\n  ${CYAN}⚠ Add this to your ~/.bashrc:${RESET}"
    echo -e "    ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

echo ""
echo -e "${BOLD}${GREEN}✅ SteamCast installed!${RESET}"
echo ""
echo -e "  Run:   ${CYAN}steamcast${RESET}"
echo -e "  Update: ${CYAN}steamcast update${RESET}"
echo ""
echo -e "  ${BOLD}Support us:${RESET}"
echo -e "  🛒 ${CYAN}https://store.steampowered.com/developer/DH/${RESET}"
