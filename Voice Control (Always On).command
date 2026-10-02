#!/bin/bash
# Double-click to start always-listening voice control. Ctrl-C or close the
# window to stop. (For start-at-login instead, re-run install.sh and answer
# "y" to the login prompt.)
cd "$(dirname "$0")" || exit 1
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Not installed yet — double-click install.sh first."
  read -rp "Press Enter to close… " _ || true
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! pgrep -f "menu_bar.py" >/dev/null 2>&1; then
  python3 menu_bar.py &
  MENU_PID=$!
  trap 'kill $MENU_PID 2>/dev/null' EXIT INT TERM
fi
exec python3 free_voice.py --always
