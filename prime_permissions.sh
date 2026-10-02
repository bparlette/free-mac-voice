#!/usr/bin/env bash
# prime_permissions.sh — one-time macOS setup: pop the native permission
# dialogs so the user just clicks Allow, and auto-select the iPhone as the
# microphone when it's available via Continuity.
#
# What macOS allows (and doesn't):
#   Microphone, Accessibility, Screen Recording -> the OS shows a dialog the
#     first time an app tries; this script provokes each one on purpose.
#   Input Monitoring -> Apple offers NO dialog API; one manual toggle needed.
#   Audio input selection -> scriptable via SwitchAudioSource (Homebrew), but
#     the iPhone only appears when Continuity is happy (same Apple ID,
#     Wi-Fi + Bluetooth on, iPhone nearby/unlocked). Nothing can force that
#     from the Mac — if the phone isn't visible we set VOICE_MIC=iPhone so
#     the app picks it up automatically whenever it comes in range.
# Nothing here bypasses anything: if a permission was previously denied, no
# dialog re-appears and the toggle must be flipped in Settings by hand.
set -uo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "prime_permissions.sh is macOS-only."
  exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/.venv"

upsert_env() { # $1=KEY $2=value — add or replace a line in ~/.free-voice/.env
  local envdir="$HOME/.free-voice"
  local env="$envdir/.env"
  mkdir -p "$envdir"
  touch "$env"
  if grep -q "^$1=" "$env" 2>/dev/null; then
    sed -i.bak "s|^$1=.*|$1=$2|" "$env" && rm -f "$env.bak"
  else
    printf '%s=%s\n' "$1" "$2" >> "$env"
  fi
}

echo "Requesting permissions for the terminal app you're running this in."
echo "(Launch Voice Control from this same terminal app later.)"
echo

# --- 1. Microphone: a 1-second recording attempt pops the system dialog ---
echo "==> 1/5 Microphone — click OK / Allow in the dialog that pops up…"
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
echo "==> 2/5 Accessibility — click \"Open System Settings\" and flip the toggle…"
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
echo "==> 3/5 Screen Recording — click Allow if a dialog pops up…"
screencapture -x /tmp/fv_perm_probe.png 2>/dev/null || true
rm -f /tmp/fv_perm_probe.png
echo "    (only needed on macOS 26+ for \"click the … button\")"

# --- 4. Input Monitoring: no dialog API exists — one manual toggle ---
echo "==> 4/5 Input Monitoring — macOS offers no popup for this one."
echo "    Opening Settings; flip the Input Monitoring toggle for your terminal app."
open "x-apple.systempreferences:com.apple.settings.PrivacySecurity" || true

# --- 5. iPhone as microphone --------------------------------------------------
echo "==> 5/5 iPhone as microphone — detecting…"
if ! command -v SwitchAudioSource >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "    installing SwitchAudioSource (audio device switcher)…"
    brew install switchaudio-osx >/dev/null 2>&1 || true
  fi
fi
IPHONE=""
if command -v SwitchAudioSource >/dev/null 2>&1; then
  IPHONE="$(SwitchAudioSource -a -t input 2>/dev/null | grep -i "iphone" | head -1 | sed 's/^[[:space:]]*//')"
  if [[ -n "$IPHONE" ]]; then
    echo "    found: $IPHONE"
    if SwitchAudioSource -s "$IPHONE" -t input >/dev/null 2>&1; then
      echo "    system input set to your iPhone ✓"
    else
      echo "    (could not switch system input — select it by hand in Sound settings)"
    fi
  fi
fi
if [[ -n "$IPHONE" ]]; then
  upsert_env VOICE_MIC "iPhone"
  echo "    saved VOICE_MIC=iPhone — the app will prefer your iPhone mic"
else
  echo "    iPhone not visible right now. For it to appear:"
  echo "      • same Apple ID on Mac and iPhone"
  echo "      • Wi-Fi and Bluetooth ON on both, iPhone nearby and unlocked"
  echo "    VOICE_MIC not changed — the app will use the Mac's default input."
  echo "    Re-run this script once your iPhone shows up to pin it."
  echo "    Opening Sound settings so you can pick it by hand if you like."
  open "x-apple.systempreferences:com.apple.settings.Sound" || true
fi

echo
echo "Done. If anything was previously DENIED, no dialog re-appears —"
echo "flip it by hand in System Settings → Privacy & Security."
