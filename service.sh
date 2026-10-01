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

  radar-install)
    echo "==> Installing 7:00 AM Daily Tech Radar LaunchAgent..."
    RADAR_LABEL="com.free-mac-voice.radar"
    RADAR_PLIST="$HOME/Library/LaunchAgents/${RADAR_LABEL}.plist"
    cat > "$RADAR_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${RADAR_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${REPO_DIR}/scripts/tech_radar.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
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
    <string>/tmp/free-mac-voice-radar.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/free-mac-voice-radar.err</string>
</dict>
</plist>
EOF
    launchctl unload "$RADAR_PLIST" 2>/dev/null || true
    launchctl load -w "$RADAR_PLIST"
    echo "==> Daily 7:00 AM Tech Radar installed successfully."
    echo "    Will scan GitHub & Hugging Face every morning at 7:00 AM."
    echo "    Findings saved to: ~/.config/free-voice/radar_report.md"
    ;;

  radar-run)
    echo "==> Running Voice Tech Radar scan now..."
    "$PYTHON_BIN" "$REPO_DIR/scripts/tech_radar.py"
    ;;

  radar-report)
    REPORT_PATH="$HOME/.config/free-voice/radar_report.md"
    if [[ -f "$REPORT_PATH" ]]; then
      cat "$REPORT_PATH"
    else
      echo "No radar report found yet. Run './service.sh radar-run' to generate one."
    fi
    ;;

  radar-status)
    echo "==> Radar Schedule Status: com.free-mac-voice.radar"
    if launchctl list | grep -q "com.free-mac-voice.radar"; then
      echo "    State: ACTIVE (Scheduled daily at 7:00 AM)"
      echo "    Report File: ~/.config/free-voice/radar_report.md"
    else
      echo "    State: NOT INSTALLED (Run './service.sh radar-install')"
    fi
    ;;

  radar-uninstall)
    echo "==> Removing Tech Radar LaunchAgent..."
    RADAR_PLIST="$HOME/Library/LaunchAgents/com.free-mac-voice.radar.plist"
    launchctl unload -w "$RADAR_PLIST" 2>/dev/null || true
    rm -f "$RADAR_PLIST"
    echo "==> Tech Radar LaunchAgent removed."
    ;;

  roast)
    echo "==> Summoning Screen Critic Overlay (Default: Couch Duo)..."
    nohup "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" > "$LOG_DIR/critic.log" 2>&1 &
    ;;

  roast-now)
    echo "==> Triggering on-demand roast..."
    "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" roast
    ;;

  roast-demo)
    echo "==> Running full animation showcase demo..."
    "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" demo
    ;;

  roast-theme)
    THEME_ARG="${2:-couch_duo}"
    echo "==> Setting critic theme to: $THEME_ARG"
    "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" theme "$THEME_ARG"
    ;;

  roast-pos)
    POS_ARG="${2:-bottom_left}"
    echo "==> Setting critic position to: $POS_ARG"
    "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" pos "$POS_ARG"
    ;;

  roast-stop)
    echo "==> Dismissing Screen Critic..."
    "$PYTHON_BIN" "$REPO_DIR/masterpiece_critic.py" stop
    ;;

  clips-web)
    echo "==> Starting Screen Critic Highlights Gallery Web Server on port 8765..."
    echo "    Open in your browser: http://localhost:8765"
    "$PYTHON_BIN" "$REPO_DIR/gallery_server.py"
    ;;

  clips-dir)
    CLIPS_PATH="$HOME/.config/free-voice/clips"
    mkdir -p "$CLIPS_PATH"
    echo "==> Opening Highlights folder in Finder: $CLIPS_PATH"
    open "$CLIPS_PATH"
    ;;

  clips-clean)
    echo "==> Cleaning up clips older than 2 days..."
    "$PYTHON_BIN" -c "from gallery_server import cleanup_old_clips; cleanup_old_clips(2.0); print('Done.')"
    ;;

  *)
    echo "Usage: ./service.sh {install|start|stop|restart|status|logs|uninstall|radar-install|radar-run|radar-report|radar-status|radar-uninstall|roast|roast-now|roast-theme <theme>|roast-pos <pos>|roast-stop|clips-web|clips-dir|clips-clean}"
    exit 1
    ;;
esac

