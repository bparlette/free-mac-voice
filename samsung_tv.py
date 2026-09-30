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
import sys
import urllib.error
import urllib.request

API = "https://api.smartthings.com/v1"
TOKEN = os.environ.get("SAMSUNG_ST_TOKEN", "").strip()
DEVICE_ID = os.environ.get("SAMSUNG_TV_DEVICE_ID", "").strip()

# Friendly aliases -> raw SmartThings input-source values. Raw values differ
# per TV model; `discover` prints the real ones for your set.
ALIASES = {
    "computer": os.environ.get("SAMSUNG_INPUT_COMPUTER", "HDMI1"),
    "mac": os.environ.get("SAMSUNG_INPUT_COMPUTER", "HDMI1"),
    "pc": os.environ.get("SAMSUNG_INPUT_COMPUTER", "HDMI1"),
    "tv": os.environ.get("SAMSUNG_INPUT_TV", "digitalTv"),
    "television": os.environ.get("SAMSUNG_INPUT_TV", "digitalTv"),
    "cable": os.environ.get("SAMSUNG_INPUT_TV", "digitalTv"),
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
    if key in ALIASES:
        return ALIASES[key]
    # Accept raw values like "HDMI1", "hdmi 2", "digitalTv"
    return key.upper().replace("HDMI ", "HDMI") if key.startswith("hdmi") else name.strip()


def cmd_set_input(name: str) -> str:
    dev = _need_device()
    source = _resolve_source(name)
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "mediaInputSource",
                      "command": "setInputSource",
                      "arguments": [source]}]})
    msg = f"TV input -> {source}"
    print(msg)
    return msg


def cmd_power(state: str) -> str:
    dev = _need_device()
    state = state.lower()
    if state not in ("on", "off"):
        raise TVError("power must be 'on' or 'off'")
    _req("POST", f"/devices/{dev}/commands", {
        "commands": [{"component": "main",
                      "capability": "switch",
                      "command": state,
                      "arguments": []}]})
    msg = f"TV power {state}"
    print(msg)
    return msg


def cmd_status() -> str:
    dev = _need_device()
    st = _req("GET", f"/devices/{dev}/status")
    main = st.get("components", {}).get("main", {})
    src = main.get("mediaInputSource", {}).get("inputSource", {}).get("value")
    power = main.get("switch", {}).get("switch", {}).get("value")
    msg = f"TV power: {power}, input: {src}"
    print(msg)
    return msg


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[0])
        print("commands: discover | set-input <name> | power on|off | status")
        return 2
    cmd, rest = argv[1], argv[2:]
    try:
        if cmd == "discover":
            cmd_discover()
        elif cmd == "set-input" and rest:
            cmd_set_input(" ".join(rest))
        elif cmd == "power" and rest:
            cmd_power(rest[0])
        elif cmd == "status":
            cmd_status()
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2
    except TVError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
