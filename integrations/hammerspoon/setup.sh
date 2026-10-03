#!/usr/bin/env bash
# Setup Hammerspoon integration for Free Mac Voice (HUD + Window Display Server)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HS_DIR="$HOME/.hammerspoon"
INIT_LUA="$HS_DIR/init.lua"

echo "==> Configuring Hammerspoon for Free Mac Voice..."

# Ensure Hammerspoon is installed
if [ ! -d "/Applications/Hammerspoon.app" ]; then
    echo "==> Hammerspoon not found in /Applications. Installing via brew..."
    brew install --cask hammerspoon
fi

mkdir -p "$HS_DIR"

if [ -f "$INIT_LUA" ] && ! grep -q "Free Mac Voice" "$INIT_LUA"; then
    BACKUP="$INIT_LUA.backup.$(date +%s)"
    echo "==> Backing up existing init.lua to $BACKUP"
    mv "$INIT_LUA" "$BACKUP"
fi

cp "$SCRIPT_DIR/init.lua" "$INIT_LUA"
echo "==> Installed Free Mac Voice Hammerspoon configuration to $INIT_LUA"

# Launch or reload Hammerspoon
if pgrep -x "Hammerspoon" >/dev/null 2>&1; then
    echo "==> Hammerspoon is already running. Reloading configuration..."
    if command -v hs >/dev/null 2>&1; then
        hs -c "hs.reload()" || true
    fi
else
    echo "==> Launching Hammerspoon.app..."
    open -a Hammerspoon
    sleep 2
fi

echo "==> Testing HUD connection on port 19825..."
for i in {1..5}; do
    if curl -s -X POST http://127.0.0.1:19825/hud \
        -H "Content-Type: application/json" \
        -d '{"text": "Free Mac Voice HUD Ready", "state": "success"}' >/dev/null 2>&1; then
        echo "==> Free Mac Voice HUD is connected and active!"
        exit 0
    fi
    sleep 1
done

echo "==> Note: If macOS prompts for Accessibility permissions for Hammerspoon, please click 'Allow' in System Settings."
