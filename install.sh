#!/usr/bin/env bash
#
# install.sh — one-command setup for free-mac-voice on Apple Silicon Macs.
#
#   bash install.sh
#
# Installs: Homebrew packages (whisper-cpp, ollama, python), a Python venv
# with everything free_voice.py needs, the local vision-language model, and a
# config template. Safe to re-run — it skips whatever is already done.
#
#   bash install.sh --update   # non-interactive refresh: skips permission
#                              # dialogs, login prompt, and welcome guide.
#                              # Used by upgrade.sh.
set -euo pipefail

UPDATE_MODE=0
YES_MODE=0
for arg in "$@"; do
  case "$arg" in
    --update) UPDATE_MODE=1 ;;
    --yes|-y|--unattended) YES_MODE=1 ;;
  esac
done
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/.venv"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\n\033[1;33mWARNING: %s\033[0m\n' "$*"; }

if [[ "$(uname)" != "Darwin" ]]; then
  warn "This installer targets macOS — continuing anyway, some steps may fail."
fi

# --- 1. Xcode Command Line Tools (Homebrew needs them) -------------------------
step "Checking Xcode Command Line Tools…"
if ! xcode-select -p >/dev/null 2>&1; then
  note "Installing — a macOS popup will ask you to confirm."
  xcode-select --install || true
  if [[ -t 0 && "$YES_MODE" != "1" ]]; then
    read -rp "Press Enter once the Command Line Tools install has finished… " _ || true
  else
    # non-interactive (automation): poll instead of blocking on read forever
    note "non-interactive — waiting up to ~10 min for the install to finish…"
    for _ in $(seq 1 60); do
      xcode-select -p >/dev/null 2>&1 && break
      sleep 10
    done
  fi
else
  note "already installed"
fi

# --- 2. Homebrew ----------------------------------------------------------------
step "Checking Homebrew…"
if ! command -v brew >/dev/null 2>&1; then
  note "Installing Homebrew (you may be asked for your Mac password)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
else
  note "already installed"
fi

# --- 3. System packages ----------------------------------------------------------
step "Installing whisper-cpp, ollama, python…"
brew install whisper-cpp ollama python \
  || warn "brew had an issue — continuing; re-run install.sh if something is missing."

# --- 4. Python venv + packages ----------------------------------------------------
step "Creating Python virtual environment…"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
  note "created at $VENV"
else
  note "already exists"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip >/dev/null
step "Installing Python packages…"
pip install -r "$REPO_DIR/requirements.txt" || pip install --ignore-requires-python -r "$REPO_DIR/requirements.txt"

# --- 5. Ollama: make sure it's serving, then pull the tiny router model -----------
step "Starting Ollama…"
brew services start ollama >/dev/null 2>&1 || true
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  note "launching 'ollama serve' in the background…"
  nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  WANT_MODEL="$(grep -E '^OLLAMA_MODEL=' "$HOME/.free-voice/.env" 2>/dev/null \
    | cut -d= -f2 | tr -d ' ' || true)"
  WANT_MODEL="${WANT_MODEL:-qwen3-vl:8b}"
  step "Pulling $WANT_MODEL (one-time download, skipped if already present)…"
  ollama pull "$WANT_MODEL"
  step "Pulling fast decision model tev1:0.8b (~800MB, skipped if already present)…"
  ollama pull tev1:0.8b || true
  step "Pulling fast drawing model qwen2.5:1.5b (~1GB, skipped if already present)…"
  ollama pull qwen2.5:1.5b || true
else
  warn "Ollama isn't responding — Tier 1 fallback will be skipped until you run 'ollama serve'."
fi

# --- 6. Kokoro Neural TTS (Default High-Definition Voice Engine) -----------------
step "Configuring Kokoro Neural TTS..."
KOKORO_DIR="$HOME/.config/free-voice/models/kokoro"
mkdir -p "$KOKORO_DIR"
if [[ ! -f "$KOKORO_DIR/voices-v1.0.bin" ]]; then
  note "Downloading Kokoro voices library (~27 MB)..."
  if curl -fSL --retry 3 -o "$KOKORO_DIR/voices-v1.0.bin.part" \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"; then
    mv "$KOKORO_DIR/voices-v1.0.bin.part" "$KOKORO_DIR/voices-v1.0.bin"
  else
    rm -f "$KOKORO_DIR/voices-v1.0.bin.part"
    warn "Kokoro voices download failed — re-run install.sh later."
  fi
fi
if [[ ! -f "$KOKORO_DIR/kokoro-v1.0.onnx" ]]; then
  note "Downloading Kokoro 82M neural TTS model (~310 MB)..."
  if curl -fSL --retry 3 -o "$KOKORO_DIR/kokoro-v1.0.onnx.part" \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"; then
    mv "$KOKORO_DIR/kokoro-v1.0.onnx.part" "$KOKORO_DIR/kokoro-v1.0.onnx"
  else
    rm -f "$KOKORO_DIR/kokoro-v1.0.onnx.part"
    warn "Kokoro model download failed — re-run install.sh later."
  fi
fi

# --- 6.5 Phonon-2 Next-Gen ASR (Default on Apple Silicon) ---------------------
if [[ "$(uname -m)" == "arm64" ]]; then
  step "Configuring Phonon-2 ASR (~164 MB Parakeet-TDT, skipped if present)..."
  "$VENV/bin/phonon" describe >/dev/null 2>&1 || true
fi

# --- 7. Config dir + .env template --------------------------------------------------
# Fixes the upgrade trap: an old .env that still points at a retired default
# model is bumped to the new default; user-customized values are never touched.
migrate_env() {
  local env="$HOME/.free-voice/.env"
  if grep -q '^OLLAMA_MODEL=qwen2\.5:1\.5b$' "$env" 2>/dev/null; then
    sed -i.bak 's/^OLLAMA_MODEL=qwen2\.5:1\.5b$/OLLAMA_MODEL=qwen3-vl:8b/' "$env"
    note "migrated OLLAMA_MODEL qwen2.5:1.5b -> qwen3-vl:8b (backup: $env.bak)"
  fi
  if ! grep -q 'OLLAMA_DECISION_MODEL=' "$env" 2>/dev/null; then
    printf '\n# Tier 0.5 decision model for ~50ms intent classification (leave blank to disable)\nOLLAMA_DECISION_MODEL=tev1:0.8b\n' >> "$env"
    note "added OLLAMA_DECISION_MODEL default (tev1:0.8b) to .env"
  fi
  if ! grep -q 'VOICE_STT_ENGINE=' "$env" 2>/dev/null; then
    printf '\n# Speech-to-text engine: "mlx-whisper" (Metal GPU on Apple Silicon, default), "phonon", or "whisper"\nVOICE_STT_ENGINE=mlx-whisper\n' >> "$env"
    note "added VOICE_STT_ENGINE default (mlx-whisper) to .env"
  fi

  if ! grep -q 'VOICE_USER_EMAIL=' "$env" 2>/dev/null; then
    printf '\n# "type my email" types this address. Uncomment and set it.\n# VOICE_USER_EMAIL=\n' >> "$env"
    note "added VOICE_USER_EMAIL placeholder to .env"
  fi
  if ! grep -q 'VOICE_WAKE_WORD=' "$env" 2>/dev/null; then
    printf '\n# Wake word for always-listening mode (default: mac). Leave blank to disable.\nVOICE_WAKE_WORD=mac\n' >> "$env"
    note "added VOICE_WAKE_WORD default (mac) to .env"
  fi
}
step "Setting up ~/.free-voice/.env…"
mkdir -p "$HOME/.free-voice"
if [[ ! -f "$HOME/.free-voice/.env" ]]; then
  cat > "$HOME/.free-voice/.env" <<'EOF'
# Optional: Gemini API free tier powers Tier 2 (open-ended questions).
# Get a key at https://aistudio.google.com/apikey — leave blank to skip.
GEMINI_API_KEY=
# Tier 1 local model (Ollama): vision-language model for routing, Q&A and
# screen understanding. 8GB minis: ollama pull qwen3-vl:4b and set this to qwen3-vl:4b.
OLLAMA_MODEL=qwen3-vl:8b
# Tier 0.5 decision model for sub-100ms intent classification (empty string disables):
OLLAMA_DECISION_MODEL=tev1:0.8b
# Fast text model for SVG vector generation ("draw a cat"):
OLLAMA_DRAW_MODEL=qwen2.5:1.5b
# Wake word for always-listening mode (default: mac). Leave blank to disable.
VOICE_WAKE_WORD=mac
EOF
  note "created — paste a Gemini key in later if you want Tier 2 Q&A"
else
  migrate_env
  note "already exists, migrated stale defaults, kept your settings"
fi

# Ensure user config directory exists (survives all git updates)
mkdir -p "$HOME/.config/free-voice"
if [[ ! -f "$HOME/.config/free-voice/extensions.py.example" ]]; then
  cat > "$HOME/.config/free-voice/extensions.py.example" <<'EOF'
# ~/.config/free-voice/extensions.py
# Custom user extensions and hooks that survive all updates.
# Rename this file to extensions.py to activate.

def register(add_command):
    # Example: Register a custom spoken command with custom Python logic
    def my_custom_action(match):
        print("Running custom Python logic!")
    
    # add_command(regex_pattern, handler_function, partial_ok=False)
    add_command(r"^my secret action$", my_custom_action)
EOF
fi

# --- 7. Permissions -------------------------------------------------------------------
if [[ "$UPDATE_MODE" == "1" ]]; then
  note "update mode — skipping permission dialogs (run install.sh without --update to re-prime)"
else
step "macOS permissions (one-time, ~60 seconds)"
note "This pops macOS's own Allow dialogs — just click through them."
note "Run this from the terminal app you'll use for Voice Control."
bash "$REPO_DIR/prime_permissions.sh"
fi

# --- 8. Smoke test ----------------------------------------------------------------------
step "Smoke test (no mic needed)…"
python "$REPO_DIR/free_voice.py" --text "open notes" --dry-run
python "$REPO_DIR/free_voice.py" --partial "open notes" --dry-run >/dev/null
note "router + completion gating OK"

# --- 9. Optional: start at login (always-listening) ---------------------------------------
if [[ "$UPDATE_MODE" == "1" ]]; then
  note "update mode — keeping existing login setting (service restart is handled by upgrade.sh)"
else
step "Start automatically at login?"
note "This keeps voice control always listening, even after a reboot."
note "It restarts itself if it ever crashes."
if [[ "$YES_MODE" == "1" ]]; then
  login_ans="Y"
  note "unattended mode: automatically enabling always-listening at login"
elif [[ -t 0 ]]; then
  read -rp "Enable always-listening at login? [y/N] " login_ans || true
else
  login_ans="N"  # non-interactive: default to no, re-run install.sh to enable
fi
if [[ "${login_ans:-N}" =~ ^[Yy] ]]; then
  bash "$REPO_DIR/service.sh" install
  note "installed — voice control and menu bar indicator now start at every login"
  note "first run will ask for Microphone permission for Python — allow it"
  note "check logs anytime: ./service.sh logs"
  note "manage service: ./service.sh {start|stop|restart|status|uninstall}"
else
  note "skipped — double-click 'Voice Control (Always On).command' to start manually"
fi
fi  # UPDATE_MODE

# --- 10. Welcome guide ----------------------------------------------------------------------
if [[ "$UPDATE_MODE" == "1" ]]; then
  note "update mode — skipping welcome guide"
else
step "Opening your 60-second start guide…"
open "$REPO_DIR/welcome.html" || true
fi

step "Done!"
cat <<'EOF'

  Talk to your Mac:  hold RIGHT OPTION ⌥, speak, release.   (Esc quits)

  Or just double-click "Voice Control.command" anytime — no terminal needed.

  For always-listening: double-click "Voice Control (Always On).command".
  (Or re-run install.sh and say yes to start-at-login.)

  Try saying:
    "open notes" · "set volume to 30" · "play"
    "set a timer for 5 minutes" · "click the Reply button"
    "search best pizza near me" · "what time is it"

  Say "help" anytime to hear everything it understands.

  To update later: bash upgrade.sh
EOF
