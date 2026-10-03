"""
Voice-Reactive 3D Vector Runner Orchestrator (server.py)
-------------------------------------------------------
Binds transcribed speech to sub-second reflex actions via llama-server (Jev)
and asynchronous world shifts via Ollama (Qwen 9B), broadcasting JSON to Three.js.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import websockets
import atexit
import signal

# === CONFIGURATION & LOCAL ENDPOINTS ===
WS_HOST = os.getenv("WS_HOST", "localhost")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
JEV_URL = os.getenv("JEV_URL", "http://localhost:8080/v1/systemone")
QWEN_URL = os.getenv("QWEN_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:9b")

FLAG_FILE = "/tmp/vector_runner_active.flag"
VOICE_INPUT_FILE = "/tmp/runner_voice_input.txt"

connected_clients = set()


def cleanup_flag():
    if os.path.exists(FLAG_FILE):
        try:
            os.remove(FLAG_FILE)
        except OSError:
            pass

atexit.register(cleanup_flag)
signal.signal(signal.SIGTERM, lambda s, f: (cleanup_flag(), sys.exit(0)))


async def ws_handler(websocket, *args):
    """Registers connected WebGL browser clients and maintains socket lifecycle."""
    connected_clients.add(websocket)
    client_info = getattr(websocket, "remote_address", "browser")
    print(f"[+] WebGL Client Connected: {client_info} (Active: {len(connected_clients)})")
    try:
        async for _ in websocket:
            pass  # Keep alive until disconnect
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"[-] WebGL Client Disconnected (Active: {len(connected_clients)})")


async def broadcast(payload: dict):
    """Broadcasts a JSON payload to all connected Three.js game clients."""
    if not connected_clients:
        return
    message = json.dumps(payload)
    results = await asyncio.gather(
        *(client.send(message) for client in connected_clients),
        return_exceptions=True
    )
    for res in results:
        if isinstance(res, Exception):
            print(f"[!] WebSocket send error: {res}")


async def route_intent(session: aiohttp.ClientSession, phrase: str) -> str:
    """
    Queries Jev / llama-server System One categorical endpoint for sub-second reflex routing.
    Schema matches game reflexes against open-ended world shift commands.
    """
    payload = {
        "state": phrase,
        "questions": {
            "game_intent": {
                "type": "categorical",
                "options": [
                    {"id": "left", "description": "The word left, steer left, or go left"},
                    {"id": "right", "description": "The word right, steer right, or go right"},
                    {"id": "jump", "description": "The word jump, hop, or leap"},
                    {"id": "faster", "description": "The word faster, speed up, boost, or turbo"},
                    {"id": "slower", "description": "The word slower, slow down, brake, or ease up"},
                    {"id": "shoot", "description": "The word shoot, fire lasers, or blast"},
                    {"id": "restart", "description": "The word restart, retry, or again"},
                    {"id": "world_shift", "description": "Phrases with change world, change theme, or descriptive world shifts"},
                    {"id": "none", "description": "Unrelated conversation, background noise, or filler speech"}
                ]
            }
        }
    }
    
    try:
        async with session.post(JEV_URL, json=payload, timeout=1.0) as resp:
            if resp.status == 200:
                data = await resp.json()
                intent = data.get("answers", {}).get("game_intent", "none")
                if intent:
                    return intent
    except Exception as e:
        print(f"[!] Jev Router Error ({e}) -> Using fast keyword reflex fallback")

    # High-speed deterministic fallback if Jev server is offline or spinning up
    p = phrase.lower().strip()
    words = p.split()
    if "left" in words or "left" in p:
        return "left"
    if "right" in words or "right" in p:
        return "right"
    if "jump" in words or "jump" in p:
        return "jump"
    if any(k in words for k in ["faster", "fast", "boost"]) or "faster" in p or "speed up" in p:
        return "faster"
    if any(k in words for k in ["slower", "slow", "brake"]) or "slower" in p or "slow down" in p:
        return "slower"
    if any(k in words for k in ["shoot", "fire", "laser", "blast"]) or "shoot" in p:
        return "shoot"
    if any(k in words for k in ["restart", "retry", "again", "respawn"]):
        return "restart"
    if any(k in p for k in ["change world", "theme", "world", "turn into", "cathedral", "vampire", "matrix", "neon", "magma", "space"]):
        return "world_shift"
        
    return "none"


async def generate_theme(session: aiohttp.ClientSession, phrase: str) -> dict | None:
    """
    Queries Ollama Qwen 9B in JSON mode to hallucinate a complete 3D color/obstacle theme.
    Non-blocking: executed in a background task so game reflexes never freeze.
    """
    prompt = (
        "You are a 3D aesthetic environment designer for a futuristic vector game. "
        "Output strictly valid JSON with the following keys:\n"
        "  world_name: brief title of the world (e.g. 'Blood Cathedral', 'Neon Cyberpunk')\n"
        "  grid_color: hex string (e.g. '#ff0033')\n"
        "  fog_color: hex string (e.g. '#1a0005')\n"
        "  laser_color: hex string (e.g. '#00ffcc')\n"
        "  obstacle_type: one of 'pyramid', 'cube', or 'ring'\n"
        "  speed_multiplier: float between 0.8 and 1.5\n\n"
        f"User Prompt: {phrase}\n"
        "Return ONLY the JSON object. Do not include markdown codeblocks or explanation."
    )
    
    payload = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 120
        }
    }
    
    try:
        async with session.post(QWEN_URL, json=payload, timeout=8.0) as resp:
            if resp.status == 200:
                data = await resp.json()
                raw_json = data.get("response", "{}")
                return json.loads(raw_json)
    except Exception as e:
        print(f"[!] Qwen Generation Failed ({e}) -> Applying procedural theme fallback")

    # Fallback preset palette
    return {
        "world_name": "Synthesized Crimson",
        "grid_color": "#ff0044",
        "fog_color": "#1a000a",
        "laser_color": "#ff00ff",
        "obstacle_type": "pyramid",
        "speed_multiplier": 1.2
    }


async def background_theme_task(session: aiohttp.ClientSession, phrase: str):
    """Dispatches generative world generation on a detached background task."""
    print(f"🎨 [Qwen 9B Background] Generating world shift for: '{phrase}'...")
    start_t = time.time()
    theme_data = await generate_theme(session, phrase)
    elapsed = time.time() - start_t
    if theme_data:
        print(f"🌌 [World Shift Ready] '{theme_data.get('world_name')}' generated in {elapsed:.2f}s -> Broadcasting to Three.js")
        await broadcast({"theme": theme_data})


async def monitor_free_voice_daemon(session: aiohttp.ClientSession):
    """
    Subscribes to free-mac-voice daemon state IPC and runner voice input pipe.
    Allows spoken voice commands from your microphone to control the runner in real-time.
    """
    state_file = os.path.join(tempfile.gettempdir(), "free-voice-state.json")
    last_mtime = 0.0
    last_cmd = ""
    last_input_pos = 0

    if os.path.exists(VOICE_INPUT_FILE):
        last_input_pos = os.path.getsize(VOICE_INPUT_FILE)
    
    while True:
        try:
            # 1. High-priority dedicated game voice pipe (written by free-mac-voice extension)
            if os.path.exists(VOICE_INPUT_FILE):
                curr_size = os.path.getsize(VOICE_INPUT_FILE)
                if curr_size > last_input_pos:
                    with open(VOICE_INPUT_FILE, "r") as f:
                        f.seek(last_input_pos)
                        lines = f.readlines()
                        last_input_pos = f.tell()
                    for line in lines:
                        cmd = line.strip()
                        if cmd:
                            intent = await route_intent(session, cmd)
                            print(f"🎙️ [free-mac-voice runner pipe] '{cmd}' -> {intent}")
                            if intent in ['left', 'right', 'jump', 'faster', 'slower', 'shoot', 'restart', 'move_left', 'move_right', 'boost']:
                                await broadcast({"action": intent})
                            elif intent == "world_shift":
                                asyncio.create_task(background_theme_task(session, cmd))

            # 2. General free-mac-voice state file watcher
            if os.path.exists(state_file):
                mtime = os.path.getmtime(state_file)
                if mtime > last_mtime:
                    last_mtime = mtime
                    with open(state_file, "r") as f:
                        data = json.load(f)
                    cmd = data.get("command", "").strip()
                    if cmd and cmd != last_cmd:
                        last_cmd = cmd
                        intent = await route_intent(session, cmd)
                        print(f"🎙️ [free-mac-voice state] '{cmd}' -> {intent}")
                        if intent in ['left', 'right', 'jump', 'faster', 'slower', 'shoot', 'restart', 'move_left', 'move_right', 'boost']:
                            await broadcast({"action": intent})
                        elif intent == "world_shift":
                            asyncio.create_task(background_theme_task(session, cmd))
        except Exception:
            pass
        await asyncio.sleep(0.04)


async def input_loop(session: aiohttp.ClientSession):
    """Interactive command feed: permits typing or piping spoken transcripts directly."""
    while True:
        try:
            phrase = await asyncio.to_thread(input, "\n🎤 Spoken / Console Command: ")
            phrase = phrase.strip()
            if not phrase:
                continue

            # Step 1: Sub-second reflex classification via Jev
            intent = await route_intent(session, phrase)
            print(f"⚡ Jev Classified: '{intent}'")

            # Step 2: Immediate Reflex Execution (0-latency WS frame)
            if intent in ['left', 'right', 'jump', 'faster', 'slower', 'shoot', 'restart', 'move_left', 'move_right', 'boost']:
                await broadcast({"action": intent})

            # Step 3: Non-blocking Generative World Shift (spawns background task)
            elif intent == "world_shift":
                asyncio.create_task(background_theme_task(session, phrase))
            else:
                print(f"ℹ️ Intent '{intent}' filtered (no in-game action taken).")
        except (EOFError, KeyboardInterrupt):
            break


async def main():
    print("=================================================================")
    print("    🚀 3D VOICE-REACTIVE VECTOR RUNNER ORCHESTRATION SERVER")
    print(f"    - WebSocket Server   : ws://{WS_HOST}:{WS_PORT}")
    print(f"    - Jev Reflex Router  : {JEV_URL}")
    print(f"    - Qwen Generator     : {QWEN_URL} ({OLLAMA_MODEL})")
    print(f"    - Active Flag        : {FLAG_FILE}")
    print("=================================================================")
    
    # Publish active flag for free-mac-voice game mode awareness
    try:
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": WS_PORT, "started_at": time.time()}, f)
    except Exception as e:
        print(f"[!] Warning: Could not write {FLAG_FILE}: {e}")

    try:
        async with aiohttp.ClientSession() as session:
            server = await websockets.serve(ws_handler, WS_HOST, WS_PORT)
            await asyncio.gather(
                monitor_free_voice_daemon(session),
                input_loop(session)
            )
            server.close()
            await server.wait_closed()
    finally:
        cleanup_flag()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Shutting down orchestrator cleanly.")
        sys.exit(0)
