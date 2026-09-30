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
      -> Tier 1 (Tier 0 miss only): local Ollama qwen3-vl:8b vision-language
             model in JSON mode (~1-2 s warm, free) for novel phrasing
             ("could you be a dear and open my browser thing"). Self-reported
             confidence < 0.5 = miss. NOTE: this is NOT calibrated like
             Jev — it's a heuristic. Non-thinking mode (think=false) so it
             answers immediately instead of reasoning first.
      -> Tier 2 (Tier 1 miss/unsure only): Gemini API free tier for
             open-ended questions (needs GEMINI_API_KEY, still $0), with
             Google Search grounding for fresh answers. On 429/quota errors
             it falls back to the local model automatically.
      -> execution: AppleScript/shell for system actions, xa11y over the
             macOS Accessibility tree for real UI clicks ("click the Reply
             button") — no brittle pixel coordinates. When the tree has no
             match, the vision model locates the element on a screenshot and
             clicks its center ("what's on my screen" describes the display).
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
Optional: `brew install ollama && ollama pull qwen3-vl:8b` (Tier 1 + vision;
8GB minis: `ollama pull qwen3-vl:4b` and set OLLAMA_MODEL=qwen3-vl:4b),
`pip install xa11y` (UI-click commands).
Permissions on the Mac: Microphone + Accessibility (+ Input Monitoring for
the push-to-talk hotkey) for the launching terminal. macOS 26+: xa11y also
needs Screen Recording to see window contents.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
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
# Default is qwen3-vl:8b — a vision-language model, so one local model covers
# routing, Q&A, AND screen understanding. 8GB minis: use qwen3-vl:4b instead.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b")
OLLAMA_TIER1 = os.environ.get("OLLAMA_TIER1", "1") == "1"
TIER1_MIN_CONFIDENCE = float(os.environ.get("TIER1_MIN_CONFIDENCE", "0.5"))
# Microphone selection: case-insensitive substring matched against input
# device names, e.g. VOICE_MIC=iPhone uses the Continuity microphone whenever
# the iPhone is in range, falling back to the system default otherwise.
# Overridable per-run with --mic.
VOICE_MIC = os.environ.get("VOICE_MIC", "")
VOICE_USER_EMAIL = os.environ.get("VOICE_USER_EMAIL", "").strip()
DRY_RUN = False


def log(msg: str) -> None:
    print(f"[free-voice] {msg}", flush=True)


# ---------------------------------------------------------------- health tracking
_START_TIME = time.time()
_last_error = ""  # short human-readable note of the most recent backend failure


def note_error(msg: str) -> None:
    """Remember the latest backend failure for the 'are you working' status."""
    global _last_error
    _last_error = f"{datetime.now():%H:%M} — {msg}"
    log(f"error noted: {msg}")


# ---------------------------------------------------------------- speech out

_say_proc = None  # in-flight `say` process, so new speech cuts off the old


def say(text: str, blocking: bool = False) -> None:
    """Speak text. Non-blocking by default: fire-and-forget Popen so the action
    feels instant instead of waiting ~2s for the voice to finish. Any in-flight
    speech is terminated first so rapid commands don't talk over each other.
    Pass blocking=True when the full prompt must be heard before continuing
    (spoken confirmations)."""
    global _say_proc
    log(f"say: {text}")
    if DRY_RUN:
        return
    try:
        if _say_proc is not None and _say_proc.poll() is None:
            _say_proc.terminate()
            _say_proc = None
        if blocking:
            subprocess.run(["say", text], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            _say_proc = subprocess.Popen(
                ["say", text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # e.g. test environments without the macOS `say` binary


def play_chime(sound_name: str = "Tink.aiff") -> None:
    """Play a short macOS audio notification sound non-blockingly."""
    path = f"/System/Library/Sounds/{sound_name}"
    if os.path.exists(path):
        try:
            subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


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


def _usable_input(want: str | None = None):
    """Check for a usable microphone without raising.

    Returns (usable: bool, index: int | None); index None means "system
    default input". Honors VOICE_MIC (or the `want` override): when set,
    only a device whose name contains it counts — otherwise the call waits
    for that specific mic rather than grabbing some other one.
    """
    try:
        import sounddevice as sd
    except Exception:
        return False, None
    want = VOICE_MIC.strip().lower() if want is None else want.strip().lower()
    try:
        if want:
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0 \
                        and want in str(d.get("name", "")).lower():
                    return True, i
            return False, None
        sd.query_devices(kind="input")  # raises when no input devices exist
        return True, None
    except Exception:
        return False, None


def resolve_input_device(want: str = ""):
    """Return a sounddevice input device index, or None for the system default.

    `want` (default VOICE_MIC) is a case-insensitive substring matched against
    input device names. Returns None when unset (use default) or when no
    usable mic exists — use _usable_input() to tell those apart.
    """
    usable, idx = _usable_input(want or None)
    return idx if usable else None


def describe_input_device() -> str:
    """Human-readable name of the mic that will be used, for startup logging."""
    usable, idx = _usable_input()
    if not usable:
        want = VOICE_MIC.strip()
        return (f"none found — waiting for {want!r}") if want \
            else "none found — waiting for a microphone"
    try:
        import sounddevice as sd
        dev = sd.query_devices(idx) if idx is not None \
            else sd.query_devices(kind="input")
        return str(dev.get("name", "?"))
    except Exception:
        return "?"


def wait_for_input_device(poll_s: float = 3.0):
    """Block until a usable microphone appears; return its device index.

    Honors VOICE_MIC (see _usable_input). Polls so a mic connected later —
    iPhone coming in range, USB mic plugged in — is picked up automatically.
    Never raises for a missing mic; KeyboardInterrupt passes through.
    """
    want = VOICE_MIC.strip()
    announced = False
    while True:
        usable, idx = _usable_input()
        if usable:
            return idx
        if not announced:
            log("no microphone found — waiting for one to be connected…"
                + (f" (waiting for {want!r})" if want else ""))
            announced = True
        time.sleep(poll_s)


_last_no_mic_say = 0.0


def _notice_no_mic() -> None:
    """Friendly no-mic notice: always logged, spoken at most every 2 minutes."""
    global _last_no_mic_say
    log("no microphone found — connect one and try again "
        "(bring your iPhone nearby with Wi-Fi/Bluetooth on, or plug in a USB mic)")
    now = time.monotonic()
    if now - _last_no_mic_say > 120:
        _last_no_mic_say = now
        say("No microphone connected.")


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
        usable, _ = _usable_input()
        if not usable:
            result["mic_error"] = True
            return
        try:
            result["audio"] = record_while_held()
        except Exception as e:
            result["mic_error"] = True
            log(f"recording failed ({e})")

    def on_press(key):
        if stop.is_set():
            return False
        if key == keyboard.Key.alt_r and not held.is_set():
            held.set()
            _recording.set()
            play_chime("Tink.aiff")
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
            play_chime("Pop.aiff")
            log("transcribing...")
            for t in worker:
                t.join(timeout=30)
            worker.clear()
            if result.pop("mic_error", False):
                _notice_no_mic()
                return
            audio = result.pop("audio", None)
            if audio is not None and len(audio) > 1600:  # >0.1s of audio
                on_utterance(audio)
            else:
                log("too short, ignoring")

    log("push-to-talk ready: hold RIGHT OPTION, speak, release. Esc quits.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


# ------------------------------------------------------- always-listening

from concurrent.futures import ThreadPoolExecutor

ALWAYS_MODE: bool = False
_vision_pool: ThreadPoolExecutor | None = None


def _get_vision_pool() -> ThreadPoolExecutor:
    global _vision_pool
    if _vision_pool is None:
        _vision_pool = ThreadPoolExecutor(max_workers=1,
                                          thread_name_prefix="free-voice-vision")
    return _vision_pool


def _submit_vision_task(fn, *args, **kwargs):
    """Submit a slow vision task to the background thread pool in --always mode."""
    pool = _get_vision_pool()
    return pool.submit(fn, *args, **kwargs)


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

    frame_len = int(16000 * 0.03)  # 30 ms frames
    max_frames = int(15 / 0.03)   # 15 s safety cap per utterance

    log("always-listening: speak naturally, Ctrl-C quits. "
        "(no wake word — speech itself is the trigger)")
    try:
        while True:  # outer: re-acquire the mic if it vanishes
            idx = wait_for_input_device()
            vad = VoiceActivityDetector(sensitivity=sensitivity)
            capturing: list[np.ndarray] = []
            log(f"microphone: {describe_input_device()} — listening")
            play_chime("Tink.aiff")
            try:
                with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16",
                                       blocksize=frame_len,
                                       device=idx) as stream:
                    while True:
                        try:
                            data, _ = stream.read(frame_len)
                        except Exception as e:
                            log(f"microphone error ({e}) — waiting for it to come back…")
                            time.sleep(2)  # cooldown so a flapping device doesn't hot-spin
                            break
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
                                try:
                                    on_utterance(audio)
                                except Exception as e:
                                    log(f"command failed ({e}) — still listening")
                            time.sleep(0.5)  # cooldown so one sentence = one command
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(f"couldn't open microphone ({e}) — retrying…")
                time.sleep(2)
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


def _clean_name(s: str) -> str:
    """Normalize names: strip non-alphanumeric chars for matching (e.g. 'es de' -> 'esde', 'ES-DE' -> 'esde')."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _soundex(token: str) -> str:
    """Standard American Soundex algorithm for phonetic indexing."""
    token = re.sub(r"[^a-zA-Z]", "", token).upper()
    if not token:
        return ""
    mapping = {
        "B": "1", "F": "1", "P": "1", "V": "1",
        "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
        "D": "3", "T": "3",
        "L": "4",
        "M": "5", "N": "5",
        "R": "6",
    }
    encoded = token[0]
    last = mapping.get(token[0], "")
    for char in token[1:]:
        code = mapping.get(char, "")
        if code and code != last:
            encoded += code
            last = code
        elif not code:
            last = ""
    return (encoded + "000")[:4]


def _phonetic_key(s: str) -> str:
    """Compute concatenated Soundex representation for all tokens in a phrase."""
    tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", s) if t]
    return "".join(_soundex(t) for t in tokens)


def resolve_app(spoken: str) -> str | None:
    """Turn 'chrome' / 'notes' / 'es de' into a real app name.

    Fuzzy: aliases, singulars, exact installed names, normalized alphanumeric,
    substring, phonetic Soundex, then difflib similarity.
    Used for FINAL transcripts, where acting on a best guess is fine.
    """
    import difflib

    s = spoken.strip().lower()
    if s in _APP_ALIASES:
        return _APP_ALIASES[s]
    singular = s[:-1] if s.endswith("s") else s
    if singular in _APP_ALIASES:
        return _APP_ALIASES[singular]

    clean_s = _clean_name(s)
    clean_singular = _clean_name(singular)
    clean_aliases = {_clean_name(k): v for k, v in _APP_ALIASES.items()}
    if clean_s in clean_aliases:
        return clean_aliases[clean_s]

    apps = installed_apps()
    low = {a.lower(): a for a in apps}
    clean_map = {_clean_name(a): a for a in apps}

    # 1. Exact raw or normalized match
    for key in (s, singular):
        if key in low:
            return low[key]
    for key in (clean_s, clean_singular):
        if key in clean_map:
            return clean_map[key]

    # 2. Substring matching (raw first, then clean if >= 4 chars)
    for key in (s, singular):
        cands = [a for a in apps if key in a.lower()]
        if cands:
            cands.sort(key=len)
            return cands[0]

    if len(clean_s) >= 4:
        cands = [a for a in apps if clean_s in _clean_name(a)]
        if cands:
            cands.sort(key=len)
            return cands[0]

    # 3. Phonetic matching via Soundex (handles STT phonetic misspellings)
    phone_s = _phonetic_key(s)
    if phone_s:
        phone_map = {_phonetic_key(a): a for a in apps if _phonetic_key(a)}
        if phone_s in phone_map:
            return phone_map[phone_s]

    # 4. Fuzzy similarity matching on normalized strings
    matches = difflib.get_close_matches(clean_s, list(clean_map.keys()), n=1, cutoff=0.75)
    if matches:
        return clean_map[matches[0]]

    return None


def resolve_app_exact(spoken: str) -> str | None:
    """Strict resolver for PARTIAL transcripts: alias, exact installed name,
    or normalized alphanumeric match — no substring/fuzzy guessing. This is the
    completion gate that stops 'open no' from firing as 'open Notes' mid-sentence."""
    s = spoken.strip().lower()
    if s in _APP_ALIASES:
        return _APP_ALIASES[s]
    singular = s[:-1] if s.endswith("s") else s
    if singular in _APP_ALIASES:
        return _APP_ALIASES[singular]

    clean_s = _clean_name(s)
    clean_singular = _clean_name(singular)
    clean_aliases = {_clean_name(k): v for k, v in _APP_ALIASES.items()}
    if clean_s in clean_aliases:
        return clean_aliases[clean_s]

    apps = installed_apps()
    low = {a.lower(): a for a in apps}
    for key in (s, singular):
        if key in low:
            return low[key]

    clean_map = {_clean_name(a): a for a in apps}
    for key in (clean_s, clean_singular):
        if key in clean_map:
            return clean_map[key]

    return None


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


def act_force_quit_app(name: str) -> None:
    app = resolve_app(name)
    if not app:
        say(f"I couldn't find an app called {name}")
        return
    if app == "Finder":
        say("I won't force quit the Finder")
        return
    shell(["killall", app])
    say(f"Force quit {app}")


def act_rename_to(new_name: str) -> None:
    """Rename selected item in Finder/macOS: Return, type name, Return."""
    act_keystroke("\r")
    time.sleep(0.1)
    act_type_text(new_name)
    time.sleep(0.1)
    act_keystroke("\r")
    say(f"Renamed to {new_name}")


def act_minimize(name: str = "") -> None:
    if name:
        app = resolve_app(name)
        if not app:
            say(f"I couldn't find an app called {name}")
            return
        applescript(
            f'tell application "System Events" to tell process "{esc(app)}" to '
            'set value of attribute "AXMinimized" of every window to true'
        )
        say(f"Minimizing {app}")
        return
    act_keystroke("m", "command down")
    say("Minimized")


def act_hide(name: str = "") -> None:
    if name:
        app = resolve_app(name)
        if not app:
            say(f"I couldn't find an app called {name}")
            return
        applescript(f'tell application "System Events" to set visible of process "{esc(app)}" to false')
        say(f"Hiding {app}")
        return
    act_keystroke("h", "command down")
    say("Hidden")


def act_switch_app(name: str) -> None:
    """Focus or switch to an already running or installed app."""
    app = resolve_app(name)
    if not app:
        say(f"I couldn't find an app called {name}")
        return
    applescript(f'tell application "{esc(app)}" to activate')
    say(f"Switched to {app}")


def act_close_all_windows() -> None:
    """Close all open windows of the active application (Option-Command-W)."""
    act_keystroke("w", "option down, command down")
    say("Closed all windows")


def get_screen_bounds() -> tuple[int, int, int, int]:
    """Return visible screen bounds (x, y, w, h) taking menu bar and dock into account."""
    try:
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        total_h = screen.frame().size.height
        f = screen.visibleFrame()
        x = int(f.origin.x)
        y = int(total_h - (f.origin.y + f.size.height))
        w = int(f.size.width)
        h = int(f.size.height)
        return x, y, w, h
    except Exception:
        return 0, 30, 1920, 1050


def act_snap_window(side: str) -> None:
    """Snap frontmost window to left half, right half, maximize, or center."""
    x, y, w, h = get_screen_bounds()
    if side == "left":
        pos = (x, y)
        size = (w // 2, h)
        label = "Snapped left"
    elif side == "right":
        pos = (x + w // 2, y)
        size = (w - w // 2, h)
        label = "Snapped right"
    elif side in ("maximize", "max"):
        pos = (x, y)
        size = (w, h)
        label = "Maximized"
    elif side == "center":
        pos = (x + w // 6, y + h // 12)
        size = (2 * w // 3, 5 * h // 6)
        label = "Centered"
    else:
        say("I don't know that position")
        return
    applescript(
        'tell application "System Events" to tell (first application process whose frontmost is true) to '
        f'tell window 1 to set {{position, size}} to {{{{{pos[0]}, {pos[1]}}}, {{{size[0]}, {size[1]}}}}}'
    )
    say(label)


def act_move_next_display() -> None:
    """Move frontmost window to the next connected display."""
    try:
        from AppKit import NSScreen
        screens = NSScreen.screens()
        if len(screens) < 2:
            say("Only one display connected")
            return
        s0, s1 = screens[0].frame(), screens[1].frame()
        dx = int(s1.origin.x - s0.origin.x)
        dy = int(s1.origin.y - s0.origin.y)
        applescript(
            'tell application "System Events" to tell (first application process whose frontmost is true) to '
            'tell window 1\n'
            '  set {wx, wy} to position\n'
            f'  set position to {{wx + ({dx}), wy + ({dy})}}\n'
            'end tell'
        )
        say("Moved to next display")
    except Exception as e:
        log(f"move to next display failed: {e}")
        say("Couldn't move window across displays")


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


def act_read_clipboard() -> None:
    """Read the current text on the clipboard aloud."""
    try:
        res = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
        text = res.stdout.strip()
        if text:
            say(text[:300])
        else:
            say("The clipboard is empty")
    except Exception:
        say("Couldn't read the clipboard")


def act_type_date() -> None:
    """Type the current formatted date."""
    act_type_text(datetime.now().strftime("%B %-d, %Y"))


def act_type_time() -> None:
    """Type the current formatted time."""
    act_type_text(datetime.now().strftime("%-I:%M %p"))


def act_type_email() -> None:
    """Type the user's configured email address."""
    email = VOICE_USER_EMAIL or os.environ.get("VOICE_USER_EMAIL", "").strip()
    if email:
        act_type_text(email)
    else:
        say("No email set. Add VOICE_USER_EMAIL to your dot env file.")


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


# ---------------------------------------------------------------- mouse control
def _mouse():
    """Lazily build a pynput mouse controller; (None, None) when unavailable."""
    try:
        from pynput.mouse import Button, Controller
    except Exception as e:  # noqa: BLE001
        log(f"mouse control unavailable: {e}")
        return None, None
    return Controller(), Button


def _click_xy(x: int, y: int, name: str) -> bool:
    """Click at (x, y) coordinates; returns True if successful/dry-run."""
    if DRY_RUN:
        log(f"DRY-RUN click at ({x}, {y}) for {name!r}")
        return True
    ctrl, btn = _mouse()
    if ctrl is None or btn is None:
        log("click failed: mouse controller unavailable")
        return False
    try:
        ctrl.position = (x, y)
        ctrl.click(btn.left, 1)
    except Exception as e:  # noqa: BLE001
        log(f"click failed: {e}")
        return False
    say(f"Clicked {name}")
    return True


# ---------------------------------------------------------------- Apple Vision OCR fast-path
_vision_framework_mod = None


def _load_vision_framework():
    """Import Apple Vision and Foundation lazily on macOS; (None, None) on failure."""
    global _vision_framework_mod
    if _vision_framework_mod is None:
        try:
            import Vision
            from Foundation import NSURL
            _vision_framework_mod = (Vision, NSURL)
        except Exception as e:  # noqa: BLE001
            _vision_framework_mod = False
            log(f"Apple Vision framework unavailable: {e}")
    return _vision_framework_mod or (None, None)


def _extract_target_name(name: str) -> str:
    """Normalize spoken element names by stripping conversational framing,
    prepositions, modal prefixes ('pop up dialog', 'dialog'), and suffixes ('button', 'link')."""
    s = name.replace("'", "").strip()
    for pfx in ("on the ", "on a ", "on an ", "on ", "the ", "a ", "an ",
                "pop up dialog ", "popup dialog ", "pop up ", "popup ", "dialog ", "alert "):
        if s.lower().startswith(pfx):
            s = s[len(pfx):].strip()
    for sfx in (" button", " link", " icon", " tab", " menu", " checkbox", " item", " toggle"):
        if s.lower().endswith(sfx):
            s = s[:-len(sfx)].strip()
    return s


def _clean_target_variants(name: str) -> list[str]:
    """Generate clean search variants from a spoken element name."""
    s = name.strip().lower()
    for prefix in ("on the ", "on a ", "on an ", "on ", "the ", "a ", "an "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    variants = [s]
    for suffix in (" button", " link", " icon", " tab", " menu", " checkbox", " item", " toggle"):
        if s.endswith(suffix):
            base = s[:-len(suffix)].strip()
            if base and base not in variants:
                variants.append(base)
            break
    for pfx in ("pop up dialog ", "popup dialog ", "pop up ", "popup ", "dialog ", "alert "):
        if s.startswith(pfx):
            base = s[len(pfx):].strip()
            if base and base not in variants:
                variants.append(base)
    return variants


def ocr_locate(name: str, screenshot_path: str | None = None) -> tuple[int, int] | str | None:
    """Locate a named text element on screen using Apple Vision OCR.

    Returns:
        (x, y): center coordinates of best match if unambiguous.
        "ambiguous": if multiple equally strong matches exist across the screen.
        None: if no match, purely iconographic, or Vision unavailable.
    """
    Vision, NSURL = _load_vision_framework()
    if Vision is None or NSURL is None:
        return None

    path = screenshot_path or capture_screenshot()
    if not path or not os.path.exists(path):
        return None

    variants = _clean_target_variants(name)
    if not variants or not variants[0]:
        return None

    try:
        from AppKit import NSScreen
        f = NSScreen.mainScreen().frame()
        sw, sh = int(f.size.width), int(f.size.height)
    except Exception:
        sw, sh = 1920, 1080

    try:
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
        req.setUsesLanguageCorrection_(False)
        url = NSURL.fileURLWithPath_(path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        success, _ = handler.performRequests_error_([req], None)
        if not success:
            return None
        results = req.results() or []
    except Exception as e:  # noqa: BLE001
        log(f"OCR request failed: {e}")
        return None

    import difflib

    candidates = []
    for obs in results:
        try:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            top_cand = cands[0]
            text = top_cand.string().strip()
            text_lower = text.lower()
            bbox = obs.boundingBox()
        except Exception:
            continue

        best_score = 0.0
        for var in variants:
            if text_lower == var:
                best_score = max(best_score, 1.0)
            elif re.search(r"\b" + re.escape(var) + r"\b", text_lower):
                best_score = max(best_score, 0.95)
            elif var in text_lower or text_lower in var:
                best_score = max(best_score, 0.85)
            else:
                ratio = difflib.SequenceMatcher(None, var, text_lower).ratio()
                if ratio >= 0.75:
                    best_score = max(best_score, ratio * 0.9)

        if best_score < 0.75:
            continue

        cx = bbox.origin.x + bbox.size.width / 2.0
        cy_vis = bbox.origin.y + bbox.size.height / 2.0
        sx = int(cx * sw)
        sy = int((1.0 - cy_vis) * sh)
        area = bbox.size.width * bbox.size.height
        dist = ((cx - 0.5) ** 2 + (cy_vis - 0.5) ** 2) ** 0.5
        score = best_score * 100.0 + area * 50.0 - dist * 10.0
        candidates.append({
            "text": text,
            "match_score": best_score,
            "score": score,
            "sx": sx,
            "sy": sy,
            "area": area,
            "dist": dist,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)

    top = [c for c in candidates if c["match_score"] >= 0.85]
    if len(top) >= 2:
        c1, c2 = top[0], top[1]
        pixel_dist = ((c1["sx"] - c2["sx"]) ** 2 + (c1["sy"] - c2["sy"]) ** 2) ** 0.5
        if (abs(c1["match_score"] - c2["match_score"]) < 0.05
                and pixel_dist > 80
                and c1["area"] < 2.5 * c2["area"]):
            log(f"OCR locate: ambiguous matches for {name!r}: "
                f"{[c['text'] for c in top[:3]]}")
            say(f"I found multiple items matching '{name}'. Which one did you want?")
            return "ambiguous"

    winner = candidates[0]
    log(f"OCR locate: matched {winner['text']!r} at ({winner['sx']}, {winner['sy']}) "
        f"for {name!r} (conf={winner['match_score']:.2f})")
    return winner["sx"], winner["sy"]


def frontmost_app() -> str:
    return applescript(
        'tell application "System Events" to get name of first '
        "application process whose frontmost is true"
    )


def _get_target_apps(primary_app: str) -> list[str]:
    """Return candidate applications to query for UI elements.
    Includes the frontmost app plus any running system modal/alert dialog apps."""
    candidates = [primary_app] if primary_app else []
    try:
        from AppKit import NSWorkspace
        running = {a.localizedName() for a in NSWorkspace.sharedWorkspace().runningApplications() if a.localizedName()}
        for sys_app in ("SecurityAgent", "CoreServicesUIAgent", "UserNotificationCenter", "Notification Center"):
            if sys_app in running and sys_app not in candidates:
                candidates.append(sys_app)
    except Exception:
        pass
    return candidates


def _press_first(app_name: str, roles: list[str], name: str) -> None:
    xa = _load_xa11y()
    if xa is None:
        say("UI clicking needs the xa11y package: pip install xa11y")
        return
    safe = _extract_target_name(name)
    if not safe:
        say("Click what?")
        return
    candidate_apps = _get_target_apps(app_name)
    for aname in candidate_apps:
        try:
            app = xa.App.by_name(aname)
        except Exception:
            continue
        for role in roles:
            try:
                els = app.locator(f"{role}[name*='{safe}']").elements()
            except Exception as e:  # noqa: BLE001
                log(f"xa11y query failed ({aname} {role}): {e}")
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
    # Tree had no match — Tier 1: Apple Vision OCR fast-path
    ocr_res = ocr_locate(safe)
    if ocr_res == "ambiguous":
        return
    if ocr_res is not None:
        x, y = ocr_res
        if _click_xy(x, y, safe):
            return
    # Tree & OCR had no match — Tier 2 (last resort): locate it visually on a screenshot (VLM).
    if vision_click(safe):
        return
    say(f"No control matching {safe}")


def act_click_button(name: str) -> None:
    _press_first(frontmost_app(), ["button"], name)


def act_click_link(name: str) -> None:
    _press_first(frontmost_app(), ["link"], name)


def act_click_any(name: str) -> None:
    _press_first(frontmost_app(), ["button", "link", "checkbox"], name)


def _locate_first(app_name: str, roles: list[str],
                  name: str) -> tuple[int, int] | None:
    """Center pixel of the first matching accessibility element, or None.

    Tree-first locate (no clicking) — the vision fallback is vision_locate().
    """
    xa = _load_xa11y()
    if xa is None or not app_name:
        return None
    safe = _extract_target_name(name)
    if not safe:
        return None
    candidate_apps = _get_target_apps(app_name)
    for aname in candidate_apps:
        try:
            app = xa.App.by_name(aname)
        except Exception:
            continue
        for role in roles:
            try:
                els = app.locator(f"{role}[name*='{safe}']").elements()
            except Exception as e:  # noqa: BLE001
                log(f"xa11y locate failed ({aname} {role}): {e}")
                continue
            if els:
                try:
                    b = els[0].bounds
                except Exception as e:  # noqa: BLE001
                    log(f"xa11y bounds failed: {e}")
                    return None
                if b is None:
                    return None
                return (int(b.x + b.width / 2), int(b.y + b.height / 2))
    return None


def act_mouse_to(name: str) -> None:
    """Move the cursor onto a named UI element ("move the mouse to the toggle").

    Accessibility tree first, Apple Vision OCR second, screenshot VLM as fallback.
    Moves only — it never clicks.
    """
    spoken = name.strip()
    if not spoken:
        say("Move the mouse to what?")
        return
    safe = _extract_target_name(spoken)
    loc = _locate_first(frontmost_app(), ["button", "link", "checkbox"], safe)
    if loc is None and safe != spoken:
        loc = _locate_first(frontmost_app(), ["button", "link", "checkbox"], spoken)
    if loc is None:
        ocr_res = ocr_locate(safe)
        if ocr_res == "ambiguous":
            return
        loc = ocr_res
    if loc is None:
        loc = vision_locate(safe)
    if loc is None:
        say(f"I couldn't find {spoken} on screen")
        return
    x, y = loc
    if DRY_RUN:
        log(f"DRY-RUN mouse move to ({x}, {y}) for {spoken!r}")
        say(f"Mouse is on {spoken}")
        return
    mouse, _ = _mouse()
    if mouse is None:
        say("Mouse control isn't available on this Mac")
        return
    mouse.position = (x, y)
    say(f"Mouse is on {spoken}")


def act_close_notifications() -> None:
    """Dismiss macOS Notification Center alert banners."""
    xa = _load_xa11y()
    if xa is None:
        say("Accessibility control unavailable")
        return
    try:
        app = xa.App.by_name("Notification Center")
    except Exception:
        say("No notifications open")
        return
    mouse, _ = _mouse()
    closed = 0
    for _ in range(3):
        try:
            groups = app.locator("group").elements()
        except Exception:
            break
        closed_this_pass = False
        for g in groups:
            if g.name:
                b = g.bounds
                if b and b.width > 200 and b.x > 1000:
                    if mouse:
                        mouse.position = (b.x + 10, b.y + 10)
                        time.sleep(0.1)
                    try:
                        for c in g.children():
                            if c.role == "button" and c.name == "Close":
                                c.press()
                                closed += 1
                                closed_this_pass = True
                                time.sleep(0.2)
                                break
                    except Exception:
                        pass
        if not closed_this_pass:
            break
    if closed:
        say(f"Closed {closed} notification{'s' if closed != 1 else ''}")
    else:
        say("No notifications to close")


def act_mouse_move(direction: str, amount: str | None) -> None:
    """Nudge the cursor ("move mouse up", "move mouse left 200")."""
    dx, dy = {"up": (0, -1), "down": (0, 1),
              "left": (-1, 0), "right": (1, 0)}[direction]
    dist = int(amount) if amount else 100
    if DRY_RUN:
        log(f"DRY-RUN mouse move {direction} {dist}px")
        say(f"Moving mouse {direction}")
        return
    mouse, _ = _mouse()
    if mouse is None:
        say("Mouse control isn't available on this Mac")
        return
    mouse.move(dx * dist, dy * dist)
    say(f"Moved mouse {direction}")


def act_scroll(direction: str, amount: str | None) -> None:
    """Scroll ("scroll up", "scroll down 3"). Amount multiplies the base step."""
    steps = int(amount) if amount else 1
    dx, dy = {"up": (0, 4), "down": (0, -4),
              "left": (4, 0), "right": (-4, 0)}[direction]
    if DRY_RUN:
        log(f"DRY-RUN scroll {direction} x{steps}")
        say(f"Scrolling {direction}")
        return
    mouse, _ = _mouse()
    if mouse is None:
        say("Mouse control isn't available on this Mac")
        return
    mouse.scroll(dx * steps, dy * steps)
    say(f"Scrolled {direction}")


def act_click_here() -> None:
    """Left-click at the current cursor position (bare "click")."""
    if DRY_RUN:
        log("DRY-RUN click at cursor")
        say("Clicked")
        return
    mouse, Button = _mouse()
    if mouse is None:
        say("Mouse control isn't available on this Mac")
        return
    mouse.click(Button.left, 1)
    say("Clicked")


# ---------------------------------------------------------------- local vision (screenshots via qwen3-vl)

_SCREENSHOT_PATH = "/tmp/free-voice-screen.png"
_shot_ts = 0.0
SCREENSHOT_TTL = 8.0  # "what's on my screen" -> "click the X" reuses the shot


def capture_screenshot(fresh: bool = False) -> str | None:
    """Capture the main display to a PNG. macOS only; None elsewhere.

    Screenshots are cached for SCREENSHOT_TTL seconds so a describe-then-click
    sequence doesn't pay for two captures. Pass fresh=True to force recapture.
    """
    global _shot_ts
    if DRY_RUN:
        log("DRY-RUN screenshot capture")
        return None
    now = time.time()
    if (not fresh and _shot_ts and os.path.exists(_SCREENSHOT_PATH)
            and now - _shot_ts < SCREENSHOT_TTL):
        log("reusing cached screenshot")
        return _SCREENSHOT_PATH
    try:
        subprocess.run(["screencapture", "-x", "-t", "png", _SCREENSHOT_PATH],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15)
    except Exception as e:  # noqa: BLE001
        note_error("screenshot failed")
        log(f"screenshot capture failed: {e}")
        return None
    _shot_ts = time.time()
    return _SCREENSHOT_PATH if os.path.exists(_SCREENSHOT_PATH) else None


def prepare_vision_image(image_path: str, max_dimension: int = 800) -> str:
    """Downsample and compress screenshot using macOS native sips.
    Reduces payload ~95%, avoids Ollama context overflow, and cuts inference
    time by ~60%. 800px is the measured sweet spot (M4: ~7.7s vs ~19.6s at
    1280px); the full-res original is kept for the two-pass click crop.
    """
    if not image_path:
        return image_path
    try:
        if not os.path.exists(image_path):
            return image_path
    except Exception:
        return image_path
    opt_path = f"{image_path}.opt.jpg"
    try:
        if os.path.exists(opt_path):
            try:
                if os.path.getmtime(opt_path) >= os.path.getmtime(image_path):
                    return opt_path
            except Exception:
                pass
        res = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "75",
             "-Z", str(max_dimension), image_path, "--out", opt_path],
            capture_output=True, check=False, timeout=5
        )
        if res.returncode == 0 and os.path.exists(opt_path):
            return opt_path
    except Exception as e:  # noqa: BLE001
        log(f"sips optimization failed ({e}), using raw screenshot")
    return image_path


def vision_ask(question: str, image_path: str,
               prefill: str | None = None,
               max_dimension: int = 800) -> str | None:
    """Ask the local vision-language model about a screenshot.

    Returns the model's text or None on failure. Downscales with native sips
    to fit within context window and eliminate 400 errors on high-DPI screens.
    Pass prefill to start the assistant's reply and suppress reasoning-style
    preamble on open-ended questions.
    """
    global _ollama_ok
    if _ollama_ok is False:
        return None
    ready_path = prepare_vision_image(image_path, max_dimension=max_dimension)
    try:
        with open(ready_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:  # noqa: BLE001
        log(f"vision read failed: {e}")
        return None
    messages = [{"role": "user", "content": question, "images": [b64]}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    body = {
        "model": OLLAMA_MODEL,
        "keep_alive": "60m",
        "think": False,
        "options": {"temperature": 0, "num_predict": 250, "num_ctx": 4096},
        "messages": messages,
        "stream": False,
    }
    try:
        req = urllib.request.Request(
            OLLAMA_HOST + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        _ollama_ok = True
    except Exception as e:  # noqa: BLE001
        _ollama_ok = False
        note_error("vision model unavailable")
        log(f"vision model unavailable: {e}")
        return None
    msg = data.get("message", {})
    content = msg.get("content", "").strip()
    if content:
        return content
    # Fallback: if reasoning tokens exhausted prediction limit, extract coordinate answer
    thinking = msg.get("thinking", "").strip()
    if thinking:
        m = re.findall(r"\b(\d{1,4}\s+\d{1,4})\b", thinking)
        if m:
            return m[-1]
        if "NONE" in thinking:
            return "NONE"
    return None


_quartz_mod = None


def _load_quartz():
    """Import Quartz lazily on macOS; None on failure."""
    global _quartz_mod
    if _quartz_mod is None:
        try:
            import Quartz
            _quartz_mod = Quartz
        except Exception as e:  # noqa: BLE001
            _quartz_mod = False
            log(f"Quartz framework unavailable: {e}")
    return _quartz_mod or None


def quartz_window_summary() -> str | None:
    """Return a spoken one-or-two sentence summary of on-screen windows via Quartz.

    Fast (~13ms) orientation without taking a screenshot or requiring Screen
    Recording permissions. Returns None if Quartz is unavailable or no layer-0
    windows >= 100px exist.
    """
    Q = _load_quartz()
    if Q is None:
        return None
    try:
        options = Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements
        window_list = Q.CGWindowListCopyWindowInfo(options, Q.kCGNullWindowID) or []
    except Exception as e:  # noqa: BLE001
        log(f"Quartz window list failed: {e}")
        return None

    windows = []
    for w in window_list:
        layer = w.get(Q.kCGWindowLayer, -1)
        bounds = w.get(Q.kCGWindowBounds, {})
        width = bounds.get("Width", 0)
        height = bounds.get("Height", 0)
        owner = w.get(Q.kCGWindowOwnerName, "")
        name = w.get(Q.kCGWindowName, "")
        if layer == 0 and width >= 100 and height >= 100:
            windows.append((owner, name))

    if not windows:
        return None

    front_app, front_title = windows[0]
    front_str = f"{front_app} ({front_title})" if front_title and front_title != front_app else front_app

    behind = []
    seen = {front_app}
    for app, title in windows[1:]:
        if app not in seen:
            seen.add(app)
            desc = f"{app} ({title})" if title and title != app else app
            behind.append(desc)

    if behind:
        if len(behind) == 1:
            behind_str = behind[0]
        elif len(behind) == 2:
            behind_str = f"{behind[0]} and {behind[1]}"
        else:
            rem = len(behind) - 2
            rem_str = "1 other app" if rem == 1 else f"{rem} other apps"
            behind_str = f"{behind[0]}, {behind[1]}, and {rem_str}"
        summary = f"In front is {front_str}. Behind it, you have {behind_str}."
    else:
        summary = f"In front is {front_str}, with no other active windows behind it."
    return summary


def act_describe_screen(visual_content_only: bool = False) -> None:
    """'what's on my screen' — Quartz orientation first (~13ms), VLM fallback."""
    if not visual_content_only:
        summary = quartz_window_summary()
        if summary:
            say(summary)
            return
    act_describe_screen_vlm()


def act_describe_screen_vlm() -> None:
    """Describe the visual display content using the local VLM (qwen3-vl)."""
    if ALWAYS_MODE:
        say("Looking...")
        _submit_vision_task(_describe_screen_vlm_task)
        return
    _describe_screen_vlm_task()


def _describe_screen_vlm_task() -> None:
    path = capture_screenshot()
    if not path:
        say("I couldn't capture the screen")
        return
    text = vision_ask(
        "Describe what's visible on this macOS screenshot in one or two short "
        "spoken sentences. Name the frontmost app and anything important like "
        "open dialogs, playing media, or error messages. Plain text only.",
        path,
        prefill="The screen shows ",
    )
    if text:
        # prefill seeded the model's reply; speak the full sentence.
        say("The screen shows " + text)
    else:
        say("I couldn't make sense of the screen")


def act_status() -> None:
    """Spoken self-check: mic, models, last error. For couch debugging —
    "are you working?" should get a useful answer, not silence."""
    mic = describe_input_device()
    if not OLLAMA_TIER1:
        tier1 = "off"
    elif _ollama_ok is True:
        tier1 = f"{OLLAMA_MODEL}, ready"
    elif _ollama_ok is False:
        tier1 = f"{OLLAMA_MODEL}, unreachable"
    else:
        tier1 = f"{OLLAMA_MODEL}, not checked yet"
    gem = "configured" if GEMINI_API_KEY else "not configured"
    up = int(time.time() - _START_TIME)
    parts = [f"Mic: {mic}.", f"Local model: {tier1}.", f"Gemini: {gem}."]
    parts.append(f"Last error: {_last_error}." if _last_error
                 else "No recent errors.")
    parts.append(f"Up {up // 60} minutes." if up >= 60 else "Just started.")
    say(" ".join(parts))


def _refine_click(path: str, name: str, x: int, y: int,
                  sw: int, sh: int, box: int = 480) -> tuple[int, int]:
    """Second vision pass: crop a `box`-px region around (x, y), ask the model
    for the target's center *within the crop*, map back to screen pixels.

    Returns the refined (x, y), or the original on any failure (including
    Pillow not being installed).
    """
    try:
        from PIL import Image
    except ImportError:
        log("vision click: Pillow not installed, skipping refinement pass")
        return x, y
    try:
        img = Image.open(path)
        w, h = img.size
        x0 = min(max(x - box // 2, 0), w - 1)
        y0 = min(max(y - box // 2, 0), h - 1)
        x1 = min(x0 + box, w)
        y1 = min(y0 + box, h)
        crop_path = "/tmp/free-voice-crop.png"
        img.crop((x0, y0, x1, y1)).save(crop_path)
        loc = vision_ask(
            f"In this cropped close-up, find the clickable UI element best "
            f"matching '{name}'. Reply with ONLY two integers X Y — the "
            f"element's center as 0-1000 fractions of THIS cropped image "
            f"(example: 512 340). If it is not clearly visible, reply exactly: NONE",
            crop_path,
        )
        m = re.match(r"\s*(\d{1,4})\s+(\d{1,4})\s*", loc or "")
        if not m:
            return x, y
        fx, fy = int(m.group(1)), int(m.group(2))
        if not (0 <= fx <= 1000 and 0 <= fy <= 1000):
            return x, y
        nx = min(max(x0 + int(fx / 1000 * (x1 - x0)), 0), sw - 1)
        ny = min(max(y0 + int(fy / 1000 * (y1 - y0)), 0), sh - 1)
        log(f"vision click refined ({x},{y}) -> ({nx},{ny}) for {name!r}")
        return nx, ny
    except Exception as e:  # noqa: BLE001
        log(f"vision refine failed, using first pass: {e}")
        return x, y


def vision_locate(name: str) -> tuple[int, int] | None:
    """Locate a UI element by screenshot vision; return pixel (x, y) or None.

    No clicking — shared by vision_click and mouse-to-target. Two passes:
    a full-screen guess, then a 480-px crop around the guess re-asked for
    finer coordinates. The first pass also returns the element's approximate
    size; when the element is at least as wide as the refinement crop, the
    first-pass center already lands inside it, so the second inference
    (~5-8s) is skipped. A missing size (older two-integer replies) refines,
    conservatively.
    """
    path = capture_screenshot()
    if not path:
        return None
    loc = vision_ask(
        f"In this macOS screenshot, find the clickable UI element best "
        f"matching '{name}'. Reply with ONLY three integers X Y S — the "
        f"element's center as 0-1000 fractions of screen width and height "
        f"(example: 512 340), and S, the element's approximate width as a "
        f"0-1000 fraction of screen width (example: 40 for a small button, "
        f"300 for a large window). If it is not clearly visible, reply "
        f"exactly: NONE",
        path,
    )
    if not loc:
        return None
    m = re.match(r"\s*(\d{1,4})\s+(\d{1,4})(?:\s+(\d{1,4}))?\s*", loc)
    if not m:
        log(f"vision locate: unparseable location {loc!r}")
        return None
    fx, fy = int(m.group(1)), int(m.group(2))
    if not (0 <= fx <= 1000 and 0 <= fy <= 1000):
        return None
    try:
        from AppKit import NSScreen
        f = NSScreen.mainScreen().frame()
        sw, sh = int(f.size.width), int(f.size.height)
    except Exception:
        sw, sh = 1920, 1080
    x = min(max(int(fx / 1000 * sw), 0), sw - 1)
    y = min(max(int(fy / 1000 * sh), 0), sh - 1)
    size_px = int(m.group(3)) / 1000 * sw if m.group(3) else 0
    if size_px >= 480:  # fills the refinement crop: center can't miss
        log(f"vision locate: large target (~{int(size_px)}px), "
            f"skipping refinement for {name!r}")
        return x, y
    return _refine_click(path, name, x, y, sw, sh)


def vision_click(name: str) -> bool:
    """Last-resort click: locate a UI element by screenshot vision, click it.

    Only runs when the accessibility tree had no match. Returns True if a
    click was attempted. Coordinates are approximate — the tree stays the
    preferred path, and this is never used for destructive or sensitive
    actions (those go through tree-only _press_first intents).
    """
    if ALWAYS_MODE:
        say("Looking...")
        _submit_vision_task(_vision_click_task, name)
        return True
    return _vision_click_task(name)


def _vision_click_task(name: str) -> bool:
    loc = vision_locate(name)
    if loc is None:
        return False
    x, y = loc
    if DRY_RUN:
        log(f"DRY-RUN vision click at ({x}, {y}) for {name!r}")
        return True
    try:
        from pynput.mouse import Button, Controller
        mouse = Controller()
        mouse.position = (x, y)
        mouse.click(Button.left, 1)
    except Exception as e:  # noqa: BLE001
        log(f"vision click failed: {e}")
        return False
    say(f"Clicked {name}")
    return True


# ---------------------------------------------------------------- confirmation

def confirm_spoken(action_desc: str, audio_fn) -> bool:
    say(f"Say 'yes' to confirm: {action_desc}", blocking=True)
    audio = audio_fn(4.0)
    text = transcribe(audio).lower()
    log(f"confirmation heard: {text!r}")
    return "yes" in text or "confirm" in text or "do it" in text


# --------------------------------- continuous dictation, macros, fun stuff

_DICTATE_CHUNK = 8.0  # seconds of audio per typed chunk
_DICTATE_STOP_EXACT = {
    "stop", "stop dictating", "end dictation", "done", "done dictating",
    "finish dictating", "that's all",
}
_DICTATE_STOP_TAIL = ("stop dictating", "end dictation", "done dictating")


def act_dictate_start() -> None:
    """Continuous dictation: type transcribed chunks until a stop phrase.

    'start dictating' / 'dictate' / 'take notes'. Only a bare 'stop' or a
    multi-word stop phrase ends the session, so 'stop' inside a sentence
    ('I told him to stop calling') keeps dictating.
    """
    if DRY_RUN:
        say("Dictation mode")
        return
    say("Dictating. Say stop dictating when you're done.")
    try:
        while True:
            try:
                text = transcribe(record_fixed(_DICTATE_CHUNK))
            except Exception as e:  # noqa: BLE001
                log(f"dictation chunk failed: {e}")
                continue
            if not text:
                continue
            low = text.strip().lower().rstrip(".!?").strip()
            if low in _DICTATE_STOP_EXACT:
                say("Done dictating")
                return
            for phrase in _DICTATE_STOP_TAIL:
                if low.endswith(phrase):
                    head = text[: -len(phrase)].strip().rstrip(".!?").strip()
                    if head:
                        act_type_text(head + " ")
                    say("Done dictating")
                    return
            act_type_text(text + " ")
    except KeyboardInterrupt:
        say("Dictation stopped")


# --- custom voice macros ----------------------------------------------

_MACRO_FILE = os.path.join(HOME, ".config", "free-voice", "macros.json")
_macros_cache: dict | None = None


def _load_macros() -> dict:
    global _macros_cache
    if _macros_cache is None:
        try:
            with open(_MACRO_FILE) as f:
                data = json.load(f)
            _macros_cache = data if isinstance(data, dict) else {}
        except Exception:
            _macros_cache = {}
    return _macros_cache


def _save_macros(macros: dict) -> None:
    global _macros_cache
    os.makedirs(os.path.dirname(_MACRO_FILE), exist_ok=True)
    with open(_MACRO_FILE, "w") as f:
        json.dump(macros, f, indent=2)
    _macros_cache = macros


def act_macro_add(trigger: str, commands: str) -> None:
    """'macro standup runs open slack and open zoom' — save a voice macro."""
    parts = [p.strip() for p in
             re.split(r"\s+then\s+|\s+and\s+|,\s*", commands, flags=re.IGNORECASE)
             if p.strip()]
    if not parts:
        say("I didn't hear any commands for that macro")
        return
    macros = _load_macros()
    macros[trigger.strip().lower()] = parts
    _save_macros(macros)
    say(f"Macro {trigger.strip()} saved with {len(parts)} "
        f"command{'s' if len(parts) != 1 else ''}")


def act_macro_list() -> None:
    macros = _load_macros()
    if not macros:
        say("You have no macros yet. Say macro, a name, then runs, to make one.")
        return
    say(f"You have {len(macros)} macro{'s' if len(macros) != 1 else ''}: "
        + ", ".join(sorted(macros)))


def act_macro_delete(trigger: str) -> None:
    macros = _load_macros()
    key = trigger.strip().lower()
    if key in macros:
        del macros[key]
        _save_macros(macros)
        say(f"Deleted macro {trigger.strip()}")
    else:
        say(f"No macro called {trigger.strip()}")


def run_macro(text: str, confirm_audio_fn=None,
              allow_destructive: bool = False, quiet_miss: bool = False) -> bool:
    """Run a user macro whose trigger exactly matches the utterance.

    Checked after Tier 0 (built-ins always win) and before Tier 1, so macro
    phrases never get misrouted to the LLM. Returns True if one ran.
    """
    parts = _load_macros().get(text.strip().lower())
    if not parts:
        return False
    log(f"macro hit: {text.strip()!r} -> {parts}")
    for p in parts:
        handle_command(p, confirm_audio_fn=confirm_audio_fn,
                       allow_destructive=allow_destructive,
                       quiet_miss=quiet_miss)
        time.sleep(0.3)
    say("Macro done")
    return True


# --- ASCII art ---------------------------------------------------------

_ASCII_RAMP = "@%#*+=-:. "


def act_ascii_art() -> None:
    """'turn my screen into ascii art' — render the screenshot as text art."""
    path = capture_screenshot()
    if not path:
        say("I couldn't capture the screen")
        return
    try:
        from PIL import Image
    except ImportError:
        say("ASCII art needs Pillow — install it with pip install pillow")
        return
    img = Image.open(path).convert("L")
    w = 120
    h = max(1, int(img.height * w / img.width * 0.5))  # glyphs are ~2x tall
    img = img.resize((w, h))
    px = img.load()
    n = len(_ASCII_RAMP) - 1
    lines = ["".join(_ASCII_RAMP[px[x, y] * n // 255] for x in range(w))
             for y in range(h)]
    out = os.path.join(tempfile.gettempdir(), "ascii-art.txt")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    shell(["open", out])
    say("Here's your screen as ASCII art")


# --- SVG drawing -------------------------------------------------------

def _llm_text(prompt: str, max_tokens: int = 2048) -> str | None:
    """Raw text from the local model, else Gemini if configured. None if both fail."""
    system = ("You are an SVG generator. Reply with ONLY valid raw SVG code "
              "starting with <svg and ending with </svg>. "
              "No explanations, no conversational text, no markdown fences.")
    # Local model first (100% on-device, private, offline)
    try:
        body = {
            "model": OLLAMA_MODEL, "keep_alive": "60m", "think": False,
            "options": {"temperature": 0.7, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "stream": False,
        }
        req = urllib.request.Request(
            OLLAMA_HOST + "/api/chat", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
        msg = data.get("message", {})
        content = msg.get("content", "").strip()
        if content:
            return content
        thinking = msg.get("thinking", "").strip()
        if thinking and ("<svg" in thinking or "{" in thinking):
            return thinking
    except Exception as e:  # noqa: BLE001
        log(f"draw: local model failed ({e}), trying gemini fallback if configured")

    if GEMINI_API_KEY:
        try:
            body = {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens,
                                     "temperature": 0.7},
            }
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # noqa: BLE001
            log(f"draw: gemini fallback failed ({e})")
    return None


def act_draw_svg(subject: str) -> None:
    """'draw a cat' — LLM-generated SVG opened in the browser."""
    subject = subject.strip()
    say(f"Drawing {subject}")
    svg = _llm_text(
        f"Generate a clean, flat SVG drawing of {subject}. "
        f"Use a 400x400 viewBox with simple shapes and colors. "
        f"Output ONLY raw <svg> code immediately starting with <svg and ending with </svg>.")
    if not svg:
        say("I couldn't draw that right now")
        return
    m = re.search(r"<svg.*?</svg>", svg, re.DOTALL | re.IGNORECASE)
    if not m:
        say("The drawing didn't come out right")
        return
    out = os.path.join(tempfile.gettempdir(), "drawing.svg")
    with open(out, "w") as f:
        f.write(m.group(0))
    shell(["open", out])
    say(f"Here's your {subject}")


# --- easter eggs -------------------------------------------------------

_JOKES = [
    "Why don't programmers like nature? Too many bugs.",
    "I told my computer I needed a break. Now it won't stop sending me KitKat ads.",
    "Why did the developer go broke? He used up all his cache.",
    "There are only 10 kinds of people: those who understand binary and those who don't.",
    "Why do Java developers wear glasses? Because they don't C sharp.",
    "I would tell you a UDP joke, but you might not get it.",
    "Why was the computer cold? It left its Windows open.",
    "A SQL query walks into a bar, sees two tables and asks: mind if I join you?",
]

_8BALL = [
    "It is certain", "Without a doubt", "Yes definitely",
    "Most likely", "Outlook good", "Signs point to yes",
    "Reply hazy, try again", "Ask again later",
    "Better not tell you now", "Cannot predict now",
    "Don't count on it", "My reply is no",
    "Outlook not so good", "Very doubtful",
]


def act_roll_dice() -> None:
    say(f"You rolled a {random.randint(1, 6)}")


def act_coin_flip() -> None:
    say(f"It's {random.choice(['heads', 'tails'])}")


def act_8ball() -> None:
    say(random.choice(_8BALL))


def act_joke() -> None:
    say(random.choice(_JOKES))


# --- reminders, read-screen, music -------------------------------------

def act_reminder(amount: int, unit: str, message: str) -> None:
    """'remind me in 10 minutes to check the oven'."""
    seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[unit.rstrip("s")]

    def ring() -> None:
        time.sleep(seconds)
        say(f"Reminder: {message}")
        shell(["afplay", "/System/Library/Sounds/Glass.aiff"])

    threading.Thread(target=ring, daemon=True).start()
    say(f"I'll remind you to {message} in {amount} {unit}")


def act_read_screen() -> None:
    """'read my screen to me' — a fuller spoken tour than describe."""
    if ALWAYS_MODE:
        say("Looking...")
        _submit_vision_task(_read_screen_task)
        return
    _read_screen_task()


def _read_screen_task() -> None:
    path = capture_screenshot()
    if not path:
        say("I couldn't capture the screen")
        return
    text = vision_ask(
        "Read this macOS screenshot aloud in three to five short spoken "
        "sentences, as if describing the screen to someone who can't see it. "
        "Go through the frontmost window top to bottom: titles, text, buttons, "
        "and any dialogs or notifications. Plain text only.",
        path,
        prefill="Looking at the screen, ",
    )
    if text:
        say(text)
    else:
        say("I couldn't make out the screen")


def act_play_genre(genre: str) -> None:
    """'play some jazz' — play the first Music playlist matching the genre."""
    g = genre.strip()
    script = (f'tell application "Music"\nactivate\n'
              f'play (first playlist whose name contains "{esc(g)}")\nend tell')
    try:
        shell(["osascript", "-e", script])
    except Exception:  # noqa: BLE001
        say(f"I couldn't find a {g} playlist in Music")
        return
    say(f"Playing {g}")


# ---------------------------------------------------------------- Tier 0: regex router + completion gating
# _p(rx, name, partial_ok): partial_ok=True ONLY for commands that are safe
# to fire mid-sentence on a growing partial transcript. Free-text payloads
# (type X, search for X, click names, numbers that can grow) are NEVER
# partial-safe — they wait for end-of-speech.

_PATTERNS: list[tuple[re.Pattern, str, bool]] = []


def _p(rx: str, name: str, partial_ok: bool = False) -> None:
    _PATTERNS.append((re.compile(rx, re.IGNORECASE), name, partial_ok))


# --- continuous dictation BEFORE generic "open/start ..." (else "start dictating" opens an app)
_p(r"^(start dictating|dictate|take notes)( until i say stop)?$", "dictate_start")
# --- apps & tabs (specific "open tab" / "open X settings" / "open trash" BEFORE generic open)
_p(r"^(new|open)( a)? tab$", "new_tab", True)
_p(r"^close( the)? tab$", "close_tab", True)
_p(r"^(reopen|undo close)( the)? tab$", "reopen_tab", True)
_p(r"^(next tab|tab forward)$", "next_tab", True)
_p(r"^(previous tab|prev tab|tab back)$", "prev_tab", True)
_p(r"^open (.+) settings$", "settings", True)
_p(r"^open trash$", "open_trash", True)
_p(r"^(open|launch|start) the (.+?) (app|application)$", "open_app", True)
_p(r"^(open|launch|start) (.+)$", "open_app", True)
_p(r"^(next app|app forward)$", "next_app", True)
_p(r"^(previous app|prev app|app back)$", "prev_app", True)
_p(r"^(switch to|focus|bring up) (.+)$", "switch_app", True)
# --- windows / tabs / quit / hide
_p(r"^close all windows$", "close_all_windows", True)
_p(r"^(close( the)? window|close this)$", "close_window", True)
_p(r"^close$", "close_window", True)
_p(r"^next window$", "next_window", True)
_p(r"^show all windows$", "show_all_windows", True)
_p(r"^show desktop$", "show_desktop", True)
_p(r"^hide everything else$", "hide_others", True)
_p(r"^kill (.+)$", "kill_app")
_p(r"^(quit|close)( the)? (app |application )?(.+)$", "quit_app", True)
_p(r"^(minimize|minimise)( the)? (.+?)( window| app)?$", "minimize_app", True)
_p(r"^(minimize|minimise)( the window)?$", "minimize", True)
_p(r"^(fullscreen|full screen|make it full screen)$", "fullscreen", True)
_p(r"^hide( the)? (.+?)( app| application)?$", "hide_app", True)
_p(r"^hide( the app)?$", "hide", True)
# --- browser navigation
_p(r"^(refresh|reload)( the (page|tab))?$", "refresh_page", True)
_p(r"^(go )?back$", "nav_back", True)
_p(r"^(go )?forward$", "nav_forward", True)
_p(r"^(page down|scroll page down)$", "page_down", True)
_p(r"^(page up|scroll page up)$", "page_up", True)
_p(r"^(find next|next match)$", "find_next", True)
_p(r"^(find|find on page|search page)$", "find_in_page", True)
_p(r"^(type url|address bar)$", "address_bar", True)
_p(r"^(private window|new incognito window|incognito window)$", "private_window", True)
_p(r"^(bookmark this|add bookmark)$", "bookmark_this", True)
_p(r"^clear( the)? terminal$", "clear_terminal", True)
# --- spaces & display management
_p(r"^next space$", "next_space", True)
_p(r"^(previous|prev) space$", "prev_space", True)
_p(r"^move to (the )?(next|other) (display|screen|monitor)$", "move_next_display", True)
# --- window snapping / tiling
_p(r"^(snap|tile)( the)? window left$", "snap_left", True)
_p(r"^(snap|tile) left$", "snap_left", True)
_p(r"^(snap|tile)( the)? window right$", "snap_right", True)
_p(r"^(snap|tile) right$", "snap_right", True)
_p(r"^(maximize|zoom)( the)? window$", "maximize_window", True)
_p(r"^(maximize|zoom)$", "maximize_window", True)
_p(r"^center( the)? window$", "center_window", True)
# --- typing, clipboard & document keys (specific macros BEFORE generic type)
_p(r"^(read|speak|what's on)( my| the)? clipboard$", "read_clipboard", True)
_p(r"^type( today's| the)? date$", "type_date", True)
_p(r"^type( the| current)? time$", "type_time", True)
_p(r"^type( my)? email$", "type_email", True)
_p(r"^type (.+)$", "type_text")
_p(r"^dictate (.+)$", "type_text")
_p(r"^press (enter|return|escape|tab|space|delete)$", "press_key", True)
_p(r"^copy$", "copy_", True)
_p(r"^(paste plain text|paste match style)$", "paste_plain", True)
_p(r"^paste$", "paste_", True)
_p(r"^cut$", "cut_", True)
_p(r"^undo$", "undo_", True)
_p(r"^redo$", "redo_", True)
_p(r"^(save as|save copy as)$", "save_as", True)
_p(r"^save$", "save_", True)
_p(r"^(print this|print)$", "print_this", True)
_p(r"^select all$", "select_all", True)
# --- Finder navigation & file management
_p(r"^go to desktop$", "finder_desktop", True)
_p(r"^go to documents$", "finder_documents", True)
_p(r"^go to downloads$", "finder_downloads", True)
_p(r"^go to apps$", "finder_apps", True)
_p(r"^new folder$", "new_folder", True)
_p(r"^rename to (.+)$", "rename_to")
_p(r"^(duplicate this|duplicate)$", "duplicate_this", True)
_p(r"^(get file info|get info)$", "get_file_info", True)
_p(r"^(preview this|preview)$", "preview_this", True)
_p(r"^(trash this|delete this|move to trash)$", "trash_this", True)
_p(r"^list view$", "list_view", True)
_p(r"^icon view$", "icon_view", True)
_p(r"^column view$", "column_view", True)
_p(r"^gallery view$", "gallery_view", True)
# --- UI clicks via accessibility tree (free-text names: final-only)
_p(r"^(click|press|tap|hit)(?: on)?( the)? (.+?) button$", "click_button")
_p(r"^(?:click|press|tap|hit)(?: on)? (the )?(.+?) link$", "click_link")
_p(r"^(click|tap)$", "click_here")
_p(r"^(?:click|press|tap|hit)(?: on)? (the )?(.+)$", "click_any")
_p(r"^move (the )?mouse to (the )?(.+)$", "mouse_to")
_p(r"^move (the )?mouse (up|down|left|right)( (\d+))?$", "mouse_move")
_p(r"^scroll (up|down|left|right)( (\d+))?$", "scroll")
# --- screen vision (final transcript only: needs a fresh screenshot)
_p(r"^what'?s on (my|the) screen$", "describe_screen")
_p(r"^describe (my|the) screen$", "describe_screen")
_p(r"^what am i looking at$", "describe_screen")
_p(r"^(what('?s| is) in this window|describe this window)$", "describe_window_visual")
_p(r"^(what color|describe (the |this )?(image|diagram|video|photo))", "describe_window_visual")
_p(r"^(are you (working|there|ok)|status|health check)$", "status", True)
# --- web (free text: final-only)
_p(r"^search mac$", "search_mac", True)
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
_p(r"^(brightness up|brighten screen)$", "bright_up", True)
_p(r"^(brightness down|dim screen)$", "bright_down", True)
_p(r"^set (the )?brightness to (\d+)( percent)?$", "bright_set")
_p(r"^(play|pause|play or pause|play pause|resume)$", "media_playpause", True)
_p(r"^next( (track|song))?$", "media_next", True)
_p(r"^previous( (track|song))?$", "media_prev", True)
# --- system power (destructive ones need confirmation: final-only)
_p(r"^(lock( (the )?(computer|mac|screen))?|lock it down)$", "lock", True)
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
_p(r"^record screen$", "record_screen", True)
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
# --- voice macros
_p(r"^macro (.+?) runs (.+)$", "macro_add")
_p(r"^(list|show)( my)? macros$", "macro_list", True)
_p(r"^delete macro (.+)$", "macro_delete")
# --- fun: ascii art & drawing
_p(r"^(turn|make|convert)( my| the)? screen into ascii( art)?$", "ascii_art", True)
_p(r"^ascii art( of my screen)?$", "ascii_art", True)
_p(r"^draw( me)?( a| an| the)? (.+)$", "draw_svg")
# --- easter eggs
_p(r"^roll( a)? (die|dice)$", "roll_dice", True)
_p(r"^flip( a)? coin$", "coin_flip", True)
_p(r"^ask the (magic )?8[ -]?ball (.+)$", "eight_ball", True)
_p(r"^(magic )?8[ -]?ball$", "eight_ball_bare", True)
_p(r"^tell me a joke$", "joke", True)
# --- reminders (plain timers already exist above)
_p(r"^remind me in (\d+) (seconds?|minutes?|hours?) to (.+)$", "reminder")
# --- read screen aloud (short describe already exists above)
_p(r"^read (my|the) screen( to me| aloud)?$", "read_screen", True)
# --- music
_p(r"^play some (.+)$", "play_genre", True)


def _partial_complete(name: str, m: re.Match) -> bool:
    """Completion gate for PARTIAL transcripts. Even for partial-safe
    patterns, only fire when the payload is terminal-complete:

    - open/quit <app>: the app phrase must resolve EXACTLY (alias or full
      installed name). 'open no' must NOT fire as 'open Notes'.
    - open <topic> settings: topic must be a known settings pane.
    - everything else partial-safe: the regex consumed the whole partial,
      which for closed enums is enough.
    """
    if name in ("open_app", "quit_app", "minimize_app", "hide_app", "switch_app"):
        if name in ("open_app", "switch_app"):
            phrase = m.group(2)
        elif name == "minimize_app":
            phrase = m.group(3)
        elif name == "hide_app":
            phrase = m.group(2)
        else:
            phrase = m.group(m.lastindex)
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
        if name in ("open_app", "quit_app", "switch_app") and (" and " in t.lower() or " then " in t.lower()):
            phrase = m.group(2) if name in ("open_app", "switch_app") else m.group(m.lastindex)
            if resolve_app(phrase) is None:
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
        elif name == "switch_app":
            act_switch_app(m.group(2))
        # quit_app and close_all_windows are destructive: handled by the
        # confirmation gate below, not here.
        elif name == "close_window":
            act_keystroke("w", "command down"); say("Closed")
        elif name == "next_window":
            act_keystroke("`", "command down"); say("Next window")
        elif name == "show_all_windows":
            act_key_code(99); say("All windows")  # F3 Mission Control
        elif name == "show_desktop":
            act_key_code(103); say("Desktop")  # F11 Show Desktop
        elif name == "hide_others":
            act_keystroke("h", "option down, command down"); say("Hiding others")
        elif name == "new_tab":
            act_keystroke("t", "command down"); say("New tab")
        elif name == "close_tab":
            act_keystroke("w", "command down"); say("Closed tab")
        elif name == "reopen_tab":
            act_keystroke("t", "shift down, command down"); say("Reopened tab")
        elif name == "next_tab":
            act_key_code(48, "control down"); say("Next tab")  # ^Tab
        elif name == "prev_tab":
            act_key_code(48, "control down, shift down"); say("Previous tab")  # ^Shift-Tab
        elif name == "next_app":
            act_key_code(48, "command down"); say("Next app")  # Cmd-Tab
        elif name == "prev_app":
            act_key_code(48, "command down, shift down"); say("Previous app")  # Cmd-Shift-Tab
        elif name == "address_bar":
            act_keystroke("l", "command down"); say("Address bar")
        elif name == "private_window":
            act_keystroke("n", "shift down, command down"); say("Private window")
        elif name == "bookmark_this":
            act_keystroke("d", "command down"); say("Bookmarked")
        elif name == "refresh_page":
            act_keystroke("r", "command down"); say("Refreshed")
        elif name == "nav_back":
            act_keystroke("[", "command down"); say("Back")
        elif name == "nav_forward":
            act_keystroke("]", "command down"); say("Forward")
        elif name in ("scroll_down", "page_down"):
            act_key_code(121); say("Scrolled down")
        elif name in ("scroll_up", "page_up"):
            act_key_code(116); say("Scrolled up")
        elif name == "find_next":
            act_keystroke("g", "command down"); say("Find next")
        elif name == "find_in_page":
            act_keystroke("f", "command down"); say("Find")
        elif name == "search_mac":
            act_key_code(49, "command down"); say("Spotlight")
        elif name == "clear_terminal":
            act_keystroke("k", "command down"); say("Cleared")
        elif name == "next_space":
            act_key_code(124, "control down"); say("Next space")
        elif name == "prev_space":
            act_key_code(123, "control down"); say("Previous space")
        elif name == "move_next_display":
            act_move_next_display()
        elif name == "minimize":
            act_minimize()
        elif name == "minimize_app":
            act_minimize(m.group(3))
        elif name == "fullscreen":
            act_key_code(3, "control down, command down")  # Ctrl-Cmd-F
        elif name == "hide":
            act_hide()
        elif name == "hide_app":
            act_hide(m.group(2))
        elif name == "snap_left":
            act_snap_window("left")
        elif name == "snap_right":
            act_snap_window("right")
        elif name == "maximize_window":
            act_snap_window("maximize")
        elif name == "center_window":
            act_snap_window("center")
        elif name == "read_clipboard":
            act_read_clipboard()
        elif name == "type_date":
            act_type_date()
        elif name == "type_time":
            act_type_time()
        elif name == "type_email":
            act_type_email()
        elif name == "type_text":
            act_type_text(m.group(1)); say("Typed")
        elif name == "press_key":
            keymap = {"enter": "return", "return": "return", "escape": "escape",
                      "tab": "tab", "space": " ", "delete": "delete"}
            act_keystroke(keymap[m.group(1).lower()] if m.group(1).lower() != " " else " ")
        elif name == "copy_":
            act_keystroke("c", "command down")
        elif name == "paste_plain":
            act_keystroke("v", "option down, shift down, command down"); say("Pasted plain text")
        elif name == "paste_":
            act_keystroke("v", "command down")
        elif name == "cut_":
            act_keystroke("x", "command down")
        elif name == "undo_":
            act_keystroke("z", "command down")
        elif name == "redo_":
            act_keystroke("z", "command down, shift down")
        elif name == "save_as":
            act_keystroke("s", "shift down, command down"); say("Save as")
        elif name == "save_":
            act_keystroke("s", "command down"); say("Saved")
        elif name == "print_this":
            act_keystroke("p", "command down"); say("Print")
        elif name == "select_all":
            act_keystroke("a", "command down")
        elif name == "finder_desktop":
            act_keystroke("d", "shift down, command down"); say("Desktop")
        elif name == "finder_documents":
            act_keystroke("o", "shift down, command down"); say("Documents")
        elif name == "finder_downloads":
            act_keystroke("l", "option down, command down"); say("Downloads")
        elif name == "finder_apps":
            act_keystroke("a", "shift down, command down"); say("Applications")
        elif name == "new_folder":
            act_keystroke("n", "shift down, command down"); say("New folder")
        elif name == "rename_to":
            act_rename_to(m.group(1))
        elif name == "duplicate_this":
            act_keystroke("d", "command down"); say("Duplicated")
        elif name == "get_file_info":
            act_keystroke("i", "command down"); say("File info")
        elif name == "preview_this":
            act_key_code(49); say("Preview")
        elif name == "trash_this":
            act_key_code(51, "command down"); say("Moved to trash")
        elif name == "list_view":
            act_keystroke("2", "command down"); say("List view")
        elif name == "icon_view":
            act_keystroke("1", "command down"); say("Icon view")
        elif name == "column_view":
            act_keystroke("3", "command down"); say("Column view")
        elif name == "gallery_view":
            act_keystroke("4", "command down"); say("Gallery view")
        elif name == "record_screen":
            act_keystroke("5", "shift down, command down"); say("Record screen")
        elif name == "click_button":
            act_click_button(m.group(3))
        elif name == "click_link":
            act_click_link(m.group(2))
        elif name == "click_any":
            act_click_any(m.group(2))
        elif name == "click_here":
            act_click_here()
        elif name == "mouse_to":
            act_mouse_to(m.group(3))
        elif name == "mouse_move":
            act_mouse_move(m.group(2), m.group(4))
        elif name == "scroll":
            act_scroll(m.group(1), m.group(3))
        elif name == "describe_screen":
            act_describe_screen()
        elif name == "describe_window_visual":
            act_describe_screen(visual_content_only=True)
        elif name == "status":
            act_status()
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
        elif name in ("shutdown", "restart", "logout", "empty_trash",
                      "quit_app", "kill_app", "close_all_windows"):
            # Destructive actions ask first (spoken "yes"), unless --yes.
            # In chained commands each destructive part is confirmed on its own.
            if name == "quit_app":
                desc = f"quitting {m.group(m.lastindex)}"
            elif name == "kill_app":
                desc = f"force quitting {m.group(1)}"
            else:
                desc = {"shutdown": "shutting down", "restart": "restarting",
                        "logout": "logging out",
                        "empty_trash": "emptying the trash",
                        "close_all_windows": "closing all windows"}[name]
            ok = allow_destructive or (
                confirm_audio_fn is not None
                and confirm_spoken(desc, confirm_audio_fn)
            )
            if not ok:
                say(f"Not {desc} without confirmation")
                return
            if name == "quit_app":
                act_quit_app(m.group(m.lastindex))
            elif name == "kill_app":
                act_force_quit_app(m.group(1))
            elif name == "close_all_windows":
                act_close_all_windows()
            else:
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
        elif name == "dictate_start":
            act_dictate_start()
        elif name == "macro_add":
            act_macro_add(m.group(1), m.group(2))
        elif name == "macro_list":
            act_macro_list()
        elif name == "macro_delete":
            act_macro_delete(m.group(1))
        elif name == "ascii_art":
            act_ascii_art()
        elif name == "draw_svg":
            act_draw_svg(m.group(3))
        elif name == "roll_dice":
            act_roll_dice()
        elif name == "coin_flip":
            act_coin_flip()
        elif name == "eight_ball":
            act_8ball()
        elif name == "eight_ball_bare":
            say("Ask me a yes or no question")
        elif name == "joke":
            act_joke()
        elif name == "reminder":
            act_reminder(int(m.group(1)), m.group(2), m.group(3))
        elif name == "read_screen":
            act_read_screen()
        elif name == "play_genre":
            act_play_genre(m.group(1))
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
    "open_app {app: name}, quit_app {app: name}, switch_app {app: name}, "
    "minimize {app: optional name}, hide {app: optional name}, "
    "close_window {}, close_all_windows {}, "
    "new_tab {}, close_tab {}, reopen_tab {}, refresh_page {}, "
    "nav_back {}, nav_forward {}, scroll_down {}, scroll_up {}, "
    "next_space {}, prev_space {}, move_next_display {}, "
    "snap_left {}, snap_right {}, maximize_window {}, center_window {}, "
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
    "open_app", "quit_app", "switch_app", "minimize", "hide",
    "close_window", "close_all_windows",
    "new_tab", "close_tab", "reopen_tab", "refresh_page",
    "nav_back", "nav_forward", "scroll_down", "scroll_up",
    "next_space", "prev_space", "move_next_display",
    "snap_left", "snap_right", "maximize_window", "center_window",
    "type_text", "web_search", "open_url",
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
    is_thinking_model = any(k in OLLAMA_MODEL.lower() for k in ("qwen3", "r1", "deepseek"))
    messages = [
        {"role": "system", "content": _TIER1_SYSTEM},
        {"role": "user", "content": text},
    ]
    if is_thinking_model:
        # Pre-fill assistant response to skip reasoning tokens in thinking models
        # and enforce sub-1.5s immediate JSON routing responses.
        messages.append({"role": "assistant", "content": '{"action": "'})

    body = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "keep_alive": "60m",  # stay resident: no cold starts
        "think": False,  # voice needs the answer, not a reasoning trace
        "options": {"temperature": 0, "num_predict": 64, "num_ctx": 1024},
        "messages": messages,
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
        note_error("Ollama unreachable")
        log(f"Tier 1 unavailable (Ollama not reachable at {OLLAMA_HOST}): {e}")
        return None
    dt = time.time() - t0
    raw_content = data.get("message", {}).get("content", "").strip()
    if is_thinking_model and not raw_content.startswith("{"):
        raw_content = '{"action": "' + raw_content
    try:
        parsed = json.loads(raw_content)
    except Exception:  # noqa: BLE001
        log(f"Tier 1 returned unparseable JSON: {raw_content[:80]}")
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


def _tier1_confirm(desc: str, allow_destructive: bool) -> bool:
    """Spoken confirmation for Tier 1 destructive actions.

    Tier 1 has no confirm_audio_fn plumbed through, so it records the
    confirmation itself via record_fixed. If no mic is available (e.g.
    --text mode), the action is declined rather than run unconfirmed.
    """
    if allow_destructive or DRY_RUN:
        return True
    try:
        return confirm_spoken(desc, record_fixed)
    except Exception as e:  # noqa: BLE001 - no mic / headless
        log(f"confirmation unavailable ({e}); declining destructive Tier 1 action")
        say(f"Not {desc} without confirmation")
        return False


def dispatch_tier1(action: str, params: dict, allow_destructive: bool = False) -> None:
    """Execute a Tier 1 JSON decision using the same act_* primitives.
    Every enum/param is validated — JSON mode guarantees shape, NOT sense."""
    p = params.get
    app_name = str(p("app") or p("app_name") or p("name") or "")
    if action == "open_app":
        act_open_app(app_name)
    elif action == "quit_app":
        if _tier1_confirm(f"quitting {app_name}", allow_destructive):
            act_quit_app(app_name)
    elif action == "switch_app":
        act_switch_app(app_name)
    elif action == "close_window":
        act_keystroke("w", "command down"); say("Closed")
    elif action == "close_all_windows":
        if _tier1_confirm("closing all windows", allow_destructive):
            act_close_all_windows()
    elif action == "new_tab":
        act_keystroke("t", "command down"); say("New tab")
    elif action == "close_tab":
        act_keystroke("w", "command down"); say("Closed tab")
    elif action == "reopen_tab":
        act_keystroke("t", "shift down, command down"); say("Reopened tab")
    elif action == "refresh_page":
        act_keystroke("r", "command down"); say("Refreshed")
    elif action == "nav_back":
        act_keystroke("[", "command down"); say("Back")
    elif action == "nav_forward":
        act_keystroke("]", "command down"); say("Forward")
    elif action in ("scroll_down", "page_down"):
        act_key_code(121); say("Scrolled down")
    elif action in ("scroll_up", "page_up"):
        act_key_code(116); say("Scrolled up")
    elif action == "next_space":
        act_key_code(124, "control down"); say("Next space")
    elif action == "prev_space":
        act_key_code(123, "control down"); say("Previous space")
    elif action == "move_next_display":
        act_move_next_display()
    elif action == "minimize":
        act_minimize(app_name)
    elif action == "hide":
        act_hide(app_name)
    elif action == "snap_left":
        act_snap_window("left")
    elif action == "snap_right":
        act_snap_window("right")
    elif action == "maximize_window":
        act_snap_window("maximize")
    elif action == "center_window":
        act_snap_window("center")
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


# ---------------------------------------------------------------- Tier 2: Gemini free-tier Q&A (search-grounded)

def ollama_answer(prompt: str) -> bool:
    """Answer a free-form question with the local model (offline fallback).

    Used when Gemini's free tier is exhausted (HTTP 429) or unreachable.
    """
    global _ollama_ok
    if _ollama_ok is False:
        return False
    body = {
        "model": OLLAMA_MODEL,
        "keep_alive": "60m",
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 150},
        "messages": [
            {"role": "system", "content": (
                "You are a concise Mac voice assistant. Answer in one or two "
                "short spoken sentences. Plain text only, no markdown, no lists."
            )},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    try:
        req = urllib.request.Request(
            OLLAMA_HOST + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        _ollama_ok = True
        text = data["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001
        _ollama_ok = False
        note_error("local model answer failed")
        log(f"local answer failed: {e}")
        return False
    if text:
        say(text)
        return True
    return False


def prewarm_ollama() -> None:
    """Load the Tier 1/vision model into memory in a background thread.

    A cold 8B model takes a while to load — doing it at startup means the
    first real command doesn't stall. Fire-and-forget: failures just log.
    """
    if not OLLAMA_TIER1 or DRY_RUN:
        return

    def _load() -> None:
        try:
            body = {
                "model": OLLAMA_MODEL,
                "keep_alive": "60m",
                "think": False,
                "options": {"num_predict": 1},
                "messages": [{"role": "user", "content": "Reply with: ok"}],
                "stream": False,
            }
            req = urllib.request.Request(
                OLLAMA_HOST + "/api/chat",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                json.load(r)
            log(f"prewarmed {OLLAMA_MODEL} (model resident for 60m)")
        except Exception as e:  # noqa: BLE001
            log(f"prewarm skipped ({e})")

    threading.Thread(target=_load, daemon=True, name="ollama-prewarm").start()


def gemini_answer(prompt: str) -> bool:
    """Answer a free-form question with Gemini's free API tier, spoken aloud.

    Google Search grounding is enabled so fresh questions ("who won last
    night") get live answers, still on the free tier. On ANY Gemini failure
    (quota 429, other HTTP errors, network issues) it falls back to the
    local model and says so.
    """
    body = {
        "system_instruction": {
            "parts": [{"text": (
                "You are a concise Mac voice assistant. Answer in one or two "
                "short spoken sentences. Plain text only, no markdown, no lists."
            )}]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],  # live web grounding, still free tier
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
    except urllib.error.HTTPError as e:
        if e.code == 429:
            log("gemini 429 — free-tier quota hit, falling back to local model")
            say("Google's free limit is hit, answering from the on-device model")
        else:
            note_error(f"Gemini HTTP {e.code}")
            log(f"gemini HTTP {e.code}: {e} — falling back to local model")
            say("Google didn't answer, trying the on-device model")
        return ollama_answer(prompt)
    except Exception as e:  # noqa: BLE001
        note_error("Gemini unreachable")
        log(f"gemini failed ({e}) — falling back to local model")
        say("Google didn't answer, trying the on-device model")
        return ollama_answer(prompt)


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

    # Voice macros (user-defined): built-ins always win, so check here —
    # before chaining and Tier 1 — and an exact trigger match runs instead
    # of being misrouted to the LLM.
    if run_macro(t, confirm_audio_fn=confirm_audio_fn,
                 allow_destructive=allow_destructive,
                 quiet_miss=quiet_miss):
        return True

    # Compound command chaining: if single Tier 0 missed, try chaining (e.g. "open notes and snap left")
    if " and " in t.lower() or " then " in t.lower() or ", " in t:
        parts = [p.strip() for p in re.split(r"\s+(?:and\s+then|then|and)\s+|,\s*", t, flags=re.IGNORECASE) if p.strip()]
        if len(parts) > 1:
            routes = [route(p, partial=False) for p in parts]
            if all(sub_r is not None for sub_r in routes):
                log(f"Tier 0 chained hit ({len(parts)} commands): {parts}")
                for (name, m) in routes:
                    execute_match(name, m, confirm_audio_fn, allow_destructive)
                    time.sleep(0.3)
                return True

    # Tier 1: local vision-language model (only on Tier 0 miss)
    t1 = ollama_route(t)
    if t1:
        action, params, conf = t1
        try:
            dispatch_tier1(action, params, allow_destructive)
        except Exception as e:  # noqa: BLE001
            say("That didn't work")
            log(f"tier 1 action failed: {e}")
        return True

    # Tier 2: Q&A (local Ollama by default, or Gemini if configured)
    if GEMINI_API_KEY:
        if gemini_answer(t):
            return True
    elif not quiet_miss:
        if ollama_answer(t):
            return True
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
        "switch to safari / focus terminal / bring up notes",
        "quit spotify / close safari  (ask first)",
        "close · close window · close all windows  (close-all asks first)",
        "snap left · snap right · maximize · center window",
        "open notes and snap left · set volume to 30 and play  (chained commands)",
        "read clipboard · type today's date · type the time · type my email",
        "type hello world / dictate dear mom,",
        "press enter / copy / paste / undo / save / select all",
        "click the Reply button / click the Docs link  (needs xa11y + perms)",
        "click  (clicks where the mouse already is)",
        "move the mouse to the toggle / move mouse up / move mouse left 200",
        "scroll up / scroll down 3 / scroll left / scroll right",
        "what's on my screen / describe my screen  (needs qwen3-vl model)",
        "are you working / status  (spoken health check)",
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
    print("\nTiers: 0 = instant regex (always) · 1 = local " + OLLAMA_MODEL +
          f" ({'on' if OLLAMA_TIER1 else 'off'}) · 2 = Gemini free tier "
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
    prewarm_ollama()  # load the local model now, not on the first command
    if args.once:
        usable, _ = _usable_input()
        if not usable:
            _notice_no_mic()
            return
        try:
            on_utterance(record_fixed(args.once))
        except Exception as e:
            log(f"couldn't record ({e})")
        return
    if args.always:
        global ALWAYS_MODE
        ALWAYS_MODE = True
        always_listen_loop(lambda audio: on_utterance(audio, quiet_miss=True),
                           sensitivity=args.sensitivity)
        return
    push_to_talk_loop(on_utterance)


if __name__ == "__main__":
    main()
