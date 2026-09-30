#!/usr/bin/env bash
# service.sh — Manage free-mac-voice as a persistent macOS LaunchAgent
#
# Commands:
#   ./service.sh install    - Install & start LaunchAgent (auto-starts on login)
#   ./service.sh start      - Start background service
#   ./service.sh stop       - Stop background service
#   ./service.sh restart    - Restart background service
#   ./service.sh status     - Check service status and health
#   ./service.sh logs       - Tail live daemon logs
#   ./service.sh uninstall  - Remove LaunchAgent

set -euo pipefail

LABEL="com.free-mac-voice"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(which python3)"
fi

RUNNER="$REPO_DIR/daemon_runner.py"
LOG_DIR="$HOME/.free-voice"
STDOUT_LOG="$LOG_DIR/daemon.log"
STDERR_LOG="$LOG_DIR/daemon.err"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

generate_plist() {
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${RUNNER}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
        <key>LC_ALL</key>
        <string>en_US.UTF-8</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${STDOUT_LOG}</string>

    <key>StandardErrorPath</key>
    <string>${STDERR_LOG}</string>
</dict>
</plist>
EOF
}

cmd="${1:-status}"

case "$cmd" in
  install)
    echo "==> Configuring LaunchAgent at $PLIST"
    generate_plist
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load -w "$PLIST"
    echo "==> Installed and started $LABEL"
    echo "    Daemon is active and will automatically run on login."
    echo "    Menu bar icon and always-listening mode are running in background."
    echo "    Check logs anytime: ./service.sh logs"
    ;;

  start)
    echo "==> Starting $LABEL..."
    if [[ ! -f "$PLIST" ]]; then
      generate_plist
      launchctl load -w "$PLIST"
    else
      launchctl start "$LABEL" 2>/dev/null || launchctl load -w "$PLIST"
    fi
    echo "==> Started."
    ;;

  stop)
    echo "==> Stopping $LABEL..."
    launchctl stop "$LABEL" 2>/dev/null || true
    echo "==> Stopped."
    ;;

  restart)
    echo "==> Restarting $LABEL..."
    launchctl stop "$LABEL" 2>/dev/null || true
    sleep 1
    launchctl start "$LABEL" 2>/dev/null || launchctl load -w "$PLIST"
    echo "==> Restarted."
    ;;

  status)
    echo "==> Service Status: $LABEL"
    if launchctl list | grep -q "$LABEL"; then
      PID="$(launchctl list | grep "$LABEL" | awk '{print $1}')"
      if [[ "$PID" != "-" && -n "$PID" ]]; then
        echo "    State: RUNNING (PID $PID)"
      else
        echo "    State: LOADED (Idle / waiting)"
      fi
    else
      echo "    State: NOT INSTALLED"
    fi
    if [[ -f "$STDOUT_LOG" ]]; then
      echo ""
      echo "--- Recent Logs (last 5 lines) ---"
      tail -n 5 "$STDOUT_LOG" 2>/dev/null || true
    fi
    ;;

  logs)
    echo "==> Streaming logs from $STDOUT_LOG (Ctrl+C to exit)..."
    touch "$STDOUT_LOG"
    tail -f "$STDOUT_LOG"
    ;;

  uninstall)
    echo "==> Uninstalling $LABEL..."
    launchctl unload -w "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "==> Uninstalled LaunchAgent."
    ;;

  *)
    echo "Usage: ./service.sh {install|start|stop|restart|status|logs|uninstall}"
    exit 1
    ;;
esac
