#!/usr/bin/env python3
"""Control a Samsung Smart TV over the network via the SmartThings cloud API.

Stdlib only. Used by free_voice.py voice commands:
    "switch to computer" / "switch to tv" / "switch input to HDMI 2"
    "turn on the tv" / "turn off the tv"

One-time setup (about 5 minutes):
  1. iPhone: open the SmartThings app, sign in with your Samsung account,
     and add your Samsung TV (it is usually auto-detected on the same Wi-Fi).
  2. On any browser: https://account.smartthings.com -> Personal Access
     Tokens -> Generate new token. Name it "mac-voice" and grant scopes
     r:devices:* (read) and x:devices:* (run commands). Copy the token.
  3. Add to the same .env file free_voice.py loads (or export in shell):
       SAMSUNG_ST_TOKEN=<paste the token>
       SAMSUNG_TV_DEVICE_ID=<from step 4>
     Optional overrides (defaults shown):
       SAMSUNG_INPUT_COMPUTER=HDMI1
       SAMSUNG_INPUT_TV=digitalTv
  4. Run:  python3 samsung_tv.py discover
     This prints your devices (copy the TV's device id into step 3) and the
     TV's supported input sources. If your Mac is on a different HDMI port
     than HDMI1, set SAMSUNG_INPUT_COMPUTER accordingly.

Usage:
    samsung_tv.py discover              list devices + TV input sources
    samsung_tv.py set-input <name>      name: computer | tv | <raw source>
    samsung_tv.py power on|off
    samsung_tv.py status                current input + power state
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request


def load_env() -> None:
    for path in (
        os.path.expanduser("~/.free-voice/.env"),
        os.path.expanduser("~/.config/free-voice/.env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k not in os.environ and v:
                        os.environ[k] = v
        except OSError:
            pass


load_env()

API = "https://api.smartthings.com/v1"
TOKEN = os.environ.get("SAMSUNG_ST_TOKEN", "").strip()
DEVICE_ID = os.environ.get("SAMSUNG_TV_DEVICE_ID", "").strip()


def send_wol(mac: str) -> None:
    """Send a Wake-on-LAN magic packet to wake the TV if it is in deep sleep."""
    try:
        clean = mac.replace(":", "").replace("-", "").replace(".", "")
        payload = bytes.fromhex("FF" * 6 + clean * 16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(payload, ("255.255.255.255", 9))
    except Exception:
        pass

# Friendly aliases -> raw SmartThings input-source values. Raw values differ
# per TV model; `discover` prints the real ones for your set.
ALIASES = {
    "computer": "HDMI1",
    "mac": "HDMI1",
    "pc": "HDMI1",
    "tv": "digitalTv",
    "television": "digitalTv",
    "cable": "digitalTv",
}


class TVError(RuntimeError):
    pass


def _req(method: str, path: str, payload: dict | None = None) -> dict:
    if not TOKEN:
        raise TVError("SAMSUNG_ST_TOKEN is not set (see samsung_tv.py header for setup)")
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise TVError("SmartThings token rejected (401) — regenerate it at "
                          "account.smartthings.com") from e
        if e.code == 404:
            raise TVError("Device not found (404) — check SAMSUNG_TV_DEVICE_ID") from e
        raise TVError(f"SmartThings HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise TVError(f"Could not reach SmartThings cloud: {e.reason}") from e


def _need_device() -> str:
    if not DEVICE_ID:
        raise TVError("SAMSUNG_TV_DEVICE_ID is not set — run "
                      "'python3 samsung_tv.py discover' to find it")
    return DEVICE_ID


def cmd_discover() -> str:
    data = _req("GET", "/devices")
    lines = []
    for d in data.get("items", []):
        name = d.get("label") or d.get("name", "?")
        dtype = (d.get("type") or "?")
        lines.append(f"{d.get('deviceId')}  {name}  [{dtype}]")
    out = "\n".join(lines) if lines else "(no devices found — is the TV added in the SmartThings app?)"
    try:
        dev = _need_device()
        st = _req("GET", f"/devices/{dev}/status")
        main = st.get("components", {}).get("main", {})
        src = main.get("mediaInputSource", {}).get("inputSource", {}).get("value")
        supported = main.get("mediaInputSource", {}).get("supportedInputSources", {}).get("value")
        power = main.get("switch", {}).get("switch", {}).get("value")
        out += f"\n\nTV {dev}:\n  current input: {src}\n  power: {power}\n  supported inputs: {supported}"
    except TVError as e:
        out += f"\n\nTV status skipped: {e}"
    print(out)
    return out


def _resolve_source(name: str) -> str:
    key = name.strip().lower().replace(" ", "")
    if key in ("computer", "mac", "pc") and os.environ.get("SAMSUNG_INPUT_COMPUTER"):
        return os.environ.get("SAMSUNG_INPUT_COMPUTER").strip()
    if key in ("tv", "television", "cable") and os.environ.get("SAMSUNG_INPUT_TV"):
        return os.environ.get("SAMSUNG_INPUT_TV").strip()
    if key in ALIASES:
        return ALIASES[key]
    # Accept raw values like "HDMI1", "hdmi 2", "digitalTv"
    return key.upper().replace("HDMI ", "HDMI") if key.startswith("hdmi") else name.strip()


def cmd_home() -> str:
    dev = _need_device()
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "samsungvd.remoteControl",
                      "command": "send",
                      "arguments": ["HOME"]}]})
    msg = "TV home"
    print(msg)
    return msg


def cmd_set_input(name: str) -> str:
    dev = _need_device()
    source = _resolve_source(name)
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "mediaInputSource",
                      "command": "setInputSource",
                      "arguments": [source]}]})
    if source.lower() in ("digitaltv", "dtv"):
        try:
            cmd_home()
        except Exception:
            pass
    msg = f"TV input -> {source}"
    print(msg)
    return msg


def _state(refresh: bool = True) -> tuple[str | None, str | None]:
    """Return (power, app) from SmartThings.

    The Frame reports switch=on while it shows Art Mode; the only reliable
    tell is tvChannel.tvChannelName == "art" (verified live on a 2024 Frame).
    A refresh is needed first or the cloud value can be minutes stale.
    Unknown values come back as None so callers never fail on missing data.
    """
    dev = _need_device()
    if refresh:
        try:
            _req("POST", f"/devices/{dev}/commands", {
                "commands": [{"component": "main", "capability": "refresh",
                              "command": "refresh", "arguments": []}]})
        except TVError:
            pass
        time.sleep(2.0)
    main = _req("GET", f"/devices/{dev}/status").get("components", {}).get("main", {})
    power = main.get("switch", {}).get("switch", {}).get("value")
    app = main.get("tvChannel", {}).get("tvChannelName", {}).get("value")
    return power, app


def cmd_art() -> str:
    dev = _need_device()
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "samsungvd.ambient",
                      "command": "setAmbientOn",
                      "arguments": []}]})
    # setAmbientOn returns 200 even when the TV ignores it, so check.
    time.sleep(4.0)
    try:
        _, app = _state()
    except TVError:
        app = None
    if app is not None and app != "art":
        raise TVError("the TV did not go into art mode (it is still on)")
    msg = "TV art mode on"
    print(msg)
    return msg


def cmd_power(state: str) -> str:
    dev = _need_device()
    state = state.lower()
    if state not in ("on", "off"):
        raise TVError("power must be 'on' or 'off'")
    if state == "off":
        if os.environ.get("SAMSUNG_TV_STANDBY", "").lower() in ("ambient", "art"):
            return cmd_art()
        _req("POST", f"/devices/{dev}/commands", {
            "commands": [{"component": "main", "capability": "switch",
                          "command": "off", "arguments": []}]})
        msg = "TV power off"
        print(msg)
        return msg

    # ON. On The Frame, Art Mode already counts as switch=on, so switch.on is
    # a no-op there; the HOME key is what leaves Art Mode for the Samsung Home
    # screen. Send it, confirm art mode is gone, and retry a couple of times.
    mac = os.environ.get("SAMSUNG_TV_MAC", "").strip()
    if mac:
        send_wol(mac)
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main", "capability": "switch",
                      "command": "on", "arguments": []}]})
    power, app = None, None
    for attempt in range(3):
        time.sleep(1.5 if attempt == 0 else 2.5)
        try:
            cmd_home()
        except TVError:
            if attempt == 2:
                raise
            continue
        try:
            power, app = _state()
        except TVError:
            break  # cannot verify; HOME was sent, assume it worked
        if power != "off" and app != "art":
            break
    if power == "off":
        raise TVError("the TV is fully off and did not wake; turn on "
                      "'Power On with Mobile' on the TV or use the remote")
    if app == "art":
        raise TVError("the TV is still showing art mode")
    msg = "TV power on"
    print(msg)
    return msg


def cmd_status() -> str:
    dev = _need_device()
    st = _req("GET", f"/devices/{dev}/status")
    main = st.get("components", {}).get("main", {})
    src = main.get("mediaInputSource", {}).get("inputSource", {}).get("value")
    power = main.get("switch", {}).get("switch", {}).get("value")
    vol = main.get("audioVolume", {}).get("volume", {}).get("value")
    mute = main.get("audioMute", {}).get("mute", {}).get("value")
    msg = f"TV power: {power}, input: {src}, volume: {vol}, mute: {mute}"
    print(msg)
    return msg


def cmd_set_volume(level: int) -> str:
    dev = _need_device()
    level = max(0, min(100, int(level)))
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "audioVolume",
                      "command": "setVolume",
                      "arguments": [level]}]})
    msg = f"TV volume -> {level}"
    print(msg)
    return msg


def cmd_volume_delta(delta: int) -> str:
    dev = _need_device()
    cmd = "volumeUp" if delta > 0 else "volumeDown"
    steps = min(10, max(1, abs(delta)))  # capped per request; report what was sent
    commands = [{"component": "main",
                 "capability": "audioVolume",
                 "command": cmd,
                 "arguments": []} for _ in range(steps)]
    _req("POST", f"/devices/{dev}/commands", {"commands": commands})
    direction = "up" if delta > 0 else "down"
    msg = f"TV volume {direction} by {steps}"
    print(msg)
    return msg


def cmd_mute(mute: bool) -> str:
    dev = _need_device()
    command = "mute" if mute else "unmute"
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "audioMute",
                      "command": command,
                      "arguments": []}]})
    msg = f"TV {'muted' if mute else 'unmuted'}"
    print(msg)
    return msg


def cmd_media(action: str) -> str:
    dev = _need_device()
    action = action.lower().strip()
    if action not in ("play", "pause", "stop", "fastforward", "rewind"):
        raise TVError(f"unsupported media action: {action}")
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "mediaPlayback",
                      "command": action,
                      "arguments": []}]})
    msg = f"TV media {action}"
    print(msg)
    return msg


def main(argv: list[str]) -> int:
    global TOKEN, DEVICE_ID
    load_env()
    TOKEN = os.environ.get("SAMSUNG_ST_TOKEN", TOKEN).strip()
    DEVICE_ID = os.environ.get("SAMSUNG_TV_DEVICE_ID", DEVICE_ID).strip()
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[0])
        print("commands: discover | set-input <name> | power on|off | status | "
              "set-volume <0-100> | volume-up [n] | volume-down [n] | mute | unmute | media play|pause|stop")
        return 2
    cmd, rest = argv[1], argv[2:]
    try:
        if cmd == "discover":
            cmd_discover()
        elif cmd == "set-input" and rest:
            cmd_set_input(" ".join(rest))
        elif cmd == "power" and rest:
            cmd_power(rest[0])
        elif cmd == "art":
            cmd_art()
        elif cmd == "home":
            cmd_home()
        elif cmd == "status":
            cmd_status()
        elif cmd == "set-volume" and rest:
            cmd_set_volume(int(rest[0]))
        elif cmd == "volume-up":
            delta = int(rest[0]) if rest else 1
            cmd_volume_delta(delta)
        elif cmd == "volume-down":
            delta = int(rest[0]) if rest else 1
            cmd_volume_delta(-delta)
        elif cmd == "mute":
            cmd_mute(True)
        elif cmd == "unmute":
            cmd_mute(False)
        elif cmd == "media" and rest:
            cmd_media(rest[0])
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2
    except TVError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
