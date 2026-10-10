#!/bin/bash
# Installs (or removes with --uninstall) the daily 2 am tech-scout job as a per-user launchd agent. Nothing else is started or restarted.
set -eu
LABEL="com.free-mac-voice.tech-scout"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UIDN="$(id -u)"
if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$UIDN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi
mkdir -p "$HOME/.free-voice/scout" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$REPO/.venv/bin/python</string><string>$REPO/scripts/tech_scout.py</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$HOME/.free-voice/scout/run.log</string>
  <key>StandardErrorPath</key><string>$HOME/.free-voice/scout/run.log</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$UIDN/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UIDN" "$PLIST"
echo "installed $LABEL: runs every day at 02:00; reports in ~/.free-voice/scout/ (latest.md)"
