#!/usr/bin/env python3
"""
free_voice.py — free, fast voice control for macOS (v2: 3-tier "Instant & Free").

Pipeline (everything local, $0 per command):
    mic (hold right-Option, speak, release)
      -> faster-whisper tiny.en on Metal (~100-300 ms, local, free)
      -> Tier 0: deterministic regex router + completion gating (<1 ms, free)
             on partial transcripts it fires ONLY when the command is
             terminal-complete at a valid boundary ("open notes" but never
             "open no"), so it can act mid-sentence without misfires
      -> Tier 1 (Tier 0 miss only): local Ollama 1.5B in JSON mode (~0.7-1 s
             warm, free) for novel phrasing ("could you be a dear and open
             my browser thing"). Self-reported confidence < 0.5 = miss.
             NOTE: this is NOT calibrated like Jev — it's a heuristic.
      -> Tier 2 (Tier 1 miss/unsure only): Gemini API free tier for
             open-ended questions (needs GEMINI_API_KEY, still $0)
      -> execution: AppleScript/shell for system actions, xa11y over the
             macOS Accessibility tree for real UI clicks ("click the Reply
             button") — no brittle pixel coordinates
      -> macOS `say` speaks a short confirmation

No TypeSafe/Jev key needed. No paid calls, ever, in the default path.

Usage:
    python3 free_voice.py --text "open notes"
    python3 free_voice.py --text "set volume to 30" --dry-run
    python3 free_voice.py --text "click the Reply button" --dry-run
    python3 free_voice.py --partial "open notes"      # demo Tier 0 gating on
                                                     # growing partials
    python3 free_voice.py --list            # show every command pattern
    python3 free_voice.py                  # push-to-talk: hold RIGHT OPTION, speak, release
    python3 free_voice.py --once 6         # record 6 s without a hotkey

Setup: reuse the venv from setup.sh (faster-whisper, sounddevice, pynput).
Optional: `brew install ollama && ollama pull qwen2.5:1.5b` (Tier 1),
`pip install xa11y` (UI-click commands).
Permissions on the Mac: Microphone + Accessibility (+ Input Monitoring for
the push-to-talk hotkey) for the launching terminal. macOS 26+: xa11y also
needs Screen Recording to see window contents.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------- env

HOME = os.path.expanduser("~")
ENV_FILES = [
    os.path.join(HOME, ".free-voice", ".env"),
    os.path.join(HOME, ".jev-voice", ".env"),  # reuse Gemini key if you already set one
]


def load_env() -> None:
    for path in ENV_FILES:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        except FileNotFoundError:
            pass


load_env()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny.en")
# Tier 1 (local LLM fallback). Set OLLAMA_TIER1=0 to disable.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIER1 = os.environ.get("OLLAMA_TIER1", "1") == "1"
TIER1_MIN_CONFIDENCE = float(os.environ.get("TIER1_MIN_CONFIDENCE", "0.5"))
# Microphone selection: case-insensitive substring matched against input
# device names, e.g. VOICE_MIC=iPhone uses the Continuity microphone whenever
# the iPhone is in range, falling back to the system default otherwise.
# Overridable per-run with --mic.
VOICE_MIC = os.environ.get("VOICE_MIC", "")
DRY_RUN = False


def log(msg: str) -> None:
    print(f"[free-voice] {msg}", flush=True)


# ---------------------------------------------------------------- speech out

def say(text: str) -> None:
    log(f"say: {text}")
    if DRY_RUN:
        return
    subprocess.run(["say", text], check=False)


def applescript(script: str) -> str:
    """Run AppleScript, return stdout. Raises on failure."""
    if DRY_RUN:
        log(f"DRY-RUN osascript: {script[:120]}")
        return ""
    out = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    return out.stdout.strip()


def shell(cmd: list[str]) -> str:
    if DRY_RUN:
        log("DRY-RUN shell: " + " ".join(cmd))
        return ""
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or "command failed")
    return out.stdout.strip()


def esc(s: str) -> str:
    """Escape a string for AppleScript double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- audio in

_rec_q: "queue.Queue" = queue.Queue()
_recording = threading.Event()


def resolve_input_device(want: str = ""):
    """Return a sounddevice input device index, or None for the system default.

    `want` (default VOICE_MIC) is a case-insensitive substring matched against
    input device names. Returns None when unset or no match — the caller then
    uses the system default input.
    """
    want = (want or VOICE_MIC).strip().lower()
    if not want:
        return None
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 \
                    and want in str(d.get("name", "")).lower():
                return i
    except Exception:
        pass
    return None


def describe_input_device() -> str:
    """Human-readable name of the mic that will be used, for startup logging."""
    try:
        import sounddevice as sd
        idx = resolve_input_device()
        if idx is None:
            dev = sd.query_devices(kind="input")
        else:
            dev = sd.query_devices(idx)
        return str(dev.get("name", "?"))
    except Exception:
        return "?"


def _audio_cb(indata, frames, time_info, status):  # sounddevice callback
    if _recording.is_set():
        _rec_q.put(bytes(indata))


def record_while_held() -> bytes:
    """Record from the default mic until _recording is cleared. Returns raw PCM16."""
    import sounddevice as sd
    import numpy as np

    frames: list[bytes] = []

    def drain():
        while True:
            try:
                frames.append(_rec_q.get(timeout=0.1))
            except queue.Empty:
                if not _recording.is_set():
                    break

    _recording.set()
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    with sd.RawInputStream(
        samplerate=16000, channels=1, dtype="int16", callback=_audio_cb,
        device=resolve_input_device()
    ):
        while _recording.is_set():
            time.sleep(0.05)
    t.join(timeout=2.0)
    pcm = b"".join(frames)
    import numpy as np  # noqa: F811  (kept local so --text mode needs no audio deps)

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def record_fixed(seconds: float):
    import sounddevice as sd

    log(f"recording {seconds:.0f}s...")
    data = sd.rec(int(seconds * 16000), samplerate=16000, channels=1,
                  dtype="float32", device=resolve_input_device())
    sd.wait()
    return data.flatten()


_whisper = None


def transcribe(audio) -> str:
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        log(f"loading whisper model '{WHISPER_MODEL}' (first run downloads it)...")
        _whisper = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
    segments, _ = _whisper.transcribe(audio, beam_size=1, vad_filter=True)
    text = " ".join(s.text for s in segments).strip()
    return text


def push_to_talk_loop(on_utterance) -> None:
    """Hold RIGHT OPTION to record, release to act. Esc quits."""
    from pynput import keyboard

    held = threading.Event()
    stop = threading.Event()
    worker: list[threading.Thread] = []
    result: dict = {}

    def worker_fn():
        result["audio"] = record_while_held()

    def on_press(key):
        if stop.is_set():
            return False
        if key == keyboard.Key.alt_r and not held.is_set():
            held.set()
            _recording.set()
            log("listening... (release right-Option)")
            t = threading.Thread(target=worker_fn, daemon=True)
            worker.append(t)
            t.start()
        elif key == keyboard.Key.esc:
            stop.set()
            _recording.clear()
            return False

    def on_release(key):
        if key == keyboard.Key.alt_r and held.is_set():
            held.clear()
            _recording.clear()
            log("transcribing...")
            for t in worker:
                t.join(timeout=30)
            worker.clear()
            audio = result.pop("audio", None)
            if audio is not None and len(audio) > 1600:  # >0.1s of audio
                on_utterance(audio)
            else:
                log("too short, ignoring")

    log("push-to-talk ready: hold RIGHT OPTION, speak, release. Esc quits.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


# ------------------------------------------------------- always-listening

class VoiceActivityDetector:
    """Tiny energy-based VAD with an adaptive noise floor. No dependencies.

    Feed 16-bit mono frames via update(); it returns one of
    "start" | "speech" | "end" | "silence". The noise floor adapts slowly
    during silence so a TV hum or fan doesn't permanently deafen it.
    Hysteresis (separate start/stop thresholds) avoids chattering.
    """

    def __init__(self, sensitivity: float = 3.0, frame_ms: int = 30,
                 start_ms: int = 250, end_ms: int = 900):
        self.sensitivity = sensitivity
        self.start_needed = max(1, start_ms // frame_ms)
        self.end_needed = max(1, end_ms // frame_ms)
        self.floor = 200.0          # adaptive RMS noise floor (int16 units)
        self.in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0

    def update(self, samples) -> str:
        import numpy as np

        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)) + 1e-6)
        if not self.in_speech:
            # adapt the floor only while silent (slowly)
            self.floor = 0.98 * self.floor + 0.02 * min(rms, self.floor * 4)
            floor = max(self.floor, 60.0)  # never trust a near-zero floor
            if rms > floor * self.sensitivity:
                self._speech_frames += 1
                if self._speech_frames >= self.start_needed:
                    self.in_speech = True
                    self._silence_frames = 0
                    return "start"
            else:
                self._speech_frames = 0
            return "silence"
        # in speech: end only after sustained quiet (hysteresis)
        if rms < max(self.floor, 60.0) * self.sensitivity * 0.6:
            self._silence_frames += 1
            if self._silence_frames >= self.end_needed:
                self.in_speech = False
                self._speech_frames = 0
                return "end"
        else:
            self._silence_frames = 0
        return "speech"


def always_listen_loop(on_utterance, sensitivity: float = 3.0) -> None:
    """Listen continuously; transcribe each detected utterance. Ctrl-C quits.

    No wake word: any speech is transcribed and routed through the normal
    tiers. Non-commands are ignored silently (the caller passes
    quiet_miss=True), so background chatter costs a transcription but no
    noise. In a loud room, prefer push-to-talk.
    """
    import sounddevice as sd
    import numpy as np

    vad = VoiceActivityDetector(sensitivity=sensitivity)
    frame_len = int(16000 * 0.03)  # 30 ms frames
    capturing: list[np.ndarray] = []
    max_frames = int(15 / 0.03)   # 15 s safety cap per utterance

    log("always-listening: speak naturally, Ctrl-C quits. "
        "(no wake word — speech itself is the trigger)")
    try:
        with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16",
                               blocksize=frame_len,
                               device=resolve_input_device()) as stream:
            while True:
                data, _ = stream.read(frame_len)
                samples = np.frombuffer(data, dtype=np.int16)
                state = vad.update(samples)
                if state == "start":
                    capturing = [samples.copy()]
                    log("heard speech, capturing...")
                elif state == "speech" and capturing:
                    capturing.append(samples.copy())
                    if len(capturing) >= max_frames:
                        state = "end"  # safety cap: cut it off
                if state == "end" and capturing:
                    audio = (np.concatenate(capturing)
                             .astype(np.float32) / 32768.0)
                    capturing = []
                    if len(audio) > 16000 * 0.4:  # ignore blips < 0.4 s
                        log("transcribing...")
                        on_utterance(audio)
                    time.sleep(0.5)  # cooldown so one sentence = one command
    except KeyboardInterrupt:
        log("always-listening stopped.")

# ---------------------------------------------------------------- app names

_APP_ALIASES = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "safari": "Safari", "firefox": "Firefox", "edge": "Microsoft Edge",
    "notes": "Notes", "mail": "Mail", "messages": "Messages",
    "facetime": "FaceTime", "photos": "Photos", "music": "Music",
    "tv": "TV", "apple tv": "TV", "podcasts": "Podcasts",
    "finder": "Finder", "settings": "System Settings",
    "preferences": "System Settings", "system settings": "System Settings",
    "terminal": "Terminal", "iterm": "iTerm",
    "calendar": "Calendar", "reminders": "Reminders", "maps": "Maps",
    "preview": "Preview", "textedit": "TextEdit",
    "app store": "App Store", "activity monitor": "Activity Monitor",
    "calculator": "Calculator", "dictionary": "Dictionary",
    "contacts": "Contacts", "freeform": "Freeform",
    "keynote": "Keynote", "pages": "Pages", "numbers": "Numbers",
    "cursor": "Cursor", "vs code": "Visual Studio Code", "vscode": "Visual Studio Code",
    "spotify": "Spotify", "zoom": "zoom.us", "slack": "Slack",
    "discord": "Discord", "steam": "Steam", "kodi": "Kodi",
    "retroarch": "RetroArch", "vlc": "VLC",
    "books": "Books", "news": "News", "stocks": "Stocks",
    "home": "Home", "weather": "Weather", "clock": "Clock",
}

_app_cache: list[str] | None = None


def installed_apps() -> list[str]:
    global _app_cache
    if _app_cache is None:
        names: set[str] = set()
        for d in ("/Applications", os.path.join(HOME, "Applications"),
                  "/Applications/Utilities"):
            try:
                for e in os.listdir(d):
                    if e.endswith(".app"):
                        names.add(e[:-4])
            except FileNotFoundError:
                pass
        _app_cache = sorted(names)
    return _app_cache


def resolve_app(spoken: str) -> str | None:
    """Turn 'chrome' / 'notes' / 'system settings' into a real app name.

    Fuzzy: aliases, singulars, exact installed names, then substring.
    Used for FINAL transcripts, where acting on a best guess is fine.
    """
    s = spoken.strip().lower()
    if s in _APP_ALIASES:
        return _APP_ALIASES[s]
    singular = s[:-1] if s.endswith("s") else s
    if singular in _APP_ALIASES:
        return _APP_ALIASES[singular]
    apps = installed_apps()
    low = {a.lower(): a for a in apps}
    for key in (s, singular):
        if key in low:
            return low[key]
    for key in (s, singular):
        cands = [a for a in apps if key in a.lower()]
        if cands:
            cands.sort(key=len)
            return cands[0]
    return None


def resolve_app_exact(spoken: str) -> str | None:
    """Strict resolver for PARTIAL transcripts: alias or exact installed
    name only — no substring matching. This is the completion gate that
    stops 'open no' from firing as 'open Notes' mid-sentence."""
    s = spoken.strip().lower()
    if s in _APP_ALIASES:
        return _APP_ALIASES[s]
    singular = s[:-1] if s.endswith("s") else s
    if singular in _APP_ALIASES:
        return _APP_ALIASES[singular]
    low = {a.lower(): a for a in installed_apps()}
    return low.get(s) or low.get(singular)


# ---------------------------------------------------------------- actions

def act_open_app(name: str) -> None:
    app = resolve_app(name)
    if not app:
        say(f"I couldn't find an app called {name}")
        return
    shell(["open", "-a", app])
    say(f"Opening {app}")


def act_quit_app(name: str) -> None:
    app = resolve_app(name)
    if not app:
        say(f"I couldn't find an app called {name}")
        return
    if app == "Finder":
        say("I won't quit the Finder")
        return
    applescript(f'tell application "{esc(app)}" to quit')
    say(f"Quitting {app}")


def act_keystroke(keys: str, using: str = "") -> None:
    mod = f" using {{{using}}}" if using else ""
    applescript(f'tell application "System Events" to keystroke "{esc(keys)}"{mod}')


def act_key_code(code: int, using: str = "") -> None:
    mod = f" using {{{using}}}" if using else ""
    applescript(f'tell application "System Events" to key code {code}{mod}')


def act_type_text(text: str) -> None:
    applescript(f'tell application "System Events" to keystroke "{esc(text)}"')
    log(f"typed {len(text)} chars")


def act_open_url(url: str) -> None:
    if not re.match(r"https?://", url):
        url = "https://" + url
    shell(["open", url])
    say(f"Opening {url}")


def act_web_search(query: str) -> None:
    q = urllib.parse.quote_plus(query)
    shell(["open", f"https://www.google.com/search?q={q}"])
    say(f"Searching for {query}")


def act_set_volume(level: int) -> None:
    level = max(0, min(100, level))
    applescript(f"set volume output volume {level}")
    say(f"Volume {level}")


def act_volume_delta(delta: int) -> None:
    cur = applescript("output volume of (get volume settings)")
    try:
        new = max(0, min(100, int(cur) + delta))
    except ValueError:
        new = 50
    act_set_volume(new)


def act_mute(muted: bool) -> None:
    applescript(
        "set volume with output muted" if muted else "set volume without output muted"
    )
    say("Muted" if muted else "Unmuted")


def act_media(cmd: str) -> None:
    # F7/F8/F9 double as prev/play/next on Mac keyboards with default
    # "media keys" behavior. Key codes: F7=98, F8=100, F9=101.
    codes = {"playpause": 100, "next": 101, "previous": 98}
    act_key_code(codes[cmd])
    say({"playpause": "Play pause", "next": "Next", "previous": "Previous"}[cmd])


def act_lock() -> None:
    act_key_code(12, "control down, command down")  # Ctrl-Cmd-Q
    say("Locking")


def act_sleep() -> None:
    shell(["pmset", "sleepnow"])
    say("Sleeping")


def act_dark_mode(on: bool) -> None:
    applescript(
        'tell application "System Events" to tell appearance preferences to '
        f"set dark mode to {str(on).lower()}"
    )
    say("Dark mode on" if on else "Light mode on")


def act_screenshot(mode: str) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(HOME, "Desktop", f"voice-screenshot-{ts}.png")
    if mode == "selection":
        shell(["screencapture", "-i", path])
    elif mode == "window":
        shell(["screencapture", "-iw", path])
    else:
        shell(["screencapture", "-x", path])
    say("Screenshot saved to the desktop")


def act_empty_trash() -> None:
    applescript('tell application "Finder" to empty the trash')
    say("Trash emptied")


def act_wifi(on: bool) -> None:
    shell(["networksetup", "-setairportpower", "en0", "on" if on else "off"])
    say(f"Wi-Fi {'on' if on else 'off'}")


_SETTINGS_PANES = {
    "wifi": "com.apple.wifi-settings",
    "wi-fi": "com.apple.wifi-settings",
    "bluetooth": "com.apple.Bluetooth-settings",
    "display": "com.apple.Displays-settings",
    "sound": "com.apple.Sound-settings",
    "network": "com.apple.Network-settings",
    "keyboard": "com.apple.Keyboard-settings",
    "mouse": "com.apple.Mouse-settings",
    "trackpad": "com.apple.Trackpad-settings",
    "privacy": "com.apple.settings.PrivacySecurity",
    "battery": "com.apple.Battery-settings",
    "general": "com.apple.GeneralSettings",
    "appearance": "com.apple.Appearance-settings",
    "wallpaper": "com.apple.Wallpaper-settings",
    "notifications": "com.apple.Notifications-settings",
    "focus": "com.apple.Focus-settings",
    "screen time": "com.apple.Screen-Time-settings",
}


def act_settings_pane(topic: str) -> None:
    pane = _SETTINGS_PANES.get(topic)
    if pane:
        shell(["open", f"x-apple.systempreferences:{pane}"])
        say(f"Opening {topic} settings")
    else:
        shell(["open", "-a", "System Settings"])
        say("Opening settings")


def act_timer(amount: int, unit: str) -> None:
    seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[unit]

    def ring():
        time.sleep(seconds)
        say("Timer done")
        for _ in range(3):
            shell(["afplay", "/System/Library/Sounds/Glass.aiff"])

    threading.Thread(target=ring, daemon=True).start()
    say(f"Timer set for {amount} {unit}{'s' if amount != 1 else ''}")


def act_calculate(expr: str) -> None:
    if not re.fullmatch(r"[\d\s+\-*/().%^]+", expr):
        say("I can only calculate plain arithmetic")
        return
    try:
        val = eval(expr.replace("^", "**"), {"__builtins__": {}}, {})  # noqa: S307
        say(f"{val:g}")
    except Exception:
        say("Couldn't calculate that")


def act_time() -> None:
    say(datetime.now().strftime("It's %-I:%M %p"))


def act_date() -> None:
    say(datetime.now().strftime("It's %A, %B %-d"))


# ---------------------------------------------------------------- xa11y UI actions (Tier 0 execution for real UI nodes)

_xa11y_mod = None


def _load_xa11y():
    """Import xa11y lazily; return None (once) if not installed."""
    global _xa11y_mod
    if _xa11y_mod is None:
        try:
            import xa11y as _m

            _xa11y_mod = _m
        except ImportError:
            _xa11y_mod = False
            log("xa11y not installed — UI-click commands disabled "
                "(pip install xa11y)")
    return _xa11y_mod or None


def frontmost_app() -> str:
    return applescript(
        'tell application "System Events" to get name of first '
        "application process whose frontmost is true"
    )


def _press_first(app_name: str, roles: list[str], name: str) -> None:
    xa = _load_xa11y()
    if xa is None:
        say("UI clicking needs the xa11y package: pip install xa11y")
        return
    if not app_name:
        say("I couldn't tell which app is in front")
        return
    safe = name.replace("'", "").strip()
    if not safe:
        say("Click what?")
        return
    app = xa.App.by_name(app_name)
    for role in roles:
        try:
            els = app.locator(f"{role}[name*='{safe}']").elements()
        except Exception as e:  # noqa: BLE001
            log(f"xa11y query failed ({role}): {e}")
            continue
        if els:
            try:
                els[0].press()
            except Exception as e:  # noqa: BLE001
                say("Couldn't click that")
                log(f"xa11y press failed: {e}")
                return
            say(f"Clicked {safe}")
            return
    say(f"No control matching {safe} in {app_name}")


def act_click_button(name: str) -> None:
    _press_first(frontmost_app(), ["button"], name)


def act_click_link(name: str) -> None:
    _press_first(frontmost_app(), ["link"], name)


def act_click_any(name: str) -> None:
    _press_first(frontmost_app(), ["button", "link", "checkbox"], name)


# ---------------------------------------------------------------- confirmation

def confirm_spoken(action_desc: str, audio_fn) -> bool:
    say(f"Say 'yes' to confirm: {action_desc}")
    audio = audio_fn(4.0)
    text = transcribe(audio).lower()
    log(f"confirmation heard: {text!r}")
    return "yes" in text or "confirm" in text or "do it" in text


# ---------------------------------------------------------------- Tier 0: regex router + completion gating
# _p(rx, name, partial_ok): partial_ok=True ONLY for commands that are safe
# to fire mid-sentence on a growing partial transcript. Free-text payloads
# (type X, search for X, click names, numbers that can grow) are NEVER
# partial-safe — they wait for end-of-speech.

_PATTERNS: list[tuple[re.Pattern, str, bool]] = []


def _p(rx: str, name: str, partial_ok: bool = False) -> None:
    _PATTERNS.append((re.compile(rx, re.IGNORECASE), name, partial_ok))


# --- apps (specific "open X settings" / "open trash" BEFORE generic open)
_p(r"^open (.+) settings$", "settings", True)
_p(r"^open trash$", "open_trash", True)
_p(r"^(open|launch|start) the (.+?) (app|application)$", "open_app", True)
_p(r"^(open|launch|start) (.+)$", "open_app", True)
_p(r"^quit( the)? (.+)$", "quit_app", True)
_p(r"^close the app (.+)$", "quit_app", True)
# --- windows / tabs
_p(r"^close( the)? (window|tab)$", "close_window", True)
_p(r"^(minimize|minimise)( the window)?$", "minimize", True)
_p(r"^(fullscreen|full screen|make it full screen)$", "fullscreen", True)
_p(r"^hide( the app)?$", "hide", True)
# --- typing & keys (type/dictate carry free text: final-only)
_p(r"^type (.+)$", "type_text")
_p(r"^dictate (.+)$", "type_text")
_p(r"^press (enter|return|escape|tab|space|delete)$", "press_key", True)
_p(r"^copy$", "copy_", True)
_p(r"^paste$", "paste_", True)
_p(r"^cut$", "cut_", True)
_p(r"^undo$", "undo_", True)
_p(r"^redo$", "redo_", True)
_p(r"^save$", "save_", True)
_p(r"^select all$", "select_all", True)
# --- UI clicks via accessibility tree (free-text names: final-only)
_p(r"^(click|press)( the)? (.+?) button$", "click_button")
_p(r"^click (the )?(.+?) link$", "click_link")
_p(r"^click (the )?(.+)$", "click_any")
# --- web (free text: final-only)
_p(r"^(search|google|look up)( the web)? for (.+)$", "web_search")
_p(r"^(search|google|look up) (.+)$", "web_search")
_p(r"^(go to|visit|open website) ([a-z0-9][a-z0-9.\-]*\.[a-z]{2,}.*)$", "open_url")
# --- volume / brightness / media
_p(r"^(volume|sound) up$", "vol_up", True)
_p(r"^(volume|sound) down$", "vol_down", True)
_p(r"^set (the )?volume to (\d+)( percent)?$", "vol_set")
_p(r"^louder$", "vol_up", True)
_p(r"^quieter$", "vol_down", True)
_p(r"^(mute|unmute|mute the sound|unmute the sound)$", "mute_toggle", True)
_p(r"^brightness up$", "bright_up", True)
_p(r"^brightness down$", "bright_down", True)
_p(r"^set (the )?brightness to (\d+)( percent)?$", "bright_set")
_p(r"^(play|pause|play or pause|play pause|resume)$", "media_playpause", True)
_p(r"^next( (track|song))?$", "media_next", True)
_p(r"^previous( (track|song))?$", "media_prev", True)
# --- system power (destructive ones need confirmation: final-only)
_p(r"^lock( (the )?(computer|mac|screen))?$", "lock", True)
_p(r"^(sleep|put (the )?(mac|computer) to sleep)$", "sleep", True)
_p(r"^(shut down|shutdown|power off)( the (mac|computer))?$", "shutdown")
_p(r"^restart( the (mac|computer))?$", "restart")
_p(r"^log ?out$", "logout")
# --- system toggles
_p(r"^dark mode$", "dark_on", True)
_p(r"^light mode$", "dark_off", True)
_p(r"^(turn on|enable) dark mode$", "dark_on", True)
_p(r"^(turn off|disable) dark mode$", "dark_off", True)
_p(r"^(turn|switch) wi-?fi (on|off)$", "wifi", True)
_p(r"^(take a )?screenshot$", "shot_full", True)
_p(r"^(take a )?screenshot of (the )?window$", "shot_window", True)
_p(r"^screenshot (a )?selection$", "shot_selection", True)
_p(r"^(empty|empty the) trash$", "empty_trash")
# --- timers / time / calc (time & date before calculate!)
_p(r"^(set|start)( a)? timer for (\d+) (seconds?|minutes?|hours?)$", "timer")
_p(r"^what time is it\??$", "time", True)
_p(r"^what('s| is) the date\??$", "date", True)
_p(r"^(calculate|what is|what's) (.+)$", "calculate")
# --- meta
_p(r"^(help|what can you say|list commands|commands)$", "help", True)


def _partial_complete(name: str, m: re.Match) -> bool:
    """Completion gate for PARTIAL transcripts. Even for partial-safe
    patterns, only fire when the payload is terminal-complete:

    - open/quit <app>: the app phrase must resolve EXACTLY (alias or full
      installed name). 'open no' must NOT fire as 'open Notes'.
    - open <topic> settings: topic must be a known settings pane.
    - everything else partial-safe: the regex consumed the whole partial,
      which for closed enums is enough.
    """
    if name in ("open_app", "quit_app"):
        # open_app patterns keep the app phrase in group 2 in both variants;
        # quit_app variants keep it in the last group.
        phrase = m.group(2) if name == "open_app" else m.group(m.lastindex)
        return resolve_app_exact(phrase) is not None
    if name == "settings":
        return m.group(1).strip().lower() in _SETTINGS_PANES
    return True


def route(text: str, partial: bool = False):
    """Tier 0 router. Returns (name, match) or None.

    partial=True: for streaming STT chunks. Only partial-safe patterns are
    considered, the match must consume the ENTIRE partial, and the
    completion gate must pass.
    """
    t = text.strip().rstrip(".!?").strip()
    if not t:
        return None
    for rx, name, pok in _PATTERNS:
        if partial and not pok:
            continue
        m = rx.match(t)
        if not m:
            continue
        if partial and m.end() != len(t):
            continue
        if partial and not _partial_complete(name, m):
            continue
        return name, m
    return None


class PartialSession:
    """Feeds growing partial transcripts to Tier 0 with dedup: each distinct
    command fires at most once per utterance, the moment it becomes
    complete. This is what a whisper.cpp --stream loop would drive."""

    def __init__(self, on_fire):
        self.on_fire = on_fire  # fn(name, match)
        self.fired: set[tuple[str, str]] = set()

    def feed(self, partial_text: str) -> bool:
        r = route(partial_text, partial=True)
        if not r:
            return False
        name, m = r
        key = (name, m.group(0).lower())
        if key in self.fired:
            return False
        self.fired.add(key)
        self.on_fire(name, m)
        return True


def _num(m: re.Match, i: int) -> int:
    return int(m.group(i))


def execute_match(name: str, m: re.Match, confirm_audio_fn=None,
                  allow_destructive: bool = False) -> None:
    """Run the action for a routed (name, match)."""
    try:
        if name == "open_app":
            # both "open X" patterns keep the app phrase in group 2
            act_open_app(m.group(2))
        elif name == "quit_app":
            act_quit_app(m.group(m.lastindex))
        elif name == "close_window":
            act_keystroke("w", "command down"); say("Closed")
        elif name == "minimize":
            act_keystroke("m", "command down")
        elif name == "fullscreen":
            act_key_code(3, "control down, command down")  # Ctrl-Cmd-F
        elif name == "hide":
            act_keystroke("h", "command down")
        elif name == "type_text":
            act_type_text(m.group(1)); say("Typed")
        elif name == "press_key":
            keymap = {"enter": "return", "return": "return", "escape": "escape",
                      "tab": "tab", "space": " ", "delete": "delete"}
            act_keystroke(keymap[m.group(1).lower()] if m.group(1).lower() != " " else " ")
        elif name == "copy_":
            act_keystroke("c", "command down")
        elif name == "paste_":
            act_keystroke("v", "command down")
        elif name == "cut_":
            act_keystroke("x", "command down")
        elif name == "undo_":
            act_keystroke("z", "command down")
        elif name == "redo_":
            act_keystroke("z", "command down, shift down")
        elif name == "save_":
            act_keystroke("s", "command down"); say("Saved")
        elif name == "select_all":
            act_keystroke("a", "command down")
        elif name == "click_button":
            act_click_button(m.group(3))
        elif name == "click_link":
            act_click_link(m.group(2))
        elif name == "click_any":
            act_click_any(m.group(2))
        elif name == "web_search":
            act_web_search(m.group(m.lastindex))
        elif name == "open_url":
            act_open_url(m.group(2))
        elif name == "vol_up":
            act_volume_delta(10)
        elif name == "vol_down":
            act_volume_delta(-10)
        elif name == "vol_set":
            act_set_volume(_num(m, 2))
        elif name == "mute_toggle":
            cur = applescript("output muted of (get volume settings)")
            act_mute(cur != "true")
        elif name == "bright_up":
            act_key_code(120)  # F2 = brightness up
        elif name == "bright_down":
            act_key_code(122)  # F1 = brightness down
        elif name == "bright_set":
            say("Brightness can only go up or down by voice on this Mac")
        elif name == "media_playpause":
            act_media("playpause")
        elif name == "media_next":
            act_media("next")
        elif name == "media_prev":
            act_media("previous")
        elif name == "lock":
            act_lock()
        elif name == "sleep":
            act_sleep()
        elif name in ("shutdown", "restart", "logout", "empty_trash"):
            desc = {"shutdown": "shutting down", "restart": "restarting",
                    "logout": "logging out",
                    "empty_trash": "emptying the trash"}[name]
            ok = allow_destructive or (
                confirm_audio_fn is not None
                and confirm_spoken(desc, confirm_audio_fn)
            )
            if not ok:
                say(f"Not {desc} without confirmation")
                return
            {"shutdown": lambda: shell(["osascript", "-e",
                'tell application "System Events" to shut down']),
             "restart": lambda: shell(["osascript", "-e",
                'tell application "System Events" to restart']),
             "logout": lambda: shell(["osascript", "-e",
                'tell application "System Events" to log out']),
             "empty_trash": act_empty_trash}[name]()
        elif name == "dark_on":
            act_dark_mode(True)
        elif name == "dark_off":
            act_dark_mode(False)
        elif name == "wifi":
            act_wifi(m.group(2) == "on")
        elif name == "settings":
            act_settings_pane(m.group(1).lower())
        elif name == "shot_full":
            act_screenshot("full")
        elif name == "shot_window":
            act_screenshot("window")
        elif name == "shot_selection":
            act_screenshot("selection")
        elif name == "open_trash":
            shell(["open", os.path.join(HOME, ".Trash")]); say("Opening trash")
        elif name == "timer":
            unit = m.group(4).rstrip("s")
            act_timer(int(m.group(3)), unit)
        elif name == "calculate":
            act_calculate(m.group(2))
        elif name == "time":
            act_time()
        elif name == "date":
            act_date()
        elif name == "help":
            cmds = sorted({n for _, n, _ in _PATTERNS})
            print("Commands: " + ", ".join(cmds))
            say("I printed the command list in the terminal")
        else:
            return  # unknown handler name: treat as unrouted
    except Exception as e:  # noqa: BLE001
        say("That didn't work")
        log(f"action failed: {e}")


# ---------------------------------------------------------------- Tier 1: local LLM fallback (Ollama, JSON mode)
# Fires ONLY when Tier 0 misses. Expected ~0.7-1 s warm on an M4 Mac mini
# for qwen2.5:1.5b — measured medians, not marketing numbers. Keep the model
# resident (keep_alive) or the first call of the day eats a 20-30 s cold
# start. Confidence here is SELF-REPORTED by the model, not calibrated like
# Jev — treat TIER1_MIN_CONFIDENCE as a rough heuristic, not a guarantee.

_TIER1_SYSTEM = (
    "You route voice commands to Mac actions. Reply with ONLY JSON, no other text: "
    '{"action": "<name>", "params": {...}, "confidence": 0.0-1.0}. '
    "Valid actions and params: "
    "open_app {app: name}, quit_app {app: name}, "
    "type_text {text: string}, web_search {query: string}, open_url {url: string}, "
    "set_volume {level: 0-100}, volume_up {}, volume_down {}, mute_toggle {}, "
    "media {op: playpause|next|previous}, lock {}, sleep {}, "
    "brightness_up {}, brightness_down {}, screenshot {}, "
    "dark_mode {on: true|false}, wifi {on: true|false}, "
    "timer {amount: number, unit: seconds|minutes|hours}, "
    "calculate {expr: string}, click_button {name: string}, click_link {name: string}. "
    "If the input is a question, small talk, or you are unsure, reply "
    '{"action": "none", "params": {}, "confidence": 0}.'
)

_TIER1_ACTIONS = {
    "open_app", "quit_app", "type_text", "web_search", "open_url",
    "set_volume", "volume_up", "volume_down", "mute_toggle", "media",
    "lock", "sleep", "brightness_up", "brightness_down", "screenshot",
    "dark_mode", "wifi", "timer", "calculate", "click_button", "click_link",
}

_ollama_ok: bool | None = None  # None=untried, False=unreachable (cached)


def ollama_route(text: str):
    """Tier 1: ask the local model for a typed action.

    Returns (action, params, confidence) or None on miss/unreachable.
    """
    global _ollama_ok
    if not OLLAMA_TIER1:
        return None
    if _ollama_ok is False:
        return None
    body = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "keep_alive": "30m",  # stay resident: no 20s cold start mid-session
        "options": {"temperature": 0, "num_predict": 80},
        "messages": [
            {"role": "system", "content": _TIER1_SYSTEM},
            {"role": "user", "content": text},
        ],
        "stream": False,
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            OLLAMA_HOST + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        _ollama_ok = True
    except Exception as e:  # noqa: BLE001
        _ollama_ok = False
        log(f"Tier 1 unavailable (Ollama not reachable at {OLLAMA_HOST}): {e}")
        return None
    dt = time.time() - t0
    try:
        parsed = json.loads(data["message"]["content"])
    except Exception:  # noqa: BLE001
        log("Tier 1 returned unparseable JSON")
        return None
    action = str(parsed.get("action", "none"))
    try:
        conf = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    params = parsed.get("params", {}) or {}
    log(f"Tier 1 ({OLLAMA_MODEL}) -> {action} {params} "
        f"conf={conf:.2f} in {dt:.2f}s")
    if action == "none" or action not in _TIER1_ACTIONS or conf < TIER1_MIN_CONFIDENCE:
        return None
    return action, params, conf


def dispatch_tier1(action: str, params: dict) -> None:
    """Execute a Tier 1 JSON decision using the same act_* primitives.
    Every enum/param is validated — JSON mode guarantees shape, NOT sense."""
    p = params.get
    if action == "open_app":
        act_open_app(str(p("app", "")))
    elif action == "quit_app":
        act_quit_app(str(p("app", "")))
    elif action == "type_text":
        act_type_text(str(p("text", ""))); say("Typed")
    elif action == "web_search":
        act_web_search(str(p("query", "")))
    elif action == "open_url":
        act_open_url(str(p("url", "")))
    elif action == "set_volume":
        try:
            act_set_volume(int(p("level", 50)))
        except (TypeError, ValueError):
            say("I didn't get a volume level")
    elif action == "volume_up":
        act_volume_delta(10)
    elif action == "volume_down":
        act_volume_delta(-10)
    elif action == "mute_toggle":
        cur = applescript("output muted of (get volume settings)")
        act_mute(cur != "true")
    elif action == "media":
        op = str(p("op", "playpause"))
        act_media(op if op in ("playpause", "next", "previous") else "playpause")
    elif action == "lock":
        act_lock()
    elif action == "sleep":
        act_sleep()
    elif action == "brightness_up":
        act_key_code(120)
    elif action == "brightness_down":
        act_key_code(122)
    elif action == "screenshot":
        act_screenshot("full")
    elif action == "dark_mode":
        act_dark_mode(bool(p("on", True)))
    elif action == "wifi":
        act_wifi(bool(p("on", True)))
    elif action == "timer":
        try:
            amount = int(p("amount", 1))
        except (TypeError, ValueError):
            amount = 1
        unit = str(p("unit", "minutes")).rstrip("s")
        if unit not in ("second", "minute", "hour"):
            unit = "minute"
        act_timer(amount, unit)
    elif action == "calculate":
        act_calculate(str(p("expr", "")))
    elif action == "click_button":
        act_click_button(str(p("name", "")))
    elif action == "click_link":
        act_click_link(str(p("name", "")))
    else:
        say("I couldn't map that to an action")


# ---------------------------------------------------------------- Tier 2: Gemini free-tier Q&A (unchanged)

def gemini_answer(prompt: str) -> bool:
    """Answer a free-form question with Gemini's free API tier, spoken aloud."""
    body = {
        "system_instruction": {
            "parts": [{"text": (
                "You are a concise Mac voice assistant. Answer in one or two "
                "short spoken sentences. Plain text only, no markdown, no lists."
            )}]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 120, "temperature": 0.3},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        say(text)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"gemini fallback failed: {e}")
        say("I didn't understand, and my backup didn't answer")
        return False


# ---------------------------------------------------------------- the cascade

def handle_command(text: str, confirm_audio_fn=None,
                   allow_destructive: bool = False,
                   quiet_miss: bool = False) -> bool:
    """Route one transcript through the tiers. Returns True if handled.

    quiet_miss=True (always-listening mode): a total miss is logged, not
    spoken, so background chatter never makes the Mac talk to itself.
    """
    t = text.strip().rstrip(".!?").strip()
    if not t:
        return False
    log(f"heard: {t!r}")

    # Tier 0: instant regex (final transcript — no gating needed)
    r = route(t, partial=False)
    if r:
        name, m = r
        log(f"Tier 0 hit: {name}")
        execute_match(name, m, confirm_audio_fn, allow_destructive)
        return True

    # Tier 1: local 1.5B fallback (only on Tier 0 miss)
    t1 = ollama_route(t)
    if t1:
        action, params, conf = t1
        try:
            dispatch_tier1(action, params)
        except Exception as e:  # noqa: BLE001
            say("That didn't work")
            log(f"tier 1 action failed: {e}")
        return True

    # Tier 2: free Gemini Q&A (optional)
    if GEMINI_API_KEY:
        return gemini_answer(t)
    if quiet_miss:
        log("no tier matched; ignoring quietly (always-listening)")
    else:
        say("I didn't understand. Say 'help' to hear what I can do")
    return False


# ---------------------------------------------------------------- CLI

def cmd_list() -> None:
    print("free_voice.py — say any of these (examples):\n")
    examples = [
        "open notes / open chrome / open system settings",
        "quit spotify",
        "close window · minimize · fullscreen · hide",
        "type hello world / dictate dear mom,",
        "press enter / copy / paste / undo / save / select all",
        "click the Reply button / click the Docs link  (needs xa11y + perms)",
        "search best pizza near me / go to youtube.com",
        "volume up / volume down / set volume to 30 / mute",
        "brightness up / brightness down",
        "play / pause / next / previous",
        "lock · sleep · shut down · restart · log out  (ask first)",
        "dark mode / light mode",
        "turn wifi off / turn wifi on",
        "open bluetooth settings / open display settings / ...",
        "screenshot / screenshot of window / screenshot selection",
        "empty trash  (asks first) · open trash",
        "set a timer for 5 minutes",
        "calculate 12 * 8 + 3 · what time is it · what is the date",
    ]
    for e in examples:
        print("  " + e)
    print("\nTiers: 0 = instant regex (always) · 1 = local Ollama 1.5B on miss "
          f"({'on' if OLLAMA_TIER1 else 'off'}) · 2 = Gemini free tier "
          f"({'on' if GEMINI_API_KEY else 'off'}).")


def demo_partials(text: str) -> None:
    """Simulate growing STT partials to show Tier 0 completion gating:
    each prefix is fed as a partial; a command fires at most once, only
    when it becomes terminal-complete."""
    print(f"simulating partials for: {text!r}\n")

    def on_fire(name, m):
        log(f"  *** FIRED Tier 0: {name} <- {m.group(0)!r}")
        execute_match(name, m)  # dry-run safe: actions only log

    session = PartialSession(on_fire)
    words = text.split()
    for i in range(1, len(words) + 1):
        partial = " ".join(words[:i])
        fired = session.feed(partial)
        print(f"  [{i}/{len(words)}] {partial!r:45} -> "
              f"{'FIRED' if fired else 'waiting...'}")
    print("\nfinal transcript also runs Tier 1/2 if Tier 0 never fired.")


def on_utterance(audio, quiet_miss: bool = False) -> None:
    try:
        text = transcribe(audio)
    except Exception as e:  # noqa: BLE001
        log(f"transcription failed: {e}")
        if not quiet_miss:
            say("I didn't catch that")
        return
    if not text:
        log("empty transcript")
        return
    handle_command(text, confirm_audio_fn=record_fixed, quiet_miss=quiet_miss)


def main() -> None:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Free voice control for macOS")
    ap.add_argument("--text", help="run one command from text, no mic")
    ap.add_argument("--partial", metavar="TEXT",
                    help="simulate growing partial transcripts for TEXT "
                         "(demos Tier 0 completion gating)")
    ap.add_argument("--once", type=float, metavar="SEC",
                    help="record SEC seconds once, no hotkey")
    ap.add_argument("--list", action="store_true", help="show commands")
    ap.add_argument("--dry-run", action="store_true",
                    help="print actions instead of running them")
    ap.add_argument("--yes", action="store_true",
                    help="allow destructive actions in --text mode")
    ap.add_argument("--always", action="store_true",
                    help="listen continuously with voice activity detection; "
                         "Ctrl-C quits (no wake word — any speech triggers)")
    ap.add_argument("--sensitivity", type=float, default=3.0, metavar="X",
                    help="always-listen mic sensitivity multiplier "
                         "(higher = easier to trigger; default 3.0)")
    ap.add_argument("--mic", metavar="NAME",
                    help="use the input device whose name contains NAME "
                         "(e.g. --mic iPhone); overrides VOICE_MIC")
    args = ap.parse_args()
    DRY_RUN = args.dry_run
    global VOICE_MIC
    if args.mic:
        VOICE_MIC = args.mic

    if args.list:
        cmd_list()
        return
    if args.partial:
        demo_partials(args.partial)
        return
    if args.text:
        handle_command(args.text, allow_destructive=args.yes)
        return
    log(f"microphone: {describe_input_device()}")
    if args.once:
        on_utterance(record_fixed(args.once))
        return
    if args.always:
        always_listen_loop(lambda audio: on_utterance(audio, quiet_miss=True),
                           sensitivity=args.sensitivity)
        return
    push_to_talk_loop(on_utterance)


if __name__ == "__main__":
    main()
