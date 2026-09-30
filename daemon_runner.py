#!/usr/bin/env python3
"""daemon_runner.py — Supervises free_voice --always and menu_bar.py as a unified background service.

Handles process lifecycle, graceful signals (SIGINT, SIGTERM), and restarts.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(REPO_DIR, ".venv", "bin", "python")
PYTHON_BIN = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable

FREE_VOICE_PY = os.path.join(REPO_DIR, "free_voice.py")
MENU_BAR_PY = os.path.join(REPO_DIR, "menu_bar.py")

procs: list[subprocess.Popen] = []


def cleanup(signum=None, frame=None):
    """Gracefully terminate all managed child processes."""
    for p in procs:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    time.sleep(0.5)
    for p in procs:
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. Launch free_voice.py in always-listening mode
    cmd_voice = [PYTHON_BIN, FREE_VOICE_PY, "--always"]
    voice_proc = subprocess.Popen(cmd_voice, cwd=REPO_DIR)
    procs.append(voice_proc)

    # 2. Launch menu_bar.py status icon
    if os.path.exists(MENU_BAR_PY):
        try:
            cmd_bar = [PYTHON_BIN, MENU_BAR_PY]
            bar_proc = subprocess.Popen(cmd_bar, cwd=REPO_DIR)
            procs.append(bar_proc)
        except Exception:
            pass

    # 3. Supervise processes
    while True:
        for p in procs:
            ret = p.poll()
            if ret is not None and p == voice_proc:
                # If core voice engine dies, exit supervisor so launchd can handle restart
                cleanup()
        time.sleep(1)


if __name__ == "__main__":
    main()
