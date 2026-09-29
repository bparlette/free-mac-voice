#!/usr/bin/env bash
# upgrade.sh — update an existing install to the latest version from GitHub.
#
# Handles both install styles:
#   * git clone  -> git pull --ff-only
#   * zip/tarball download -> downloads the latest main tarball and overlays
#     the new files, preserving .venv, logs, and your ~/.free-voice/.env
#
# Then re-runs install.sh, which refreshes dependencies and is safe to
# run repeatedly. Test hook: UPGRADE_TARBALL_URL overrides the download URL.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/bparlette/free-mac-voice"
TARBALL_URL="${UPGRADE_TARBALL_URL:-$REPO_URL/archive/refs/heads/main.tar.gz}"

cd "$REPO_DIR"

if [[ -d .git ]]; then
  echo "==> git install detected — pulling latest..."
  git pull --ff-only
else
  echo "==> zip install detected — downloading latest version..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$TARBALL_URL" | tar -xz -C "$tmp"
  src="$tmp"/free-mac-voice-main
  if [[ ! -d "$src" ]]; then
    echo "ERROR: unexpected tarball layout" >&2
    exit 1
  fi
  shopt -s dotglob nullglob
  for f in "$src"/*; do
    base="$(basename "$f")"
    case "$base" in
      .git|.venv) continue ;;   # never clobber these
    esac
    cp -r "$f" "$REPO_DIR/"
    echo "    updated $base"
  done
  shopt -u dotglob nullglob
fi

echo "==> re-running installer (refreshes deps, keeps your settings)..."
bash "$REPO_DIR/install.sh"
