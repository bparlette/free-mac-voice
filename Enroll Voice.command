#!/bin/bash
# Double-click or run to enroll your voice profile for Free Mac Voice.
cd "$(dirname "$0")" || exit 1
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Virtual environment not found at .venv/bin/python"
  read -rp "Press Enter to close… " _ || true
  exit 1
fi

echo "=== Free Mac Voice - Voice Enrollment ==="
./.venv/bin/python free_voice.py --enroll
echo ""
read -rp "Enrollment finished. Press [Enter] to exit… " _ || true
