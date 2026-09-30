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
if [[ "${1:-}" == "--update" ]]; then
  UPDATE_MODE=1
fi
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
  if [[ -t 0 ]]; then
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
pip install -r "$REPO_DIR/requirements.txt"

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
  step "Pulling fast drawing model qwen2.5:1.5b (~1GB, skipped if already present)…"
  ollama pull qwen2.5:1.5b || true
else
  warn "Ollama isn't responding — Tier 1 fallback will be skipped until you run 'ollama serve'."
fi

# --- 6. Config dir + .env template --------------------------------------------------
# Fixes the upgrade trap: an old .env that still points at a retired default
# model is bumped to the new default; user-customized values are never touched.
migrate_env() {
  local env="$HOME/.free-voice/.env"
  if grep -q '^OLLAMA_MODEL=qwen2\.5:1\.5b$' "$env" 2>/dev/null; then
    sed -i.bak 's/^OLLAMA_MODEL=qwen2\.5:1\.5b$/OLLAMA_MODEL=qwen3-vl:8b/' "$env"
    note "migrated OLLAMA_MODEL qwen2.5:1.5b -> qwen3-vl:8b (backup: $env.bak)"
  fi
  if ! grep -q 'VOICE_USER_EMAIL=' "$env" 2>/dev/null; then
    printf '\n# "type my email" types this address. Uncomment and set it.\n# VOICE_USER_EMAIL=\n' >> "$env"
    note "added VOICE_USER_EMAIL placeholder to .env"
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
# Fast text model for SVG vector generation ("draw a cat"):
OLLAMA_DRAW_MODEL=qwen2.5:1.5b
EOF
  note "created — paste a Gemini key in later if you want Tier 2 Q&A"
else
  migrate_env
  note "already exists, migrated stale defaults, kept your settings"
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
if [[ -t 0 ]]; then
  read -rp "Enable always-listening at login? [y/N] " login_ans || true
else
  login_ans="N"  # non-interactive: default to no, re-run install.sh to enable
fi
if [[ "${login_ans:-N}" =~ ^[Yy] ]]; then
  PLIST="$HOME/Library/LaunchAgents/com.free-mac-voice.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed -e "s#__VENV__#$VENV#g" -e "s#__REPO__#$REPO_DIR#g" \
    "$REPO_DIR/com.free-mac-voice.plist" > "$PLIST"
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load -w "$PLIST"
  note "installed — voice control is now listening and starts at every login"
  note "first run will ask for Microphone permission for Python — allow it"
  note "logs: /tmp/free-mac-voice.log"
  note "to remove later: launchctl unload -w \"$PLIST\""
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
