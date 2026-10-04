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
from intents import map_voice_to_actions, map_voice_to_action  # noqa: E402

WS_HOST = os.getenv("WS_HOST", "localhost")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
JEV_URL = os.getenv("JEV_URL", "http://localhost:8080/v1/systemone")
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


async def query_jev_fallback(phrase: str) -> list[str]:
    """Optional Jev System One categorical query if local rules didn't catch conversational phrasing."""
    try:
        import aiohttp
        payload = {
            "state": phrase,
            "questions": {
                "shooter_intent": {
                    "type": "categorical",
                    "options": [
                        {"id": "kick", "description": "Kick, stomp, or smash the ground tank"},
                        {"id": "shoot", "description": "Shoot, blast, launch rocket, or fire at the jet"},
                        {"id": "vent_1", "description": "Vent, cool, or flush reactor core 1"},
                        {"id": "vent_2", "description": "Vent, cool, or flush reactor core 2"},
                        {"id": "vent_3", "description": "Vent, cool, or flush reactor core 3"},
                        {"id": "red", "description": "Answer red color for the weapons puzzle"},
                        {"id": "blue", "description": "Answer blue color for the weapons puzzle"},
                        {"id": "start", "description": "Deploy, start mission, launch, or retry"},
                        {"id": "none", "description": "Unrelated conversation or background noise"}
                    ]
                }
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(JEV_URL, json=payload, timeout=0.6) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ans = data.get("answers", {}).get("shooter_intent", "none")
                    if ans and ans != "none":
                        return [ans]
    except Exception:
        pass
    return []


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
                        raw_text = line.strip()
                        if not raw_text:
                            continue
                        # Broadcast voice_heard so the UI dot blinks and displays the heard transcript
                        await broadcast({"event": "voice_heard", "text": raw_text})
                        actions = map_voice_to_actions(raw_text)
                        if not actions:
                            actions = await query_jev_fallback(raw_text)
                        print(f"[voice] '{raw_text}' -> {actions}", flush=True)
                        for act in actions:
                            await broadcast({"action": act})
                            if len(actions) > 1:
                                await asyncio.sleep(0.09)
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
