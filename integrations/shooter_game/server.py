"""
Rogue Mech Protocol voice bridge (server.py)
--------------------------------------------
Serves ws://localhost:8765 and relays spoken commands (piped in by
free-mac-voice via /tmp/shooter_voice_input.txt) to game.html as
{"action": "..."} frames. No LLM needed: mapping is deterministic and instant.
"""

import asyncio
import atexit
import json
import os
import signal
import sys
import time

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intents import map_voice_to_action  # noqa: E402

WS_HOST = os.getenv("WS_HOST", "localhost")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
FLAG_FILE = "/tmp/shooter_game_active.flag"
VOICE_INPUT_FILE = "/tmp/shooter_voice_input.txt"

clients = set()


def cleanup_flag():
    try:
        os.remove(FLAG_FILE)
    except OSError:
        pass


atexit.register(cleanup_flag)
signal.signal(signal.SIGTERM, lambda s, f: (cleanup_flag(), sys.exit(0)))


async def ws_handler(websocket, *args):
    clients.add(websocket)
    try:
        async for _ in websocket:
            pass
    except Exception:
        pass
    finally:
        clients.discard(websocket)


async def broadcast(payload: dict):
    if not clients:
        return
    msg = json.dumps(payload)
    await asyncio.gather(*(c.send(msg) for c in list(clients)), return_exceptions=True)


async def watch_voice_pipe():
    pos = os.path.getsize(VOICE_INPUT_FILE) if os.path.exists(VOICE_INPUT_FILE) else 0
    while True:
        try:
            if os.path.exists(VOICE_INPUT_FILE):
                size = os.path.getsize(VOICE_INPUT_FILE)
                if size < pos:
                    pos = 0
                if size > pos:
                    with open(VOICE_INPUT_FILE, "r") as f:
                        f.seek(pos)
                        lines = f.readlines()
                        pos = f.tell()
                    for line in lines:
                        action = map_voice_to_action(line)
                        print(f"[voice] '{line.strip()}' -> {action}", flush=True)
                        if action:
                            await broadcast({"action": action})
        except Exception as e:
            print(f"[!] pipe error: {e}", flush=True)
        await asyncio.sleep(0.04)


async def main():
    print(f"ROGUE MECH PROTOCOL bridge on ws://{WS_HOST}:{WS_PORT}", flush=True)
    with open(FLAG_FILE, "w") as f:
        json.dump({"pid": os.getpid(), "port": WS_PORT, "started_at": time.time()}, f)
    try:
        async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
            await watch_voice_pipe()
    finally:
        cleanup_flag()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
