#!/bin/bash
# Double-click to start voice control. (Run install.sh first, once.)
cd "$(dirname "$0")" || exit 1
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Voice control isn't installed yet."
  echo "Double-click install.sh (or run: bash install.sh) first."
  read -rp "Press Enter to close… " _ || true
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
exec python3 free_voice.py
