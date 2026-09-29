#!/usr/bin/env bash
# prime_permissions.sh — pop macOS's native permission dialogs so the user
# just clicks Allow, instead of hunting through System Settings.
#
# What macOS allows (and doesn't):
#   Microphone, Accessibility, Screen Recording -> the OS shows a dialog the
#     first time an app tries; this script provokes each one on purpose.
#   Input Monitoring -> Apple offers NO dialog API; one manual toggle needed.
# Nothing here bypasses anything: if a permission was previously denied, no
# dialog re-appears and the toggle must be flipped in Settings by hand.
set -uo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "prime_permissions.sh is macOS-only."
  exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/.venv"

echo "Requesting permissions for the terminal app you're running this in."
echo "(Launch Voice Control from this same terminal app later.)"
echo

# --- 1. Microphone: a 1-second recording attempt pops the system dialog ---
echo "==> 1/4 Microphone — click OK / Allow in the dialog that pops up…"
if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" - <<'EOF' 2>/dev/null || true
try:
    import sounddevice as sd
    sd.rec(16000, samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    print("    mic probe done")
except Exception as e:
    print(f"    mic probe could not record ({e}) — check the toggle in Settings")
EOF
else
  echo "    (venv not found — run install.sh first)"
fi

# --- 2. Accessibility: talking to System Events pops the system dialog ---
echo "==> 2/4 Accessibility — click \"Open System Settings\" and flip the toggle…"
osascript -e 'tell application "System Events" to return (count of processes)' >/dev/null 2>&1 || true
if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" - <<'EOF' 2>/dev/null || true
try:
    import ctypes, ctypes.util
    lib = ctypes.util.find_library("ApplicationServices")
    if lib:
        ax = ctypes.CDLL(lib)
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        print("    Accessibility:", "GRANTED" if ax.AXIsProcessTrusted() else "not yet — flip the toggle in Settings")
    else:
        print("    Accessibility: could not verify — check Settings")
except Exception:
    print("    Accessibility: could not verify — check Settings")
EOF
fi

# --- 3. Screen Recording: a screenshot attempt pops the dialog (macOS 26+) ---
echo "==> 3/4 Screen Recording — click Allow if a dialog pops up…"
screencapture -x /tmp/fv_perm_probe.png 2>/dev/null || true
rm -f /tmp/fv_perm_probe.png
echo "    (only needed on macOS 26+ for \"click the … button\")"

# --- 4. Input Monitoring: no dialog API exists — one manual toggle ---
echo "==> 4/4 Input Monitoring — macOS offers no popup for this one."
echo "    Opening Settings; flip the Input Monitoring toggle for your terminal app."
open "x-apple.systempreferences:com.apple.settings.PrivacySecurity" || true

echo
echo "Done. If anything was previously DENIED, no dialog re-appears —"
echo "flip it by hand in System Settings → Privacy & Security."
