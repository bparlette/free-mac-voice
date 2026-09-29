#!/bin/bash
# Double-click to start always-listening voice control. Ctrl-C or close the
# window to stop. (For start-at-login instead, re-run install.sh and answer
# "y" to the login prompt.)
cd "$(dirname "$0")"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Not installed yet — double-click install.sh first."
  read -rp "Press Enter to close… " _ || true
  exit 1
fi
source .venv/bin/activate
exec python3 free_voice.py --always
