"""
integrations/runner_game/runner_manager.py
-----------------------------------------
Manages lifecycle for the 3D Voice-Reactive Vector Runner in free-mac-voice:
- Fires up Ollama & Jev endpoints if necessary
- Launches the asynchronous server.py orchestrator
- Opens the Three.js WebGL game in Safari
- Handles zero-interference Game Mode and clean exit
"""

import os
import sys
import json
import signal
import subprocess
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(DIR, "server.py")
GAME_HTML = os.path.join(DIR, "game.html")
FLAG_FILE = "/tmp/vector_runner_active.flag"
VOICE_INPUT_FILE = "/tmp/runner_voice_input.txt"


def is_runner_active() -> bool:
    """Returns True if the Vector Runner orchestrator is running."""
    if os.path.exists(FLAG_FILE):
        try:
            with open(FLAG_FILE, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid:
                os.kill(pid, 0)
                return True
        except (OSError, json.JSONDecodeError):
            pass
    return False


def ensure_ollama_running():
    """Verifies Ollama is responsive on port 11434; attempts background launch if down."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=0.8):
            return True
    except Exception:
        pass

    # Attempt to start ollama serve
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        return True
    except Exception:
        return False


def start_runner_game(say_fn=None) -> bool:
    """Fires up everything necessary for the 3D Voice-Reactive Vector Runner to play."""
    if is_runner_active():
        if say_fn:
            say_fn("Runner game is already active")
        # Ensure Safari is focused
        subprocess.run(["open", "-a", "Safari", GAME_HTML])
        return True

    # 1. Ensure Ollama is ready (and free the shared ws port if the shooter is running)
    try:
        from integrations.shooter_game.shooter_manager import is_shooter_active, close_shooter_game
        if is_shooter_active():
            close_shooter_game(say_fn=lambda *_: None)
            time.sleep(0.4)
    except Exception:
        pass
    ensure_ollama_running()

    # 2. Pick Python binary (prefer venv)
    repo_dir = os.path.abspath(os.path.join(DIR, "..", ".."))
    venv_python = os.path.join(repo_dir, ".venv", "bin", "python")
    python_bin = venv_python if os.path.exists(venv_python) else sys.executable

    # 3. Launch server.py in background
    cmd = [python_bin, SERVER_PY]
    proc = subprocess.Popen(
        cmd,
        cwd=DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # 4. Open game.html in Safari
    time.sleep(0.4)
    subprocess.run(["open", "-a", "Safari", GAME_HTML])

    if say_fn:
        say_fn("Starting runner game. Game mode active.")
    else:
        subprocess.Popen(["say", "Starting runner game. Game mode active."])

    return True


def close_runner_game(say_fn=None) -> bool:
    """Cleanly terminates the runner backend orchestrator and closes the game window."""
    # 1. Kill backend orchestrator
    if os.path.exists(FLAG_FILE):
        try:
            with open(FLAG_FILE, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        except Exception:
            pass
        try:
            os.remove(FLAG_FILE)
        except OSError:
            pass

    # 2. Close Safari game window
    safari_close = (
        'tell application "Safari"\n'
        '    set winList to every window\n'
        '    repeat with w in winList\n'
        '        if name of w contains "Vector Runner" or name of w contains "Voice-Reactive" then\n'
        '            close w\n'
        '        end if\n'
        '    end repeat\n'
        'end tell'
    )
    subprocess.run(["osascript", "-e", safari_close], capture_output=True)

    if say_fn:
        say_fn("Runner game closed")
    else:
        subprocess.Popen(["say", "Runner game closed"])

    return True


def pipe_voice_to_runner(text: str) -> None:
    """Appends speech transcript to the runner game input pipe."""
    try:
        with open(VOICE_INPUT_FILE, "a") as f:
            f.write(text.strip() + "\n")
    except Exception as e:
        print(f"[!] Error writing to {VOICE_INPUT_FILE}: {e}")
