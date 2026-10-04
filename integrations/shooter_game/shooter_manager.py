"""
integrations/shooter_game/shooter_manager.py
--------------------------------------------
Lifecycle for Rogue Mech Protocol (the "shooter" game) in free-mac-voice:
launches the WebSocket bridge, opens game.html in Safari, publishes the
active flag that puts free-mac-voice into zero-interference Game Mode.
"""

import json
import os
import signal
import subprocess
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(DIR, "server.py")
GAME_HTML = os.path.join(DIR, "game.html")
FLAG_FILE = "/tmp/shooter_game_active.flag"
VOICE_INPUT_FILE = "/tmp/shooter_voice_input.txt"


def is_shooter_active() -> bool:
    """True if the shooter bridge process is alive."""
    try:
        with open(FLAG_FILE) as f:
            pid = json.load(f).get("pid")
        if pid:
            os.kill(pid, 0)
            return True
    except (OSError, ValueError):
        pass
    return False


def _stop_other_games() -> None:
    """Both games share ws port 8765, so only one may run at a time."""
    try:
        from integrations.runner_game.runner_manager import is_runner_active, close_runner_game
        if is_runner_active():
            close_runner_game(say_fn=lambda *_: None)
            time.sleep(0.4)
    except Exception:
        pass


def start_shooter_game(say_fn=None) -> bool:
    if is_shooter_active():
        if say_fn:
            say_fn("Shooter game is already active")
        subprocess.run(["open", "-a", "Safari", GAME_HTML])
        return True

    _stop_other_games()
    try:
        open(VOICE_INPUT_FILE, "w").close()
    except OSError:
        pass

    repo_dir = os.path.abspath(os.path.join(DIR, "..", ".."))
    venv_python = os.path.join(repo_dir, ".venv", "bin", "python")
    python_bin = venv_python if os.path.exists(venv_python) else sys.executable
    subprocess.Popen([python_bin, SERVER_PY], cwd=DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    subprocess.run(["open", "-a", "Safari", GAME_HTML])

    msg = "Starting shooter game. Game mode active."
    if say_fn:
        say_fn(msg)
    else:
        subprocess.Popen(["say", msg])
    return True


def close_shooter_game(say_fn=None) -> bool:
    try:
        with open(FLAG_FILE) as f:
            pid = json.load(f).get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    except (OSError, ValueError):
        pass
    try:
        os.remove(FLAG_FILE)
    except OSError:
        pass

    script = (
        'tell application "Safari"\n'
        '    repeat with w in (every window)\n'
        '        if name of w contains "Rogue Mech" then close w\n'
        '    end repeat\n'
        'end tell'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)

    if say_fn:
        say_fn("Shooter game closed")
    else:
        subprocess.Popen(["say", "Shooter game closed"])
    return True


def pipe_voice_to_shooter(text: str) -> None:
    try:
        with open(VOICE_INPUT_FILE, "a") as f:
            f.write(text.strip() + "\n")
    except OSError as e:
        print(f"[!] Error writing to {VOICE_INPUT_FILE}: {e}")
