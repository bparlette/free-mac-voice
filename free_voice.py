#!/usr/bin/env python3
"""
free_voice.py — free, fast voice control for macOS (v2: 3-tier "Instant & Free").

Pipeline (everything local, $0 per command):
    mic (hold right-Option, speak, release)
      -> mlx-whisper on Metal GPU (~100-300 ms, local, free; or faster-whisper fallback)
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

Setup: reuse the venv from setup.sh (mlx-whisper / faster-whisper, sounddevice, pynput).
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
import hashlib
import html
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
import zlib

try:
    from speaker_id import (
        verify_speaker,
        enroll_speaker,
        is_speaker_enrolled,
        reset_speaker,
        get_profile_path,
        get_model_path,
        ensure_model,
        SPEAKER_THRESHOLD,
    )
except ImportError:
    verify_speaker = lambda a, **kw: (True, 1.0)
    enroll_speaker = lambda s, **kw: 1.0
    is_speaker_enrolled = lambda **kw: False
    reset_speaker = lambda **kw: False
    get_profile_path = lambda: ""
    get_model_path = lambda: ""
    ensure_model = lambda **kw: ""
    SPEAKER_THRESHOLD = 0.17

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
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base.en")
# Speech-to-text engine: "mlx-whisper" (Metal GPU on Apple Silicon), "phonon" (Parakeet-TDT), or "whisper" (faster-whisper CPU)
VOICE_STT_ENGINE = os.environ.get("VOICE_STT_ENGINE", "mlx-whisper").strip().lower()
# Tier 1 (local LLM fallback). Set OLLAMA_TIER1=0 to disable.
# Default is qwen3-vl:8b — a vision-language model, so one local model covers
# routing, Q&A, AND screen understanding. 8GB minis: use qwen3-vl:4b instead.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b")
# Local fast decision model for sub-100ms intent classification (tev1:0.8b by default, or qwen2.5:1.5b)
OLLAMA_DECISION_MODEL = os.environ.get("OLLAMA_DECISION_MODEL", "tev1:0.8b").strip()
# Confidence threshold for decision routing: 0.7 for tev1:0.8b, 0.5 for qwen2.5:1.5b
DECISION_MIN_CONFIDENCE = float(os.environ.get("DECISION_MIN_CONFIDENCE", "0.7" if "tev1" in OLLAMA_DECISION_MODEL.lower() else "0.5"))
# Model for multi-action sequential planning in JSON chat mode (generative model required)
OLLAMA_PLANNER_MODEL = os.environ.get("OLLAMA_PLANNER_MODEL", "qwen2.5:1.5b").strip()
# Pre-routing voice command gate (1=enabled, 0=disabled)
VOICE_COMMAND_GATE = os.environ.get("VOICE_COMMAND_GATE", "1").strip().lower() in ("1", "true", "yes")
VOICE_COMMAND_GATE_MODEL = os.environ.get("VOICE_COMMAND_GATE_MODEL", "tev1:0.8b").strip()
# Fast text model for SVG vector generation (qwen2.5:1.5b by default: ~2s generation, zero thinking overhead)
OLLAMA_DRAW_MODEL = os.environ.get("OLLAMA_DRAW_MODEL", "qwen2.5:1.5b")
OLLAMA_TIER1 = os.environ.get("OLLAMA_TIER1", "1") == "1"
TIER1_MIN_CONFIDENCE = float(os.environ.get("TIER1_MIN_CONFIDENCE", "0.5"))
# Microphone selection: case-insensitive substring matched against input
# device names, e.g. VOICE_MIC=iPhone uses the Continuity microphone whenever
# the iPhone is in range, falling back to the system default otherwise.
# Overridable per-run with --mic.
VOICE_MIC = os.environ.get("VOICE_MIC", "")
VOICE_USER_EMAIL = os.environ.get("VOICE_USER_EMAIL", "").strip()
# Wake word for always-listening mode. Default is "mac". Empty string disables.
VOICE_WAKE_WORD = os.environ.get("VOICE_WAKE_WORD", "mac").strip().lower()
# Wake word feedback style: "both" (chime + "Yes?"), "chime" (chime only), "voice" ("Yes?" only), "silent"
VOICE_WAKE_FEEDBACK = os.environ.get("VOICE_WAKE_FEEDBACK", "both").strip().lower()
VOICE_WAKE_CHIME = os.environ.get("VOICE_WAKE_CHIME", "Tink.aiff").strip()
WAKE_WINDOW_SEC = float(os.environ.get("VOICE_WAKE_WINDOW", "15.0"))
WAKE_WINDOW_MAX_CAP_SEC = float(os.environ.get("VOICE_WAKE_MAX_CAP", "60.0"))
VOICE_WAKE_PHRASE = os.environ.get("VOICE_WAKE_PHRASE", "what you want").strip()
VOICE_VAD_SENSITIVITY = float(os.environ.get("VOICE_VAD_SENSITIVITY", "1.35"))
_wake_window_until = 0.0
_wake_window_opened_at = 0.0
DRY_RUN = False



_ollama_ok: bool | None = None  # None=untried, True=working
_ollama_last_failure: float = 0.0  # retry after cooldown rather than permanent lockout
_decision_ok: bool | None = None
_decision_last_failure: float = 0.0



def acknowledge_wake() -> None:
    """Provide audio feedback when wake word is heard alone."""
    fb = VOICE_WAKE_FEEDBACK
    if fb in ("chime", "both"):
        play_chime(VOICE_WAKE_CHIME or "Tink.aiff")
    if fb in ("voice", "both"):
        say(VOICE_WAKE_PHRASE or "what you want")


_STATE_FILE = os.path.join(tempfile.gettempdir(), "free-voice-state.json")


_last_state: tuple = ("", {})
HEARTBEAT_SEC = 10.0  # menu_bar.py treats a state older than 30 s as "Not Running"


def update_state(state: str, **kwargs) -> None:
    """Publish current status for menu bar / external observers."""
    global _last_state
    _last_state = (state, kwargs)
    if DRY_RUN:
        return
    try:
        data = {
            "state": state,
            "wake_word": VOICE_WAKE_WORD,
            "always": ALWAYS_MODE,
            "ts": time.time(),
            **kwargs,
        }
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


def heartbeat_state(wake_word: str = "") -> None:
    """Re-publish the current state with a fresh timestamp so quiet listening
    isn't shown as "Not Running". A finished command ("processing") or an
    expired wake window goes back to "listening"."""
    state, kw = _last_state
    if not state or state == "processing" or \
            (state == "wake_heard" and time.time() >= _wake_window_until):
        update_state("listening", wake_word=wake_word)
    else:
        update_state(state, **kw)


VOICE_ENABLE_HUD = os.environ.get("VOICE_ENABLE_HUD", "1").lower() in ("1", "true", "yes")
_IN_TESTS = "unittest" in sys.modules or "pytest" in sys.modules


def notify_hud(text: str, state: str = "action") -> None:
    """Send an asynchronous notification to the on-screen HUD (e.g. Hammerspoon).

    Non-blocking: fire-and-forget in a daemon thread. Silently ignores if no HUD is running.
    """
    if not VOICE_ENABLE_HUD or DRY_RUN or _IN_TESTS or not text:
        return

    def _send():
        try:
            payload = json.dumps({"text": text, "state": state}).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:19825/hud",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=0.08):
                pass
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def _notify_hammerspoon_display_next() -> bool:
    """Ask Hammerspoon to move the focused window to the next display via its HTTP API."""
    if not VOICE_ENABLE_HUD or DRY_RUN or _IN_TESTS:
        return False
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19825/display/next",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=0.15) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False


def parse_wake_word(text: str, wake_word: str = "mac") -> tuple[bool, str]:
    """Check if text begins with the wake word (e.g. 'Mac', 'Hey Mac', 'Mack').

    Returns (is_wake, command_text).
    - If text starts with the wake word followed by a command:
        (True, "stripped command")
    - If text is ONLY the wake word (or wake greeting):
        (True, "")
    - If text does NOT start with the wake word:
        (False, text)
    """
    t = text.strip().lstrip(".-~ \t")
    if not t or not wake_word:
        return bool(t), t
    w = wake_word.strip().lower()

    # Greetings can be followed by commas or spaces, or merged: "Hey, Mac", "Hay Mac", "And Mac", "heymac"
    greeting = r"(?:(?:hey|hay|hi|hello|ok|okay|yo|ay|ey|and|a|an)[,\s]*)*"
    if w == "mac":
        # Handle "mac", "mack", and common Whisper phoneme variants
        target = r"(?:(?:mac|mack|matt|max|mark|match|make|mike|mock|macs|mac\'s)\b[,\s:!\.-]*)+"
    else:
        target = r"(?:" + re.escape(w) + r"\b[,\s:!\.-]*)+"

    rx = re.compile(rf"^{greeting}{target}(?:[,\s:!\.-]+\s*(.*)|(.*))?$", re.IGNORECASE)
    m = rx.match(t)
    if m:
        cmd = (m.group(1) or m.group(2) or "").strip()
        cmd = re.sub(r"^[,\s:!\.-]+", "", cmd).strip()
        return True, cmd
    return False, t


def log(msg: str) -> None:
    t_str = time.strftime("%H:%M:%S")
    print(f"[free-voice] {t_str} {msg}", flush=True)


# ---------------------------------------------------------------- health tracking
_START_TIME = time.time()
_last_error = ""  # short human-readable note of the most recent backend failure


def note_error(msg: str) -> None:
    """Remember the latest backend failure for the 'are you working' status."""
    global _last_error
    _last_error = f"{datetime.now():%H:%M} — {msg}"
    log(f"error noted: {msg}")


# ---------------------------------------------------------------- speech out

_CONFIG_DIR = os.path.join(HOME, ".config", "free-voice")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")
_config_cache: dict | None = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        try:
            with open(_CONFIG_FILE) as f:
                data = json.load(f)
            _config_cache = data if isinstance(data, dict) else {}
        except Exception:
            _config_cache = {}
    return _config_cache


def _save_config(cfg: dict) -> None:
    global _config_cache
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    _config_cache = cfg


_cfg = _load_config()
VOICE_TTS_ENGINE = os.environ.get("VOICE_TTS_ENGINE", _cfg.get("tts_engine", "kokoro")).strip().lower()
VOICE_KOKORO_VOICE = os.environ.get("VOICE_KOKORO_VOICE", _cfg.get("kokoro_voice", "am_fenrir")).strip()
VOICE_SAY_VOICE = os.environ.get("VOICE_SAY_VOICE", _cfg.get("say_voice", "Samantha")).strip()

KOKORO_MODEL_PATH = os.environ.get(
    "KOKORO_MODEL_PATH",
    os.path.join(_CONFIG_DIR, "models", "kokoro", "kokoro-v1.0.onnx")
)
KOKORO_VOICES_PATH = os.environ.get(
    "KOKORO_VOICES_PATH",
    os.path.join(_CONFIG_DIR, "models", "kokoro", "voices-v1.0.bin")
)

_kokoro_instance = None
_kokoro_failed = False
_tts_cache_dir = os.path.join(tempfile.gettempdir(), "free-voice-tts-cache")

_say_proc = None  # in-flight speech process (say or afplay), so new speech cuts off the old


def is_speaking() -> bool:
    """Return True if TTS audio playback is currently in flight."""
    return _say_proc is not None and _say_proc.poll() is None


# Curated voice catalog for Kokoro
KOKORO_VOICES: dict[str, tuple[str, str]] = {
    # American Female
    "heart": ("Heart", "af_heart"),
    "sarah": ("Sarah", "af_sarah"),
    "bella": ("Bella", "af_bella"),
    "nicole": ("Nicole", "af_nicole"),
    "nova": ("Nova", "af_nova"),
    "sky": ("Sky", "af_sky"),
    "alloy": ("Alloy", "af_alloy"),
    "jessica": ("Jessica", "af_jessica"),
    "river": ("River", "af_river"),
    "kore": ("Kore", "af_kore"),
    "aoede": ("Aoede", "af_aoede"),
    # American Male
    "fenrir": ("Fenrir", "am_fenrir"),
    "adam": ("Adam", "am_adam"),
    "michael": ("Michael", "am_michael"),
    "liam": ("Liam", "am_liam"),
    "echo": ("Echo", "am_echo"),
    "eric": ("Eric", "am_eric"),
    "onyx": ("Onyx", "am_onyx"),
    "puck": ("Puck", "am_puck"),
    "santa": ("Santa", "am_santa"),
    # British Female
    "emma": ("Emma", "bf_emma"),
    "alice": ("Alice", "bf_alice"),
    "isabella": ("Isabella", "bf_isabella"),
    "lily": ("Lily", "bf_lily"),
    # British Male
    "george": ("George", "bm_george"),
    "daniel": ("Daniel", "bm_daniel"),
    "fable": ("Fable", "bm_fable"),
    "lewis": ("Lewis", "bm_lewis"),
}

KOKORO_SHOWCASE: list[tuple[str, str]] = [
    ("Fenrir", "am_fenrir"),
    ("Heart", "af_heart"),
    ("Adam", "am_adam"),
    ("Sarah", "af_sarah"),
    ("George", "bm_george"),
    ("Nicole", "af_nicole"),
    ("Emma", "bf_emma"),
    ("Michael", "am_michael"),
]

MACOS_SHOWCASE: list[tuple[str, str]] = [
    ("Samantha", "Samantha"),
    ("Alex", "Alex"),
    ("Daniel", "Daniel"),
    ("Karen", "Karen"),
    ("Fred", "Fred"),
]


def _get_kokoro():
    """Lazily load Kokoro ONNX model session."""
    global _kokoro_instance, _kokoro_failed
    if _kokoro_failed:
        return None
    if _kokoro_instance is not None:
        return _kokoro_instance
    if not (os.path.isfile(KOKORO_MODEL_PATH) and os.path.isfile(KOKORO_VOICES_PATH)):
        return None
    try:
        from kokoro_onnx import Kokoro
        _kokoro_instance = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
        log("Kokoro TTS initialized successfully")
        return _kokoro_instance
    except Exception as e:
        log(f"Kokoro initialization failed ({e}), falling back to native say")
        _kokoro_failed = True
        return None


_TTS_CACHE_MAX_CHARS = 40    # only short, fixed phrases ("Volume 30", "Muted") are cached
_TTS_CACHE_MAX_FILES = 200   # LRU cap (mtime is bumped on every hit)
_last_uncached_wav: str | None = None


def _prune_tts_cache() -> None:
    """Keep at most _TTS_CACHE_MAX_FILES cached phrases, dropping the oldest."""
    try:
        files = [os.path.join(_tts_cache_dir, f) for f in os.listdir(_tts_cache_dir)
                 if f.endswith(".wav") and not f.startswith("uncached-")]
        if len(files) <= _TTS_CACHE_MAX_FILES:
            return
        files.sort(key=os.path.getmtime)
        for f in files[:len(files) - _TTS_CACHE_MAX_FILES]:
            try:
                os.remove(f)
            except OSError:
                pass
    except OSError:
        pass


def _synthesize_kokoro(text: str, voice: str | None = None) -> str | None:
    """Synthesize text using Kokoro-82M, caching short common phrases only
    (long text such as clipboard reads or LLM answers is never kept). Returns wav path."""
    global _last_uncached_wav
    kokoro = _get_kokoro()
    if kokoro is None:
        return None
    v = voice or VOICE_KOKORO_VOICE
    try:
        import soundfile as sf
        os.makedirs(_tts_cache_dir, exist_ok=True)
        if len(text) >= _TTS_CACHE_MAX_CHARS:
            fd, wav_path = tempfile.mkstemp(prefix="uncached-", suffix=".wav", dir=_tts_cache_dir)
            os.close(fd)
            samples, sample_rate = kokoro.create(text, voice=v, speed=1.0, lang="en-us")
            sf.write(wav_path, samples, sample_rate)
            prev, _last_uncached_wav = _last_uncached_wav, wav_path
            if prev:
                try:
                    os.remove(prev)  # safe even if afplay still has it open
                except OSError:
                    pass
            return wav_path

        cache_key = hashlib.md5(f"{v}:{text}".encode("utf-8")).hexdigest()
        wav_path = os.path.join(_tts_cache_dir, f"{cache_key}.wav")
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            try:
                os.utime(wav_path)  # LRU touch
            except OSError:
                pass
            return wav_path

        samples, sample_rate = kokoro.create(text, voice=v, speed=1.0, lang="en-us")
        sf.write(wav_path, samples, sample_rate)
        _prune_tts_cache()
        return wav_path
    except Exception as e:
        log(f"Kokoro synthesis error ({e}), falling back to native say")
        return None


def say(text: str, blocking: bool = False, voice: str | None = None) -> None:
    """Speak text. Default engine is Kokoro (neural speech) with instant fallback
    to macOS native `say`. Non-blocking by default: fire-and-forget Popen so the
    action feels instant instead of waiting ~2s for the voice to finish. Any
    in-flight speech is terminated first so rapid commands don't talk over each other.
    Pass blocking=True when the full prompt must be heard before continuing."""
    global _say_proc
    log(f"say: {text}")
    notify_hud(text, "action")
    if DRY_RUN:
        return
    try:
        if _say_proc is not None and _say_proc.poll() is None:
            _say_proc.terminate()
            _say_proc = None

        if VOICE_TTS_ENGINE == "kokoro":
            wav = _synthesize_kokoro(text, voice=voice)
            if wav and os.path.exists(wav):
                if blocking:
                    subprocess.run(["afplay", wav], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    _say_proc = subprocess.Popen(
                        ["afplay", wav],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

        # Fallback / Native macOS say
        cmd = ["say"]
        if voice:
            cmd.extend(["-v", voice])
        cmd.append(text)

        if blocking:
            subprocess.run(cmd, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            _say_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # e.g. test environments without the macOS `say` binary


def play_chime(sound_name: str = "Tink.aiff") -> None:
    """Play a short macOS audio notification sound non-blockingly."""
    if DRY_RUN:
        return
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
    want = VOICE_MIC.strip()
    note = ""
    if not usable and want and _usable_input("")[0]:
        # VOICE_MIC not present; wait_for_input_device falls back to default
        usable, idx, note = True, None, f" (default input; {want!r} not found)"
    if not usable:
        return (f"none found — waiting for {want!r}") if want \
            else "none found — waiting for a microphone"
    try:
        import sounddevice as sd
        dev = sd.query_devices(idx) if idx is not None \
            else sd.query_devices(kind="input")
        return str(dev.get("name", "?")) + note
    except Exception:
        return "?" + note


def wait_for_input_device(poll_s: float = 3.0, fallback_after_s: float = 6.0):
    """Block until a usable microphone appears; return its device index.

    Honors VOICE_MIC (see _usable_input). Polls so a mic connected later —
    iPhone coming in range, USB mic plugged in — is picked up automatically.
    If VOICE_MIC is set but that mic hasn't appeared after `fallback_after_s`,
    falls back to the system default input (None) and logs it.
    Never raises for a missing mic; KeyboardInterrupt passes through.
    """
    want = VOICE_MIC.strip()
    announced = False
    started = time.monotonic()
    while True:
        usable, idx = _usable_input()
        if usable:
            return idx
        if want and time.monotonic() - started >= fallback_after_s \
                and _usable_input("")[0]:
            log(f"VOICE_MIC {want!r} not found after {fallback_after_s:.0f}s — "
                "falling back to the default input")
            return None
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
    import numpy as np  # kept local so --text mode needs no audio deps

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
_phonon_model = None


def _get_phonon_model():
    global _phonon_model
    if _phonon_model is None:
        try:
            from fermion.transcribe import _resolve
            from fermion._speech import backends, fetch

            repo, key, pin, local_dir = _resolve("phonon-2")
            engine_kind = pin.get("engine", "phonon2")
            model_dir = local_dir if local_dir is not None else fetch.ensure(repo, key, pin)
            log("loading Phonon-2 ASR model (164MB Parakeet-TDT)...")
            _phonon_model = backends.load(engine_kind, model_dir, profile=key, backend=pin["backend"], quiet=True)
            log("Phonon-2 ASR model ready")
        except Exception as e:
            log(f"Phonon-2 initialization skipped ({e}); using Whisper")
            _phonon_model = False
    return _phonon_model if _phonon_model is not False else None


def _resolve_mlx_whisper_repo(model_name: str) -> str:
    m = model_name.strip()
    if "/" in m:
        return m
    base_m = m.lower().replace(".en", "")
    mapping = {
        "tiny": "mlx-community/whisper-tiny-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "large": "mlx-community/whisper-large-v3-mlx",
    }
    return mapping.get(base_m, f"mlx-community/whisper-{base_m}-mlx")


def transcribe(audio) -> str:
    global _whisper
    if VOICE_STT_ENGINE == "phonon":
        phonon = _get_phonon_model()
        if phonon is not None:
            try:
                res = phonon.transcribe_array_detailed(audio)
                text, _, _ = res.triple()
                return text.strip()
            except Exception as e:
                log(f"Phonon-2 decode failed ({e}) — falling back to Whisper")

    # If _whisper is explicitly set/mocked, use it directly
    if _whisper is not None:
        segments, _ = _whisper.transcribe(
            audio,
            beam_size=1,
            vad_filter=True,
            initial_prompt="Mac, Safari, Chrome, Finder, Terminal, Notes, System Settings, snap left, snap right, maximize, volume.",
        )
        return " ".join(s.text for s in segments).strip()

    # Metal GPU accelerated MLX-Whisper on Apple Silicon
    if VOICE_STT_ENGINE in ("mlx-whisper", "mlx", "whisper"):
        try:
            import mlx_whisper

            repo = _resolve_mlx_whisper_repo(WHISPER_MODEL)
            res = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=repo,
                initial_prompt="Mac, Safari, Chrome, Finder, Terminal, Notes, System Settings, snap left, snap right, maximize, volume.",
            )
            return str(res.get("text", "")).strip()
        except Exception as e:
            log(f"MLX-Whisper unavailable ({e}) — falling back to CPU Whisper")

    from faster_whisper import WhisperModel

    log(f"loading whisper model '{WHISPER_MODEL}' (first run downloads it)...")
    _whisper = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
    segments, _ = _whisper.transcribe(
        audio,
        beam_size=1,
        vad_filter=True,
        initial_prompt="Mac, Safari, Chrome, Finder, Terminal, Notes, System Settings, snap left, snap right, maximize, volume.",
    )
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
                def _safe_dispatch():
                    try:
                        on_utterance(audio)
                    except Exception as e:
                        log(f"push-to-talk error ({e}) — still listening")
                threading.Thread(target=_safe_dispatch, daemon=True).start()
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

    def __init__(self, sensitivity: float = 1.35, frame_ms: int = 30,
                 start_ms: int = 90, end_ms: int = 550):
        self.sensitivity = sensitivity
        self.start_needed = max(1, start_ms // frame_ms)
        self.end_needed = max(1, end_ms // frame_ms)
        self.floor = 200.0          # adaptive RMS noise floor (int16 units)
        self.in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0

    def reset(self, new_floor: float | None = None) -> None:
        """Reset detection state and optionally calibrate the noise floor."""
        self.in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        if new_floor is not None and new_floor > 0:
            self.floor = max(new_floor, 60.0)

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
        stop_threshold = max(self.floor * 1.15, self.floor + 50.0)
        if rms < stop_threshold:
            self._silence_frames += 1
            if self._silence_frames >= self.end_needed:
                self.in_speech = False
                self._speech_frames = 0
                return "end"
        else:
            self._silence_frames = 0
        return "speech"


def always_listen_loop(on_utterance, sensitivity: float = 1.35, wake_word: str = "") -> None:
    """Listen continuously; transcribe each detected utterance. Ctrl-C quits.

    In wake word mode, commands must start with the wake word (e.g. 'Mac, ...')
    or follow a standalone wake word trigger within the wake window
    (WAKE_WINDOW_SEC, 15 s by default; VOICE_WAKE_WINDOW overrides it).
    Ambient room chatter is ignored silently.
    """
    import sounddevice as sd
    import numpy as np
    from collections import deque

    frame_len = int(16000 * 0.03)  # 30 ms frames
    max_frames = int(15 / 0.03)   # 15 s safety cap per utterance

    if wake_word:
        log(f"always-listening: say '{wake_word.title()}, <command>' "
            f"(or say '{wake_word.title()}' and wait for chime). Ctrl-C quits.")
    else:
        log("always-listening: speak naturally, Ctrl-C quits. "
            "(no wake word — speech itself is the trigger)")
    try:
        while True:  # outer: re-acquire the mic if it vanishes
            idx = wait_for_input_device()
            vad = VoiceActivityDetector(sensitivity=sensitivity)
            capturing: list[np.ndarray] = []
            # Pre-roll: keep ~750ms of audio before speech onset to prevent
            # clipping soft consonants and the wake word (e.g. "Mac").
            preroll: deque = deque(maxlen=max(25, vad.start_needed + 15))
            log(f"microphone: {describe_input_device()} — listening")
            update_state("listening", wake_word=wake_word)
            play_chime("Tink.aiff")
            try:
                with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16",
                                       blocksize=frame_len,
                                       device=idx) as stream:
                    # Drain startup frames (chime echo / stream stabilization) and calibrate initial noise floor
                    startup_rms = []
                    for _ in range(16):
                        try:
                            d, _ = stream.read(frame_len)
                            s_init = np.frombuffer(d, dtype=np.int16)
                            startup_rms.append(float(np.sqrt(np.mean(s_init.astype(np.float64) ** 2))))
                        except Exception:
                            break
                    if startup_rms:
                        vad.floor = max(float(np.median(startup_rms)), 60.0)

                    last_hb = time.monotonic()
                    was_speaking = False
                    while True:
                        try:
                            data, _ = stream.read(frame_len)
                        except Exception as e:
                            log(f"microphone error ({e}) — waiting for it to come back…")
                            time.sleep(2)  # cooldown so a flapping device doesn't hot-spin
                            break
                        if time.monotonic() - last_hb >= HEARTBEAT_SEC:
                            heartbeat_state(wake_word)
                            last_hb = time.monotonic()
                        samples = np.frombuffer(data, dtype=np.int16)
                        if is_speaking():
                            capturing = []
                            preroll.clear()
                            vad.reset()
                            was_speaking = True
                            continue
                        if was_speaking:
                            was_speaking = False
                            # Drain any speaker echo lingering in the audio buffer
                            try:
                                avail = stream.read_available
                                if avail > 0:
                                    stream.read(avail)
                            except Exception:
                                pass
                            preroll.clear()
                            vad.reset()
                            continue
                        state = vad.update(samples)
                        if state == "start":
                            capturing = list(preroll) + [samples.copy()]
                            preroll.clear()
                            log("heard speech, capturing...")
                        elif state == "speech" and capturing:
                            capturing.append(samples.copy())
                            if len(capturing) >= max_frames:
                                state = "end"  # safety cap: cut it off
                                vad.reset()
                        elif state == "silence":
                            preroll.append(samples.copy())
                        if state == "end" and capturing:
                            vad.reset()
                            audio = (np.concatenate(capturing)
                                     .astype(np.float32) / 32768.0)
                            capturing = []
                            if len(audio) > 16000 * 0.4:  # ignore blips < 0.4 s
                                log("transcribing...")
                                try:
                                    on_utterance(audio)
                                except Exception as e:
                                    log(f"command failed ({e}) — still listening")
                                heartbeat_state(wake_word)  # "processing" -> "listening"
                                last_hb = time.monotonic()
                            # Drain stale audio accumulated in buffer while transcribing/speaking
                            try:
                                avail = stream.read_available
                                if avail > 0:
                                    stream.read(avail)
                            except Exception:
                                pass
                            preroll.clear()
                            vad.reset()
                            time.sleep(0.3)  # cooldown so one sentence = one command
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
    "safari": "Safari", "firefox": "Firefox", "edge": "Microsoft Edge", "browser": "Safari",
    "fire": "Safari", "the fire": "Safari", "safire": "Safari", "so fire": "Safari",
    "notes": "Notes", "apple notes": "Notes", "mail": "Mail", "apple mail": "Mail", "email": "Mail",
    "messages": "Messages", "imessage": "Messages", "imessages": "Messages", "text messages": "Messages",
    "facetime": "FaceTime", "photos": "Photos", "music": "Music", "itunes": "Music", "apple music": "Music",
    "tv": "TV", "apple tv": "TV", "podcasts": "Podcasts",
    "finder": "Finder", "settings": "System Settings", "system preferences": "System Settings",
    "preferences": "System Settings", "system settings": "System Settings",
    "terminal": "Terminal", "iterm": "iTerm", "iterm2": "iTerm",
    "calendar": "Calendar", "reminders": "Reminders", "maps": "Maps",
    "preview": "Preview", "textedit": "TextEdit", "text edit": "TextEdit",
    "app store": "App Store", "activity monitor": "Activity Monitor", "task manager": "Activity Monitor",
    "calculator": "Calculator", "calc": "Calculator", "dictionary": "Dictionary",
    "contacts": "Contacts", "freeform": "Freeform",
    "keynote": "Keynote", "pages": "Pages", "numbers": "Numbers",
    "code": "Visual Studio Code", "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "vsc": "Visual Studio Code",
    "cursor": "Cursor", "sublime": "Sublime Text", "sublime text": "Sublime Text",
    "pycharm": "PyCharm", "intellij": "IntelliJ IDEA", "webstorm": "WebStorm",
    "word": "Microsoft Word", "ms word": "Microsoft Word",
    "excel": "Microsoft Excel", "ms excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint", "ms powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook", "teams": "Microsoft Teams",
    "photoshop": "Adobe Photoshop", "illustrator": "Adobe Illustrator", "acrobat": "Adobe Acrobat",
    "figma": "Figma", "notion": "Notion",
    "spotify": "Spotify", "zoom": "zoom.us", "slack": "Slack",
    "discord": "Discord", "steam": "Steam", "kodi": "Kodi",
    "retroarch": "RetroArch", "vlc": "VLC",
    "books": "Books", "news": "News", "stocks": "Stocks",
    "home": "Home", "weather": "Weather", "clock": "Clock",
    "find my": "Find My", "findmy": "Find My",
    "find my iphone": "Find My", "find my phone": "Find My",
    "find my ipad": "Find My", "find my mac": "Find My",
    "find my device": "Find My",
}

_app_cache: list[str] | None = None


def running_apps() -> list[str]:
    """Return list of localized names for currently running applications."""
    try:
        from AppKit import NSWorkspace
        return [
            a.localizedName()
            for a in NSWorkspace.sharedWorkspace().runningApplications()
            if a.localizedName()
        ]
    except Exception:
        return []


def installed_apps() -> list[str]:
    global _app_cache
    if _app_cache is None:
        names: set[str] = set()
        for d in ("/Applications", "/System/Applications", os.path.join(HOME, "Applications"),
                  "/Applications/Utilities", "/System/Applications/Utilities"):
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


# Per-call recomputation caches for resolve_app. Each entry keeps a reference
# to its source object, so a new installed_apps() list (or alias dict change)
# invalidates it.
_clean_aliases_cache: tuple = (None, 0, {})
_phonetic_map_cache: tuple = (None, 0, {})


def _clean_aliases() -> dict:
    global _clean_aliases_cache
    src, n, cached = _clean_aliases_cache
    if src is not _APP_ALIASES or n != len(_APP_ALIASES):
        cached = {_clean_name(k): v for k, v in _APP_ALIASES.items()}
        _clean_aliases_cache = (_APP_ALIASES, len(_APP_ALIASES), cached)
    return cached


def _phonetic_map(apps: list[str]) -> dict:
    global _phonetic_map_cache
    src, n, cached = _phonetic_map_cache
    if src is not apps or n != len(apps):
        cached = {_phonetic_key(a): a for a in apps if _phonetic_key(a)}
        _phonetic_map_cache = (apps, len(apps), cached)
    return cached


def resolve_app(spoken: str, prefer_running: bool = False) -> str | None:
    """Turn 'chrome' / 'notes' / 'es de' into a real app name.

    Fuzzy: aliases, singulars, colloquial prefixes/suffixes, token-set matching,
    exact installed names, running apps preference, normalized alphanumeric,
    substring, phonetic Soundex, then difflib similarity.
    Used for FINAL transcripts, where acting on a best guess is fine.
    """
    import difflib

    raw = spoken.strip().lower()
    # Strip conversational noise: "my notes" -> "notes", "the safari app" -> "safari"
    clean_spoken = re.sub(r"^(?:my|the)\s+", "", raw, flags=re.IGNORECASE).strip()
    clean_spoken = re.sub(r"\s+app$", "", clean_spoken, flags=re.IGNORECASE).strip()

    variants = [raw]
    if clean_spoken and clean_spoken not in variants:
        variants.append(clean_spoken)

    # 0. Check aliases first for all variants & their singular forms
    clean_aliases = _clean_aliases()
    for v in variants:
        if v in _APP_ALIASES:
            return _APP_ALIASES[v]
        sing = v[:-1] if v.endswith("s") else v
        if sing in _APP_ALIASES:
            return _APP_ALIASES[sing]
        clean_v = _clean_name(v)
        if clean_v in clean_aliases:
            return clean_aliases[clean_v]

    # If prefer_running, check currently running applications first
    if prefer_running:
        active = running_apps()
        if active:
            act_low = {a.lower(): a for a in active}
            act_clean = {_clean_name(a): a for a in active}
            for v in variants:
                sing = v[:-1] if v.endswith("s") else v
                for k in (v, sing):
                    if k in act_low:
                        return act_low[k]
                    cv = _clean_name(k)
                    if cv in act_clean:
                        return act_clean[cv]
                for k in (v, sing):
                    cands = [a for a in active if k in a.lower()]
                    if cands:
                        cands.sort(key=len)
                        return cands[0]

    apps = installed_apps()
    low = {a.lower(): a for a in apps}
    clean_map = {_clean_name(a): a for a in apps}

    # 1. Exact raw or normalized match
    for v in variants:
        sing = v[:-1] if v.endswith("s") else v
        for key in (v, sing):
            if key in low:
                return low[key]
        clean_v = _clean_name(v)
        clean_sing = _clean_name(sing)
        for key in (clean_v, clean_sing):
            if key in clean_map:
                return clean_map[key]

    # 2. Token-set matching: all spoken words exist in the app name
    # e.g. "visual studio" -> "Visual Studio Code"
    for v in variants:
        tokens = set(re.findall(r"[a-z0-9]+", v))
        if len(tokens) >= 2:
            matches = []
            for a in apps:
                app_toks = set(re.findall(r"[a-z0-9]+", a.lower()))
                if tokens.issubset(app_toks):
                    matches.append(a)
            if matches:
                matches.sort(key=len)
                return matches[0]

    # 3. Substring matching (raw first, then clean if >= 4 chars)
    for v in variants:
        sing = v[:-1] if v.endswith("s") else v
        for key in (v, sing):
            cands = [a for a in apps if key in a.lower()]
            if cands:
                cands.sort(key=len)
                return cands[0]

        clean_v = _clean_name(v)
        if len(clean_v) >= 4:
            cands = [a for a in apps if clean_v in _clean_name(a)]
            if cands:
                cands.sort(key=len)
                return cands[0]

    # 4. Phonetic matching via Soundex (handles STT phonetic misspellings)
    for v in variants:
        phone_s = _phonetic_key(v)
        if phone_s:
            phone_map = _phonetic_map(apps)
            if phone_s in phone_map:
                return phone_map[phone_s]

    # 5. Fuzzy similarity matching on normalized strings
    for v in variants:
        clean_v = _clean_name(v)
        matches = difflib.get_close_matches(clean_v, list(clean_map.keys()), n=1, cutoff=0.75)
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
    clean_aliases = _clean_aliases()
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
    # If window is fullscreen, macOS blocks Cmd+M; un-fullscreen first
    try:
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            AXUIElementSetAttributeValue,
            kAXFocusedWindowAttribute,
            kAXWindowsAttribute,
        )
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front:
            app_el = AXUIElementCreateApplication(front.processIdentifier())
            err, win = AXUIElementCopyAttributeValue(app_el, kAXFocusedWindowAttribute, None)
            if err != 0 or not win:
                err, wins = AXUIElementCopyAttributeValue(app_el, kAXWindowsAttribute, None)
                win = wins[0] if (err == 0 and wins) else None
            if win:
                err_fs, is_fs = AXUIElementCopyAttributeValue(win, "AXFullScreen", None)
                if err_fs == 0 and is_fs:
                    AXUIElementSetAttributeValue(win, "AXFullScreen", False)
                    time.sleep(0.3)
    except Exception:
        pass
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
    app = resolve_app(name, prefer_running=True)
    if not app:
        say(f"I couldn't find an app called {name}")
        return
    applescript(f'tell application "{esc(app)}" to activate')
    say(f"Switched to {app}")


def act_close_window(target_app_name: str | None = None) -> bool:
    """Close the frontmost visible application window (or target_app_name window).

    Uses macOS Accessibility AXCloseButton on the frontmost visible application
    window, which reliably closes the window even when background daemons or
    notifications hold key state. Falls back to in-process Cmd-W keystroke.
    """
    if DRY_RUN:
        log("DRY-RUN close window")
        say("Closed")
        return True

    target_pid = None
    target_name = None

    if target_app_name:
        resolved = resolve_app(target_app_name, prefer_running=True)
        if resolved:
            try:
                from AppKit import NSWorkspace
                for a in NSWorkspace.sharedWorkspace().runningApplications():
                    if a.localizedName() == resolved:
                        target_pid = a.processIdentifier()
                        target_name = resolved
                        break
            except Exception:
                pass

    if target_pid is None:
        try:
            from AppKit import NSWorkspace
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front:
                target_pid = front.processIdentifier()
                target_name = front.localizedName()
        except Exception:
            pass

    if target_pid is None:
        # Fall back to topmost real application window (layer 0, size >= 150)
        Q = _load_quartz()
        if Q:
            try:
                opts = Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements
                for w in (Q.CGWindowListCopyWindowInfo(opts, Q.kCGNullWindowID) or []):
                    owner = w.get(Q.kCGWindowOwnerName, "")
                    layer = w.get(Q.kCGWindowLayer, -1)
                    bounds = w.get(Q.kCGWindowBounds, {})
                    if layer == 0 and bounds.get("Width", 0) >= 150 and bounds.get("Height", 0) >= 150:
                        if owner not in ("UserNotificationCenter", "Dock", "Window Server", "SystemUIServer", "loginwindow"):
                            target_pid = w.get(Q.kCGWindowOwnerPID)
                            target_name = owner
                            break
            except Exception as e:
                log(f"Quartz window search failed: {e}")

    closed = False
    if target_pid is not None:
        try:
            import ApplicationServices as AX
            ax_app = AX.AXUIElementCreateApplication(target_pid)
            # Try focused window first
            err_f, focused_win = AX.AXUIElementCopyAttributeValue(ax_app, "AXFocusedWindow", None)
            wins_to_try = [focused_win] if (not err_f and focused_win) else []
            err_w, windows = AX.AXUIElementCopyAttributeValue(ax_app, "AXWindows", None)
            if not err_w and windows:
                for w in windows:
                    if w not in wins_to_try:
                        wins_to_try.append(w)
            for win in wins_to_try:
                err_btn, btn = AX.AXUIElementCopyAttributeValue(win, "AXCloseButton", None)
                if not err_btn and btn:
                    res = AX.AXUIElementPerformAction(btn, "AXPress")
                    if res == 0:
                        closed = True
                        log(f"closed window of {target_name} (pid {target_pid}) via AXCloseButton")
                        break
        except Exception as e:
            log(f"AXCloseButton failed: {e}")

    if not closed:
        if target_pid is not None:
            try:
                from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
                for a in NSWorkspace.sharedWorkspace().runningApplications():
                    if a.processIdentifier() == target_pid:
                        a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                        time.sleep(0.08)
                        break
            except Exception:
                pass
        act_keystroke("w", "command down")
        closed = True

    say("Closed")
    return closed


def act_close_all_windows(target_app_name: str | None = None) -> None:
    """Close all open windows of the active application (Option-Command-W or AXCloseButton)."""
    closed_any = False
    try:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
        target_pid = None
        if target_app_name:
            for app in ws.runningApplications():
                if target_app_name.lower() in (app.localizedName() or "").lower():
                    target_pid = app.processIdentifier()
                    break
        if target_pid is None:
            front = ws.frontmostApplication()
            if front:
                target_pid = front.processIdentifier()

        if target_pid is not None:
            import ApplicationServices as AX
            ax_app = AX.AXUIElementCreateApplication(target_pid)
            err, windows = AX.AXUIElementCopyAttributeValue(ax_app, "AXWindows", None)
            if not err and windows:
                for win in windows:
                    err_btn, btn = AX.AXUIElementCopyAttributeValue(win, "AXCloseButton", None)
                    if not err_btn and btn:
                        res = AX.AXUIElementPerformAction(btn, "AXPress")
                        if res == 0:
                            closed_any = True
    except Exception as e:
        log(f"AX close all windows error: {e}")

    if not closed_any:
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


def _native_set_front_window_bounds(pos: tuple[int, int], size: tuple[int, int]) -> bool:
    """Set position and size of the frontmost focused window directly in-process via PyObjC AXUIElement.

    Bypasses /usr/bin/osascript System Events, reducing window snapping latency from ~150ms
    to ~1-2ms, and functioning reliably on non-AppleScript apps (Electron, browsers, terminals).
    Returns True on success, or False to fall back to AppleScript.
    """
    try:
        from AppKit import NSWorkspace, CGPoint, CGSize
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            AXUIElementSetAttributeValue,
            AXValueCreate,
            kAXPositionAttribute,
            kAXSizeAttribute,
            kAXFocusedWindowAttribute,
            kAXWindowsAttribute,
            kAXValueTypeCGPoint,
            kAXValueTypeCGSize,
        )
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if not front:
            return False
        app_el = AXUIElementCreateApplication(front.processIdentifier())
        err, win = AXUIElementCopyAttributeValue(app_el, kAXFocusedWindowAttribute, None)
        if err != 0 or not win:
            err, wins = AXUIElementCopyAttributeValue(app_el, kAXWindowsAttribute, None)
            if err == 0 and wins:
                win = wins[0]
        if not win:
            return False

        new_pos = AXValueCreate(kAXValueTypeCGPoint, CGPoint(pos[0], pos[1]))
        new_size = AXValueCreate(kAXValueTypeCGSize, CGSize(size[0], size[1]))
        err1 = AXUIElementSetAttributeValue(win, kAXPositionAttribute, new_pos)
        err2 = AXUIElementSetAttributeValue(win, kAXSizeAttribute, new_size)
        err3 = AXUIElementSetAttributeValue(win, kAXPositionAttribute, new_pos)
        if err2 != 0 or (err1 != 0 and err3 != 0):
            return False
        return True
    except Exception as e:
        log(f"native window bounds failed ({e}), falling back to AppleScript")
        return False


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
    elif side == "top":
        pos = (x, y)
        size = (w, h // 2)
        label = "Snapped top"
    elif side == "bottom":
        pos = (x, y + h // 2)
        size = (w, h - h // 2)
        label = "Snapped bottom"
    elif side == "center":
        pos = (x + w // 6, y + h // 12)
        size = (2 * w // 3, 5 * h // 6)
        label = "Centered"
    else:
        say("I don't know that position")
        return

    if not _native_set_front_window_bounds(pos, size):
        applescript(
            'tell application "System Events" to tell (first application process whose frontmost is true) to '
            f'tell window 1 to set {{position, size}} to {{{{{pos[0]}, {pos[1]}}}, {{{size[0]}, {size[1]}}}}}'
        )
    say(label)


def act_move_next_display() -> None:
    """Move frontmost window to the next connected display."""
    if _notify_hammerspoon_display_next():
        say("Moved to next display")
        return
    try:
        from AppKit import NSScreen
        screens = NSScreen.screens()
        if len(screens) < 2:
            say("Only one display connected")
            return
        s0, s1 = screens[0].frame(), screens[1].frame()
        dx = int(s1.origin.x - s0.origin.x)
        dy = int(s1.origin.y - s0.origin.y)

        # Attempt native PyObjC move first
        moved = False
        try:
            from AppKit import NSWorkspace, CGPoint
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementCopyAttributeValue,
                AXUIElementSetAttributeValue,
                AXValueCreate,
                AXValueGetValue,
                kAXPositionAttribute,
                kAXFocusedWindowAttribute,
                kAXWindowsAttribute,
                kAXValueTypeCGPoint,
            )
            ws = NSWorkspace.sharedWorkspace()
            front = ws.frontmostApplication()
            if front:
                app_el = AXUIElementCreateApplication(front.processIdentifier())
                err, win = AXUIElementCopyAttributeValue(app_el, kAXFocusedWindowAttribute, None)
                if err != 0 or not win:
                    err, wins = AXUIElementCopyAttributeValue(app_el, kAXWindowsAttribute, None)
                    if err == 0 and wins:
                        win = wins[0]
                if win:
                    err_pos, pos_val = AXUIElementCopyAttributeValue(win, kAXPositionAttribute, None)
                    if err_pos == 0 and pos_val:
                        _, pt = AXValueGetValue(pos_val, kAXValueTypeCGPoint, None)
                        new_pt = AXValueCreate(kAXValueTypeCGPoint, CGPoint(pt.x + dx, pt.y + dy))
                        AXUIElementSetAttributeValue(win, kAXPositionAttribute, new_pt)
                        moved = True
        except Exception as e:
            log(f"native display move failed: {e}")

        if not moved:
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


def _send_key_in_process(key_or_code: str | int, using: str = "") -> bool:
    """Send a keystroke or key code directly in-process via pynput/Quartz.

    Avoids spawning /usr/bin/osascript which fails on macOS with error 1002
    ('osascript is not allowed to send keystrokes') because Accessibility is
    granted to the parent Python process, not subprocess osascript.
    """
    kb = _keyboard()
    if kb is None:
        return False
    try:
        from pynput.keyboard import Key, KeyCode
        mods = []
        u = using.lower()
        if "command" in u or "cmd" in u:
            mods.append(Key.cmd)
        if "shift" in u:
            mods.append(Key.shift)
        if "option" in u or "alt" in u:
            mods.append(Key.alt)
        if "control" in u or "ctrl" in u:
            mods.append(Key.ctrl)

        if isinstance(key_or_code, int):
            target_key = KeyCode.from_vk(key_or_code)
        else:
            if key_or_code in ("\r", "\n"):
                target_key = Key.enter
            elif key_or_code == "\t":
                target_key = Key.tab
            elif key_or_code == " ":
                target_key = Key.space
            elif key_or_code == "\b":
                target_key = Key.backspace
            else:
                target_key = key_or_code

        for m in mods:
            kb.press(m)
        time.sleep(0.01)
        kb.press(target_key)
        time.sleep(0.01)
        kb.release(target_key)
        time.sleep(0.01)
        for m in reversed(mods):
            kb.release(m)
        return True
    except Exception as e:
        log(f"in-process keystroke failed ({e})")
        return False


def act_keystroke(keys: str, using: str = "") -> None:
    if DRY_RUN:
        log(f"DRY-RUN keystroke {keys!r} using {using!r}")
        return
    if _send_key_in_process(keys, using):
        return
    mod = f" using {{{using}}}" if using else ""
    applescript(f'tell application "System Events" to keystroke "{esc(keys)}"{mod}')


def act_key_code(code: int, using: str = "") -> None:
    if DRY_RUN:
        log(f"DRY-RUN key code {code} using {using!r}")
        return
    if _send_key_in_process(code, using):
        return
    mod = f" using {{{using}}}" if using else ""
    applescript(f'tell application "System Events" to key code {code}{mod}')


def act_type_text(text: str) -> None:
    if DRY_RUN:
        log(f"DRY-RUN type {len(text)} chars: {text!r}")
        return
    kb = _keyboard()
    if kb is not None:
        try:
            from pynput.keyboard import Key
            for char in text:
                if char in ("\r", "\n"):
                    kb.press(Key.enter)
                    time.sleep(0.01)
                    kb.release(Key.enter)
                elif char == "\t":
                    kb.press(Key.tab)
                    time.sleep(0.01)
                    kb.release(Key.tab)
                else:
                    kb.type(char)
                time.sleep(0.01)
            log(f"typed {len(text)} chars via pynput")
            return
        except Exception as e:
            log(f"pynput typing failed ({e}), falling back to AppleScript")
    try:
        applescript(f'tell application "System Events" to keystroke "{esc(text)}"')
        log(f"typed {len(text)} chars via AppleScript")
    except Exception as e:
        log(f"AppleScript typing failed: {e}")


def act_spotlight_search(query: str = "") -> None:
    act_key_code(49, "command down")
    if query:
        time.sleep(0.15)
        act_type_text(query)
        say(f"Searching for {query}")
    else:
        say("Spotlight")


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


def act_media_seek(seconds: int = 10, forward: bool = True) -> None:
    # Arrow keystrokes across YouTube, Netflix, QuickTime, VLC (key code 124=right, 123=left)
    steps = max(1, round(seconds / 10))
    code = 124 if forward else 123
    for _ in range(steps):
        act_key_code(code)
    say(f"Skipped {seconds} seconds forward" if forward else f"Rewound {seconds} seconds")


def act_media_subtitles() -> None:
    # 'c' key toggles subtitles/captions on YouTube, Netflix, Disney+, Plex, Hulu, VLC
    act_keystroke("c")
    say("Toggled subtitles")


def act_media_what_did_they_say() -> None:
    # Apple TV signature feature: rewinds 15 seconds and toggles subtitles
    act_key_code(123)
    act_key_code(123)
    act_keystroke("c")
    say("Rewinding with subtitles")


def act_media_next_episode() -> None:
    # Shift+N plays next video/episode on YouTube, Netflix
    act_keystroke("n", "shift down")
    say("Next episode")


def act_airplay_settings() -> None:
    shell(["open", "x-apple.systempreferences:com.apple.AirPlay-Settings.extension"])
    say("Opening AirPlay settings. Turn on AirPlay Receiver to cast from your iPhone or iPad.")


def act_watch_stream(service: str, query: str = "") -> None:
    s = service.lower().replace(" plus", "").replace(" video", "").strip()
    urls = {
        "youtube": "https://www.youtube.com",
        "netflix": "https://www.netflix.com",
        "hulu": "https://www.hulu.com",
        "disney": "https://www.disneyplus.com",
        "max": "https://www.max.com",
        "hbo": "https://www.max.com",
        "prime": "https://www.amazon.com/gp/video/storefront",
    }
    if s == "apple tv":
        act_open_app("TV")
        return
    if s == "youtube" and query:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        shell(["open", url])
        say(f"Searching YouTube for {query}")
        return
    if s in urls:
        shell(["open", urls[s]])
        say(f"Opening {s.title()}")
    else:
        say(f"Opening {service}")



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


def _wifi_device() -> str:
    """Wi-Fi interface from `networksetup -listallhardwareports` (en0 fallback).
    On a Mac mini en0 is Ethernet and Wi-Fi is usually en1."""
    try:
        out = shell(["networksetup", "-listallhardwareports"])
    except Exception:
        out = ""
    port = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip().lower()
        elif line.startswith("Device:") and port in ("wi-fi", "airport"):
            dev = line.split(":", 1)[1].strip()
            if dev:
                return dev
    return "en0"


def act_wifi(on: bool) -> None:
    shell(["networksetup", "-setairportpower", _wifi_device(), "on" if on else "off"])
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
    "airplay": "com.apple.AirPlay-Settings.extension",
    "airplay receiver": "com.apple.AirPlay-Settings.extension",
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


_TIME_UNIT_ALIASES = {
    "s": "second", "sec": "second", "secs": "second", "second": "second", "seconds": "second",
    "m": "minute", "min": "minute", "mins": "minute", "minute": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
}


def _normalize_time_unit(unit, default: str = "minute") -> str:
    """Map 's'/'sec'/'secs'/'seconds' etc. to 'second'/'minute'/'hour'."""
    return _TIME_UNIT_ALIASES.get(str(unit or "").strip().lower(), default)


def act_timer(amount: int, unit: str) -> None:
    unit = _normalize_time_unit(unit)
    seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[unit]

    def ring():
        time.sleep(seconds)
        say("Timer done")
        for _ in range(3):
            shell(["afplay", "/System/Library/Sounds/Glass.aiff"])

    threading.Thread(target=ring, daemon=True).start()
    say(f"Timer set for {amount} {unit}{'s' if amount != 1 else ''}")


def act_calculate(expr: str) -> None:
    expr = expr.strip()
    if len(expr) > 64 or not re.fullmatch(r"[\d\s+\-*/().%^]+", expr):
        say("I can only calculate plain arithmetic")
        return
    # Guard against exponent chains that hang the thread ("9^9^9"). '**' is
    # rejected outright; '^' must be followed by a plain integer <= 100.
    if "**" in expr or expr.count("^") > 1:
        say("Calculation is too large")
        return
    if re.search(r"\^(?!\s*\d)", expr):
        say("Exponent is too large")
        return
    for m in re.finditer(r"\^\s*(\d+)", expr):
        if int(m.group(1)) > 100:
            say("Exponent is too large")
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


def act_tech_radar() -> None:
    """Read the morning technology radar summary aloud."""
    report_file = os.path.expanduser("~/.config/free-voice/radar_report.md")
    if not os.path.exists(report_file):
        say("Running morning tech radar scan")
        try:
            radar_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "tech_radar.py")
            subprocess.run([sys.executable, radar_script], timeout=25, capture_output=True)
        except Exception:
            pass

    if os.path.exists(report_file):
        try:
            with open(report_file, "r") as f:
                content = f.read()
            if "Actionable Upgrade Alerts" in content:
                alerts_section = content.split("Actionable Upgrade Alerts")[-1].strip().split("\n\n")[0]
                if "No action required" in alerts_section or "bleeding edge" in alerts_section:
                    say("Voice, decision routing, and computer control systems are up to date and at the bleeding edge.")
                else:
                    lines = [l.strip("- *⚠️").strip() for l in alerts_section.splitlines() if l.strip().startswith("-")]
                    if lines:
                        say(f"Radar alert: {lines[0]}")
                    else:
                        say("Radar scan complete. No new alerts.")
            else:
                say("Tech radar report is ready in your config folder.")
        except Exception:
            say("Tech radar scan is complete.")
    else:
        say("Unable to access tech radar report.")


def _critic_running(pid_file: str = "/tmp/masterpiece_critic.pid") -> bool:
    """True only if the PID file names a live process that is actually the
    critic (guards against PID reuse). A stale PID file is removed."""
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except Exception:
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=2, check=False)
    except Exception:
        return True  # can't inspect; keep the old os.kill-only behaviour
    if "masterpiece_critic" in out.stdout:
        return True
    try:
        os.remove(pid_file)  # PID was reused by an unrelated process
    except OSError:
        pass
    return False


def act_masterpiece_roast(action: str = "start", theme: str = None) -> None:
    """Launch or trigger Screen Critic overlay to roast user."""
    pid_file = "/tmp/masterpiece_critic.pid"
    cmd_file = "/tmp/masterpiece_critic_cmd.txt"
    critic_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masterpiece_critic.py")
    python_bin = sys.executable

    is_running = _critic_running(pid_file)

    if action == "stop":
        if is_running:
            try:
                with open(cmd_file, "w") as f:
                    f.write("stop")
            except Exception:
                pass
            say("Dismissing the screen critic.")
        else:
            say("The screen critic isn't watching right now.")
        return

    if theme:
        act_set_critic_theme(theme, start_if_needed=True)
        return

    # Start or roast on demand
    if is_running:
        try:
            with open(cmd_file, "w") as f:
                f.write("roast_now")
        except Exception:
            pass
    else:
        try:
            subprocess.Popen([python_bin, critic_script], start_new_session=True)
            log("launched screen critic overlay")
        except Exception as e:
            log(f"failed to launch critic: {e}")
            say("Unable to summon the screen critic right now.")


def act_set_critic_theme(theme_query: str, start_if_needed: bool = False) -> None:
    """Switch active critic theme (couch_duo, wine_girls, theater_critic, byte_orbit, kids_club)."""
    q = theme_query.lower().strip()
    theme_key = "couch_duo"
    msg = "Switching to the Couch Duo, Leo and Cleo."

    if any(k in q for k in ["wine", "girl"]):
        theme_key = "wine_girls"
        msg = "Switching to Wine Night with Chloe and Maya."
    elif any(k in q for k in ["theater", "theatre", "masterpiece", "reginald", "critic"]):
        theme_key = "theater_critic"
        msg = "Summoning Sir Reginald, the Masterpiece Critic."
    elif any(k in q for k in ["robot", "byte", "orbit", "cyber"]):
        theme_key = "byte_orbit"
        msg = "Activating Byte and Orbit."
    elif any(k in q for k in ["kid", "puppy", "barnaby", "toby", "children"]):
        theme_key = "kids_club"
        msg = "Switching to Kids Club with Toby and Barnaby."
    elif any(k in q for k in ["gamer", "cat", "couch", "default"]):
        theme_key = "couch_duo"
        msg = "Switching to the Couch Duo with Leo and Cleo."

    # Update config file
    config_file = os.path.expanduser("~/.config/free-voice/critic_config.json")
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        cfg = {}
        if os.path.exists(config_file):
            with open(config_file) as f:
                cfg = json.load(f)
        cfg["theme"] = theme_key
        with open(config_file, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

    # Signal running overlay if active
    pid_file = "/tmp/masterpiece_critic.pid"
    cmd_file = "/tmp/masterpiece_critic_cmd.txt"
    is_running = _critic_running(pid_file)

    if is_running:
        try:
            with open(cmd_file, "w") as f:
                f.write(f"set_theme:{theme_key}")
        except Exception:
            pass
        say(msg)
    elif start_if_needed:
        say(msg)
        critic_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masterpiece_critic.py")
        subprocess.Popen([sys.executable, critic_script], start_new_session=True)
    else:
        say(f"{msg} It will be ready next time you roast.")


def act_set_critic_pos(pos_query: str) -> None:
    """Move screen critic overlay position (bottom_left, bottom_center, bottom_right)."""
    q = pos_query.lower().strip()
    pos_key = "bottom_left"
    label = "bottom left"

    if "center" in q or "middle" in q:
        pos_key = "bottom_center"
        label = "bottom center"
    elif "right" in q:
        pos_key = "bottom_right"
        label = "bottom right"
    else:
        pos_key = "bottom_left"
        label = "bottom left"

    # Update config file
    config_file = os.path.expanduser("~/.config/free-voice/critic_config.json")
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        cfg = {}
        if os.path.exists(config_file):
            with open(config_file) as f:
                cfg = json.load(f)
        cfg["position"] = pos_key
        with open(config_file, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

    # Signal running overlay
    cmd_file = "/tmp/masterpiece_critic_cmd.txt"
    try:
        with open(cmd_file, "w") as f:
            f.write(f"set_pos:{pos_key}")
    except Exception:
        pass
    say(f"Moved screen critic to the {label}.")



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


def act_pick_voice() -> None:
    """Rotate through voice examples speaking the same sentence, starting with the voice name."""
    is_kokoro = (VOICE_TTS_ENGINE == "kokoro" and _get_kokoro() is not None)
    showcase = KOKORO_SHOWCASE if is_kokoro else MACOS_SHOWCASE

    say("Sampling voices. Say use voice, followed by the name, to select one.", blocking=True)
    for display_name, voice_id in showcase:
        sample_sentence = f"{display_name}. This is what I sound like on your Mac."
        say(sample_sentence, blocking=True, voice=voice_id)
    say("Which voice would you like?", blocking=True)


def act_set_voice(name: str) -> None:
    """Set active voice and persist to ~/.config/free-voice/config.json."""
    global VOICE_KOKORO_VOICE, VOICE_SAY_VOICE
    clean = name.strip().lower().replace("-", "_")

    # Match in Kokoro voices
    found_kokoro = None
    found_display = None
    for key, (disp, vid) in KOKORO_VOICES.items():
        if clean in (key, vid.lower(), disp.lower()):
            found_kokoro = vid
            found_display = disp
            break

    cfg = _load_config()
    if found_kokoro:
        VOICE_KOKORO_VOICE = found_kokoro
        cfg["kokoro_voice"] = found_kokoro
        _save_config(cfg)
        say(f"Voice set to {found_display}.", blocking=True, voice=found_kokoro)
        log(f"Voice changed to Kokoro {found_display} ({found_kokoro})")
        return

    # Fallback to macOS say voice (e.g. Samantha, Alex, Daniel)
    VOICE_SAY_VOICE = name.strip().title()
    cfg["say_voice"] = VOICE_SAY_VOICE
    _save_config(cfg)
    say(f"Voice set to {VOICE_SAY_VOICE}.", blocking=True, voice=VOICE_SAY_VOICE)
    log(f"Voice changed to macOS {VOICE_SAY_VOICE}")


def act_get_voice() -> None:
    """Report current active voice."""
    if VOICE_TTS_ENGINE == "kokoro" and _get_kokoro() is not None:
        disp = VOICE_KOKORO_VOICE
        for k, (d, vid) in KOKORO_VOICES.items():
            if vid == VOICE_KOKORO_VOICE:
                disp = d
                break
        say(f"Current voice is {disp} using Kokoro neural speech.")
    else:
        say(f"Current voice is {VOICE_SAY_VOICE} using macOS speech.")


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
_mouse_cached = None
_keyboard_cached = None


def _mouse():
    """Lazily build and cache a pynput mouse controller; (None, None) when unavailable."""
    global _mouse_cached
    if _mouse_cached is None:
        try:
            from pynput.mouse import Button, Controller
            _mouse_cached = (Controller(), Button)
        except Exception as e:  # noqa: BLE001
            log(f"mouse control unavailable: {e}")
            return None, None
    return _mouse_cached


def _keyboard():
    """Lazily build and cache a pynput keyboard controller; None when unavailable."""
    global _keyboard_cached
    if _keyboard_cached is None:
        try:
            from pynput.keyboard import Controller
            _keyboard_cached = Controller()
        except Exception as e:  # noqa: BLE001
            log(f"keyboard control unavailable: {e}")
            return None
    return _keyboard_cached


def _warp_mouse(x: float, y: float) -> None:
    """Physically warp the hardware cursor on macOS and post a mouse moved event."""
    try:
        import Quartz
        target = (float(x), float(y))
        Quartz.CGWarpMouseCursorPosition(target)
        ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, target, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    except Exception as e:
        log(f"Quartz mouse warp failed: {e}")


def _click_xy(x: int, y: int, name: str) -> bool:
    """Click at (x, y) coordinates; returns True if successful/dry-run."""
    if DRY_RUN:
        log(f"DRY-RUN click at ({x}, {y}) for {name!r}")
        return True
    _warp_mouse(x, y)
    time.sleep(0.04)
    try:
        import Quartz
        target = (float(x), float(y))
        down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, target, Quartz.kCGMouseButtonLeft)
        up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, target, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.04)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        say(f"Clicked {name}")
        return True
    except Exception as e:
        log(f"Quartz click failed ({e}), falling back to pynput")
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


def _screen_dimensions() -> tuple[int, int]:
    try:
        from AppKit import NSScreen
        f = NSScreen.mainScreen().frame()
        return int(f.size.width), int(f.size.height)
    except Exception:
        pass
    return 1920, 1080


def _app_window_center(app_name: str, prefer_bottom: bool = False) -> tuple[int, int] | None:
    """Find the center coordinates of an app window via System Events."""
    cond = """
            set w_bottom to (item 2 of pos) + (item 2 of sz)
            if w_bottom > max_bottom then
                set max_bottom to w_bottom
                set best_cx to ((item 1 of pos) + ((item 1 of sz) / 2)) as integer
                set best_cy to ((item 2 of pos) + ((item 2 of sz) / 2)) as integer
            end if
    """ if prefer_bottom else """
            set best_cx to ((item 1 of pos) + ((item 1 of sz) / 2)) as integer
            set best_cy to ((item 2 of pos) + ((item 2 of sz) / 2)) as integer
            exit repeat
    """
    script = f'''
    tell application "System Events" to tell process "{app_name}"
        if (count of windows) > 0 then
            set best_cx to 0
            set best_cy to 0
            set max_bottom to -99999
            repeat with w in windows
                set pos to position of w
                set sz to size of w
                {cond}
            end repeat
            return (best_cx as text) & "," & (best_cy as text)
        end if
    end tell
    '''
    try:
        res = applescript(script)
        if res and "," in res:
            parts = [int(p.strip()) for p in res.split(",")]
            return parts[0], parts[1]
    except Exception:
        pass
    return None


def act_mouse_to(name: str) -> None:
    """Move the cursor onto a screen area, app window, or named UI element.

    Screen edge/region first, application window second, Accessibility tree
    third, Apple Vision OCR fourth, screenshot VLM as fallback.
    Moves only — it never clicks.
    """
    spoken = name.strip()
    if not spoken:
        say("Move the mouse to what?")
        return

    lower = spoken.lower().strip()
    loc = None

    # 1. Screen positions / edges
    if lower in ("bottom", "bottom of the screen", "bottom edge", "bottom center", "the bottom"):
        w, h = _screen_dimensions()
        loc = (w // 2, h - 50)
    elif lower in ("top", "top of the screen", "top edge", "top center", "the top"):
        w, h = _screen_dimensions()
        loc = (w // 2, 50)
    elif lower in ("center", "middle", "center of the screen", "middle of the screen"):
        w, h = _screen_dimensions()
        loc = (w // 2, h // 2)
    elif lower in ("left", "left side", "left edge"):
        w, h = _screen_dimensions()
        loc = (50, h // 2)
    elif lower in ("right", "right side", "right edge"):
        w, h = _screen_dimensions()
        loc = (w - 50, h // 2)

    # 2. Application window (e.g. "terminal", "bottom terminal", "safari")
    if loc is None:
        prefer_bot = "bottom" in lower or "lower" in lower
        for token in lower.split():
            clean_tok = token.strip(" ,.-")
            matched_app = resolve_app(clean_tok)
            if matched_app:
                loc = _app_window_center(matched_app, prefer_bottom=prefer_bot)
                if loc:
                    break

    # 3. Accessibility tree, OCR, Vision
    if loc is None:
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
    _warp_mouse(x, y)
    mouse.position = (x, y)
    say(f"Mouse is on {spoken}")


def act_close_notifications() -> None:
    """Dismiss macOS Notification Center alert banners in the top right corner."""
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
    orig_pos = mouse.position if mouse else None
    min_x = _screen_dimensions()[0] / 2

    def get_banners():
        banners = []
        try:
            if hasattr(app, "locator"):
                loc_groups = app.locator("group").elements()
                if loc_groups:
                    for g in loc_groups:
                        b = getattr(g, "bounds", None)
                        if b and getattr(b, "width", 0) > 150 and getattr(b, "x", 0) > min_x:
                            banners.append(g)
                    if banners:
                        return banners
        except Exception:
            pass

        try:
            windows = app.windows()
            if not windows:
                return []
            win = windows[0]
            def rec(el):
                b = getattr(el, "bounds", None)
                if b and getattr(b, "width", 0) > 150 and getattr(b, "x", 0) > min_x and getattr(el, "role", "") == "group" and getattr(el, "name", None):
                    banners.append(el)
                for c in el.children():
                    rec(c)
            rec(win)
            return banners
        except Exception:
            return []

    total_closed = 0
    for _ in range(10):
        banners = get_banners()
        if not banners:
            break
        clicked_any = False
        for b_el in banners:
            b = b_el.bounds
            if not b:
                continue
            _warp_mouse(b.x + 10, b.y + 10)
            if mouse:
                mouse.position = (b.x + 10, b.y + 10)
            time.sleep(0.12)
            try:
                for c in b_el.children():
                    if c.role == "button" and c.name in ("Close", "Clear All", "Clear", "Dismiss"):
                        c.press()
                        total_closed += 1
                        clicked_any = True
                        time.sleep(0.25)
                        break
            except Exception:
                pass
            if clicked_any:
                break
        if not clicked_any:
            break

    if orig_pos and mouse:
        try:
            mouse.position = orig_pos
            _warp_mouse(orig_pos[0], orig_pos[1])
        except Exception:
            pass

    if total_closed:
        say(f"Closed {total_closed} alert{'s' if total_closed != 1 else ''}")
    else:
        say("No alerts to close")


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
    pos = getattr(mouse, "position", None)
    if isinstance(pos, (tuple, list)) and len(pos) >= 2:
        try:
            cur_x, cur_y = int(pos[0]), int(pos[1])
            target_x = max(0, int(cur_x + dx * dist))
            target_y = max(0, int(cur_y + dy * dist))
            _warp_mouse(target_x, target_y)
        except Exception:
            pass
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

_SCREENSHOT_DIR = os.path.join(HOME, ".free-voice", "tmp")
_SCREENSHOT_PATH = os.path.join(_SCREENSHOT_DIR, "free-voice-screen.png")
_shot_ts = 0.0
SCREENSHOT_TTL = 8.0  # "what's on my screen" -> "click the X" reuses the shot


def _ensure_screenshot_dir() -> str:
    try:
        os.makedirs(_SCREENSHOT_DIR, mode=0o700, exist_ok=True)
        os.chmod(_SCREENSHOT_DIR, 0o700)
    except Exception:
        pass
    return _SCREENSHOT_PATH


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
        _ensure_screenshot_dir()
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
    return _click_xy(x, y, name)


# ---------------------------------------------------------------- confirmation

def confirm_spoken(action_desc: str, audio_fn) -> bool:
    say(f"Say 'yes' to confirm: {action_desc}", blocking=True)
    audio = audio_fn(4.0)
    text = transcribe(audio).lower().strip()
    log(f"confirmation heard: {text!r}")
    # Immediate rejection if negative words are present
    if re.search(r"\b(no|don't|dont|never|stop|cancel|negative|nope)\b", text):
        log("confirmation rejected by negative word")
        return False
    # Require affirmative confirmation using word boundaries
    return bool(re.search(r"\b(yes|confirm|confirmed|do it|proceed|affirmative|yep|yeah)\b", text))


def describe_action_for_prompt(action: str, params: dict) -> str:
    """Format action and params into a concise, natural spoken confirmation phrase."""
    if action == "open_app":
        return f"open {params.get('app', 'app')}"
    elif action == "quit_app":
        return f"quit {params.get('app', 'app')}"
    elif action == "switch_app":
        return f"switch to {params.get('app', 'app')}"
    elif action == "set_volume":
        if "level" in params:
            return f"set volume to {params['level']}"
        return f"turn volume {params.get('direction', 'down')}"
    elif action == "media":
        return f"{params.get('op', 'media action')}"
    elif action == "timer":
        return f"set timer for {params.get('minutes', 5)} minutes"
    elif action == "web_search":
        return f"search the web for {params.get('query', '')}"
    return action.replace("_", " ")



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


def act_macro_add(trigger: str, commands: str, allow_shell: bool = False) -> None:
    """'When I say party mode, set volume to 80 and play some jazz' — save shortcut."""
    clean_trigger = trigger.strip().strip("'\"").lower()
    commands = re.sub(r"^(?:do|run)\s+", "", commands.strip(), flags=re.IGNORECASE)
    parts = [p.strip() for p in
             re.split(r"\s+then\s+|\s+and\s+|,\s*", commands, flags=re.IGNORECASE)
             if p.strip()]
    # Security guard: voice-created macros cannot execute raw shell/bash commands without explicit programmatic permission
    if not allow_shell:
        for p in parts:
            if p.startswith(("shell ", "bash ", "run script ")) or (p.startswith("run ") and "/" in p):
                say("Voice shortcuts cannot execute shell scripts for security reasons")
                log(f"blocked shell command in voice macro: {p!r}")
                return
    macros = _load_macros()
    macros[clean_trigger] = parts
    _save_macros(macros)
    say(f"Shortcut {clean_trigger} saved with {len(parts)} "
        f"command{'s' if len(parts) != 1 else ''}")




def act_macro_list() -> None:
    macros = _load_macros()
    if not macros:
        say("You have no custom shortcuts yet. Say 'when I say', a phrase, then the commands, to make one.")
        return
    say(f"You have {len(macros)} shortcut{'s' if len(macros) != 1 else ''}: "
        + ", ".join(sorted(macros)))


def act_macro_delete(trigger: str) -> None:
    macros = _load_macros()
    key = trigger.strip().strip("'\"").lower()
    if key in macros:
        del macros[key]
        _save_macros(macros)
        say(f"Deleted shortcut {key}")
    else:
        say(f"No shortcut called {key}")


_MACRO_MAX_DEPTH = 5
_macro_tls = threading.local()


def run_macro(text: str, confirm_audio_fn=None,
              allow_destructive: bool = False, quiet_miss: bool = False) -> bool:
    """Recursion guard around _run_macro_unguarded: a macro that (directly or
    via another macro) triggers itself, or nests too deep, is stopped."""
    key = text.strip().lower()
    stack = _macro_tls.__dict__.setdefault("stack", [])
    if key in stack or len(stack) >= _MACRO_MAX_DEPTH:
        if not _load_macros().get(key):
            return False
        log(f"shortcut/macro {key!r} skipped: recursion ({' -> '.join(stack + [key])})")
        say("That shortcut calls itself, so I stopped it")
        return True
    stack.append(key)
    try:
        return _run_macro_unguarded(text, confirm_audio_fn=confirm_audio_fn,
                                    allow_destructive=allow_destructive,
                                    quiet_miss=quiet_miss)
    finally:
        stack.pop()


def _run_macro_unguarded(text: str, confirm_audio_fn=None,
                         allow_destructive: bool = False, quiet_miss: bool = False) -> bool:
    """Run a user shortcut/macro whose trigger matches the utterance."""
    parts = _load_macros().get(text.strip().lower())
    if not parts:
        return False
    log(f"shortcut/macro hit: {text.strip()!r} -> {parts}")
    for p in parts:
        if p.startswith(("shell ", "bash ", "run script ")) or (p.startswith("run ") and "/" in p):
            cmd_str = re.sub(r"^(?:shell|bash|run script|run)\s+", "", p).strip()
            shell(["/bin/bash", "-c", cmd_str])
            log(f"custom macro shell executed: {cmd_str}")
        else:
            handle_command(p, confirm_audio_fn=confirm_audio_fn,
                           allow_destructive=allow_destructive,
                           quiet_miss=quiet_miss)
        time.sleep(0.3)
    say("Shortcut done")
    return True


_CUSTOM_HANDLERS: dict = {}


def register_custom_command(pattern: str, handler, partial_ok: bool = False) -> None:
    """Allow user extensions to register custom voice patterns and handlers."""
    name = f"custom_ext_{len(_PATTERNS)}"
    _p(pattern, name, partial_ok)
    _CUSTOM_HANDLERS[name] = handler


def load_custom_extensions() -> None:
    """Load user extensions from ~/.config/free-voice/extensions.py if present."""
    ext_path = os.path.join(HOME, ".config", "free-voice", "extensions.py")
    if not os.path.exists(ext_path):
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("user_extensions", ext_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "register"):
                mod.register(register_custom_command)
            log("loaded custom user extensions from ~/.config/free-voice/extensions.py")
    except Exception as e:
        log(f"warning: failed to load user extensions ({e})")


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
    art = "\n".join(lines) + "\n"
    out_txt = os.path.join(tempfile.gettempdir(), "ascii-art.txt")
    with open(out_txt, "w") as f:
        f.write(art)

    out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
    escaped = html.escape(art)
    html_content = (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>ASCII Art</title>\n"
        "<style>\n"
        "  body {\n"
        "    margin: 0;\n"
        "    padding: 24px;\n"
        "    background-color: #0d1117;\n"
        "    color: #c9d1d9;\n"
        "    display: flex;\n"
        "    justify-content: center;\n"
        "    align-items: center;\n"
        "    min-height: 100vh;\n"
        "    box-sizing: border-box;\n"
        "  }\n"
        "  pre {\n"
        "    font-family: ui-monospace, Menlo, Consolas, monospace;\n"
        "    font-size: 8px;\n"
        "    line-height: 1;\n"
        "    white-space: pre;\n"
        "    margin: 0;\n"
        "    padding: 16px;\n"
        "  }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"<pre>{escaped}</pre>\n"
        "</body>\n"
        "</html>\n"
    )
    with open(out_html, "w") as f:
        f.write(html_content)
    shell(["open", out_html])
    say("Here's your screen as ASCII art")


# --- ASCII & SVG drawing ------------------------------------------------

_CANONICAL_ASCII = {
    "flower": (
        "     _(_)_\n"
        "   (_)@(_)\n"
        "     (_)\n"
        "      |\n"
        "    \\ | /\n"
        "     \\|/\n"
        "      |"
    ),
    "rose": (
        "          .-.\n"
        "         /   \\\n"
        "        |  @  |\n"
        "         \\   /\n"
        "     .---.`-'.---.\n"
        "    /   /  |  \\   \\\n"
        "   |   |   |   |   |\n"
        "    \\   \\  |  /   /\n"
        "     `---`-.-'---'\n"
        "           |\n"
        "        \\--|--/\n"
        "         \\ | /\n"
        "          \\|/\n"
        "           |\n"
        "          /|\n"
        "         / |"
    ),
    "cat": (
        "       /\\_/\\\n"
        "      ( o.o )\n"
        "     ==_ \" _==\n"
        "       /   \\\n"
        "      /     \\\n"
        "     |       |\n"
        "    /|  ___  |\\\n"
        "   ( | |   | | )\n"
        "  (_(_)|   |_)_)"
    ),
    "dog": (
        "         __\n"
        "        /  \\\n"
        "       / .. \\\n"
        "      (_\\  /_)\n"
        "       / '' \\\n"
        "      ( /\"\"\\ )\n"
        "       `\\  /'\n"
        "         `'"
    ),
    "heart": (
        "             .---.     .---.\n"
        "           .'     '. .'     '.\n"
        "          /         V         \\\n"
        "         |   .:::.     .:::.   |\n"
        "         |  :::::::. :::::::   |\n"
        "          \\  ':::::::::::::'  /\n"
        "           '.  ':::::::::'  .'\n"
        "             '.  ':::::'  .'\n"
        "               '.  '::' .'\n"
        "                 '.   .'\n"
        "                   '.'"
    ),
    "tree": (
        "          /\\\n"
        "         /  \\\n"
        "        / /\\ \\\n"
        "       / /  \\ \\\n"
        "      / /____\\ \\\n"
        "          ||\n"
        "          ||"
    ),
    "skull": (
        "         .---.\n"
        "        /     \\\n"
        "       | () () |\n"
        "        \\  ^  /\n"
        "         |||||\n"
        "         |||||"
    ),
    "coffee": (
        "          ( (\n"
        "           ) )\n"
        "        .____.\n"
        "        |    |]\n"
        "        \\____/\n"
        "       `------'"
    ),
    "butterfly": (
        "      \\       /\n"
        "       \\  /\\ /\n"
        "      .-'   '-.\n"
        "    .'  .--.   '.\n"
        "   /   (    )    \\\n"
        "  |     `--'      |\n"
        "   \\             /\n"
        "    '.   .---. .'\n"
        "      '(     )'\n"
        "        `---'"
    ),
    "star": (
        "           *\n"
        "          / \\\n"
        "     *---'   '---*\n"
        "      \\         /\n"
        "       >   *   <\n"
        "      /         \\\n"
        "     *---.   .---*\n"
        "          \\ /\n"
        "           *"
    ),
    "sword": (
        "           /| ________________\n"
        "     O|===|* >________________>\n"
        "           \\|"
    ),
    "apple": (
        "            .:'\n"
        "         __ :'__\n"
        "      .'`__`-'__``.\n"
        "     :__________.-'\n"
        "     :_________:\n"
        "      :_________`-;\n"
        "       `.__.-.__.'"
    ),
    "penguin": (
        "       .-.\n"
        "      |o_o |\n"
        "      |:_/ |\n"
        "     //   \\ \\\n"
        "    (|     | )\n"
        "   /'\\_   _/`\\\n"
        "   \\___)=(___/"
    ),
}

_CANONICAL_ASCII["hearts"] = _CANONICAL_ASCII["heart"]
_CANONICAL_ASCII["love"] = _CANONICAL_ASCII["heart"]
_CANONICAL_ASCII["cats"] = _CANONICAL_ASCII["cat"]
_CANONICAL_ASCII["kitten"] = _CANONICAL_ASCII["cat"]
_CANONICAL_ASCII["kitty"] = _CANONICAL_ASCII["cat"]
_CANONICAL_ASCII["dogs"] = _CANONICAL_ASCII["dog"]
_CANONICAL_ASCII["puppy"] = _CANONICAL_ASCII["dog"]
_CANONICAL_ASCII["flowers"] = _CANONICAL_ASCII["flower"]
_CANONICAL_ASCII["roses"] = _CANONICAL_ASCII["rose"]
_CANONICAL_ASCII["mac"] = _CANONICAL_ASCII["apple"]
_CANONICAL_ASCII["cup of coffee"] = _CANONICAL_ASCII["coffee"]


def _llm_text(prompt: str, max_tokens: int = 1024, system: str | None = None) -> str | None:
    """Raw text from a local model (fast qwen2.5 or vision qwen3), else Gemini."""
    if system is None:
        system = ("You are an SVG generator. Reply with ONLY valid raw SVG code "
                  "starting with <svg and ending with </svg>. "
                  "No explanations, no conversational text, no markdown fences.")
    # Try fast local instruction model first if present (e.g. qwen2.5:1.5b generates SVGs in 3s without thinking trace),
    # otherwise default to configured OLLAMA_MODEL
    local_candidates = []
    if OLLAMA_DRAW_MODEL and OLLAMA_DRAW_MODEL != OLLAMA_MODEL:
        local_candidates.append(OLLAMA_DRAW_MODEL)
    local_candidates.append(OLLAMA_MODEL)

    for mod in local_candidates:
        try:
            body = {
                "model": mod, "keep_alive": "60m", "think": False,
                "options": {"temperature": 0.5, "repeat_penalty": 1.15, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                "stream": False,
            }
            req = urllib.request.Request(
                OLLAMA_HOST + "/api/chat", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.load(r)
            msg = data.get("message", {})
            content = msg.get("content", "").strip()
            if content:
                return content
            thinking = msg.get("thinking", "").strip()
            if thinking:
                return thinking
        except Exception as e:  # noqa: BLE001
            log(f"draw: local model {mod} failed ({e})")

    if GEMINI_API_KEY:
        try:
            body = {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens,
                                     "temperature": 0.7},
            }
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{GEMINI_MODEL}:generateContent")
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": GEMINI_API_KEY})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # noqa: BLE001
            log(f"draw: gemini fallback failed ({e})")
    return None


def act_draw_ascii(subject: str) -> None:
    """'draw ascii flower' — ASCII art rendered and opened in the browser."""
    subject = subject.strip()
    clean_subj = re.sub(r"^(a|an|the)\s+", "", subject, flags=re.I).strip()
    clean_subj = re.sub(r"^(?:picture|image|drawing)\s+of\s+", "", clean_subj, flags=re.I).strip()
    clean_subj = re.sub(r"^(a|an|the)\s+", "", clean_subj, flags=re.I).strip()
    clean_subj = re.sub(r"^ascii\s+", "", clean_subj, flags=re.I).strip() or clean_subj
    say(f"Drawing ASCII {clean_subj}")

    key = clean_subj.lower()
    art = _CANONICAL_ASCII.get(key)
    if not art:
        for k, v in _CANONICAL_ASCII.items():
            if k in key or key in k:
                art = v
                break
    if not art:
        art = _llm_text(
            f"Generate recognizable, high quality, symmetrical ASCII art of {clean_subj}.\n"
            f"Use standard monospace characters (@, #, *, +, =, -, :, ., |, /, \\).\n"
            f"Keep it compact (under 40 cols wide, 10-18 lines tall).\n"
            f"Ensure lines are balanced and well-aligned in monospace.\n"
            f"Output ONLY the ASCII art inside ``` code fences.",
            max_tokens=512,
            system="You are a master ASCII artist. Reply with ONLY raw ASCII art inside code fences, with zero conversational text.",
        )
    if not art:
        say("I couldn't draw that in ASCII right now")
        return
    cleaned = art.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if not cleaned:
        say("The ASCII drawing didn't come out right")
        return

    out_txt = os.path.join(tempfile.gettempdir(), "ascii-art.txt")
    with open(out_txt, "w") as f:
        f.write(cleaned + "\n")

    out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
    escaped = html.escape(cleaned)

    # Dynamic styling theme based on subject
    s_lower = clean_subj.lower()
    if any(w in s_lower for w in ("heart", "love", "rose", "valentine")):
        accent = "#ff4d6d"
        glow = "rgba(255, 77, 109, 0.45)"
    elif any(w in s_lower for w in ("matrix", "skull", "cyber", "hacker")):
        accent = "#39ff14"
        glow = "rgba(57, 255, 20, 0.45)"
    elif any(w in s_lower for w in ("star", "sun", "gold", "flower", "coffee")):
        accent = "#ffd166"
        glow = "rgba(255, 209, 102, 0.45)"
    else:
        accent = "#58a6ff"
        glow = "rgba(88, 166, 255, 0.35)"

    html_content = (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>ASCII {html.escape(clean_subj)}</title>\n"
        "<style>\n"
        f"  :root {{\n"
        f"    --accent: {accent};\n"
        f"    --glow: {glow};\n"
        f"  }}\n"
        "  body {\n"
        "    margin: 0;\n"
        "    padding: 32px 16px;\n"
        "    background: radial-gradient(circle at 50% 20%, #161b22 0%, #090d13 100%);\n"
        "    color: #e6edf3;\n"
        '    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;\n'
        "    display: flex;\n"
        "    flex-direction: column;\n"
        "    justify-content: center;\n"
        "    align-items: center;\n"
        "    min-height: 100vh;\n"
        "    box-sizing: border-box;\n"
        "  }\n"
        "  .card {\n"
        "    background: #0d1117;\n"
        "    border: 1px solid #30363d;\n"
        "    border-radius: 16px;\n"
        "    padding: 36px 48px;\n"
        "    box-shadow: 0 20px 50px rgba(0,0,0,0.8), 0 0 35px var(--glow);\n"
        "    display: flex;\n"
        "    flex-direction: column;\n"
        "    align-items: center;\n"
        "    max-width: 90vw;\n"
        "  }\n"
        "  .badge {\n"
        "    font-size: 13px;\n"
        "    font-weight: 600;\n"
        "    text-transform: uppercase;\n"
        "    letter-spacing: 1.5px;\n"
        "    color: var(--accent);\n"
        "    margin-bottom: 20px;\n"
        "    padding: 5px 14px;\n"
        "    background: rgba(255, 255, 255, 0.04);\n"
        "    border: 1px solid var(--accent);\n"
        "    border-radius: 20px;\n"
        "  }\n"
        "  pre {\n"
        '    font-family: ui-monospace, "SF Mono", Menlo, Consolas, "Courier New", monospace;\n'
        "    font-size: 18px;\n"
        "    line-height: 1.18;\n"
        "    letter-spacing: 0.5px;\n"
        "    white-space: pre;\n"
        "    color: #f0f6fc;\n"
        "    text-shadow: 0 0 12px var(--glow);\n"
        "    margin: 0 0 24px 0;\n"
        "    padding: 16px;\n"
        "    user-select: all;\n"
        "  }\n"
        "  .btn {\n"
        "    background: #21262d;\n"
        "    color: #c9d1d9;\n"
        "    border: 1px solid #30363d;\n"
        "    padding: 8px 18px;\n"
        "    border-radius: 8px;\n"
        "    font-size: 13px;\n"
        "    font-weight: 500;\n"
        "    cursor: pointer;\n"
        "    transition: all 0.2s ease;\n"
        "  }\n"
        "  .btn:hover {\n"
        "    background: #30363d;\n"
        "    color: #fff;\n"
        "    border-color: #8b949e;\n"
        "  }\n"
        "  .hint {\n"
        "    font-size: 12px;\n"
        "    color: #6e7681;\n"
        "    margin-top: 14px;\n"
        "  }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        '  <div class="card">\n'
        f'    <div class="badge">ASCII {html.escape(clean_subj)}</div>\n'
        f'    <pre>{escaped}</pre>\n'
        '    <button class="btn" onclick="navigator.clipboard.writeText(document.querySelector(\'pre\').innerText); this.innerText=\'Copied!\'; setTimeout(()=>this.innerText=\'Copy ASCII\', 1500)">Copy ASCII</button>\n'
        '    <div class="hint">Free Mac Voice • Press ⌘W to close</div>\n'
        "  </div>\n"
        "</body>\n"
        "</html>\n"
    )
    with open(out_html, "w") as f:
        f.write(html_content)

    shell(["open", out_html])
    say(f"Here's your ASCII {clean_subj}")


def act_draw_svg(subject: str) -> None:
    """'draw a cat' — LLM-generated SVG opened in the browser."""
    subject = subject.strip()
    say(f"Drawing {subject}")
    clean_subj = re.sub(r"^(a|an|the)\s+", "", subject, flags=re.I).strip() or subject
    svg = _llm_text(
        f"Generate a clean, flat SVG drawing of {clean_subj}. "
        f"Use a 400x400 viewBox with simple shapes and colors. "
        f"Output ONLY raw <svg> code immediately starting with <svg and ending with </svg>.")
    if not svg:
        say("I couldn't draw that right now")
        return
    m = re.search(r"<svg.*?</svg>", svg, re.DOTALL | re.IGNORECASE)
    if not m:
        say("The drawing didn't come out right")
        return
    svg_code = m.group(0)
    if 'xmlns="http://www.w3.org/2000/svg"' not in svg_code and "xmlns='http://www.w3.org/2000/svg'" not in svg_code:
        svg_code = re.sub(r"<svg\b", '<svg xmlns="http://www.w3.org/2000/svg"', svg_code, count=1, flags=re.I)
    out_svg = os.path.join(tempfile.gettempdir(), "drawing.svg")
    with open(out_svg, "w") as f:
        f.write(svg_code)

    shell(["open", out_svg])
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


# --- Samsung TV control (SmartThings cloud API) ---------------------------
_TV_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "samsung_tv.py")


def _tv_configured() -> bool:
    return bool(os.environ.get("SAMSUNG_ST_TOKEN", "").strip()
                and os.environ.get("SAMSUNG_TV_DEVICE_ID", "").strip())


def act_tv_input(alias: str, spoken_name: str) -> None:
    """'switch to computer' — set the Samsung TV input via samsung_tv.py."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "set-input", alias])
    except Exception as e:  # noqa: BLE001
        log(f"TV set-input failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say(f"Switching to {spoken_name}")


def act_tv_power(on: bool) -> None:
    """'turn on/off the tv' — power the Samsung TV via samsung_tv.py."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "power", "on" if on else "off"])
    except Exception as e:  # noqa: BLE001
        log(f"TV power failed: {e}")
        err = str(e).lower()
        if "art mode" in err and "did not go" in err:
            say("The TV didn't switch to art mode")
        elif "art mode" in err:
            say("The TV is stuck on the art screen")
        elif "fully off" in err:
            say("The TV is fully off and won't wake over Wi-Fi. Use the remote")
        else:
            say("I couldn't reach the Samsung TV")
        return
    say(f"Turning the TV {'on' if on else 'off'}")


def act_tv_volume_set(level: int) -> None:
    """'set tv volume to 25' — set the Samsung TV volume."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "set-volume", str(level)])
    except Exception as e:  # noqa: BLE001
        log(f"TV set-volume failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say(f"TV volume {level}")


def act_tv_volume_delta(delta: int) -> None:
    """'tv volume up' / 'tv volume down' — adjust Samsung TV volume."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        cmd = "volume-up" if delta > 0 else "volume-down"
        shell([sys.executable, _TV_SCRIPT, cmd, str(abs(delta))])
    except Exception as e:  # noqa: BLE001
        log(f"TV volume-delta failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say(f"TV volume {'up' if delta > 0 else 'down'}")


def act_tv_mute(mute: bool) -> None:
    """'mute tv' / 'unmute tv' — toggle mute on the Samsung TV."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "mute" if mute else "unmute"])
    except Exception as e:  # noqa: BLE001
        log(f"TV mute failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say(f"TV {'muted' if mute else 'unmuted'}")


def act_tv_media(action: str) -> None:
    """'pause tv' / 'play tv' — control media playback on the Samsung TV."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "media", action])
    except Exception as e:  # noqa: BLE001
        log(f"TV media failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say(f"TV {action}")


def act_tv_art() -> None:
    """'art mode' — put Samsung The Frame TV into Art/Ambient mode."""
    if not _tv_configured():
        say("Samsung TV isn't set up yet — see samsung_tv.py for the one-time setup")
        return
    try:
        shell([sys.executable, _TV_SCRIPT, "art"])
    except Exception as e:  # noqa: BLE001
        log(f"TV art mode failed: {e}")
        say("I couldn't reach the Samsung TV")
        return
    say("TV art mode")


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
# --- Masterpiece Screen Theater / Critic Roaster (BEFORE generic open/start/watch)
_p(r"^(?:please )?roast (?:me|us|my screen|this)(?: please)?$", "roast_me", True)
_p(r"^(?:please )?watch (?:the (?:show|game|movie)|tv|me play(?: games?)?)(?: with (?:me|us))?$", "roast_me", True)
_p(r"^(?:please )?roast (?:me|us) with (wine girls?|wine night|theater critic|masterpiece(?: critic)?|robots?|byte and orbit|kids?(?: club)?|gamer(?: and cat)?|couch duo)(?: please)?$", "roast_with_theme", True)
_p(r"^(?:please )?(?:switch|change)(?: the)? critic(?: theme)? to (wine girls?|wine night|theater critic|masterpiece(?: critic)?|robots?|byte and orbit|kids?(?: club)?|gamer(?: and cat)?|couch duo|default)(?: please)?$", "roast_theme", True)
_p(r"^(?:please )?move(?: the)? critic to (?:the )?(left|center|right|bottom left|bottom center|bottom right)(?: please)?$", "roast_pos", True)
_p(r"^(?:start |launch )?(?:the )?(?:mystery science theater|mst3k|masterpiece )?(?:critic|roaster|theater critic|roasting)$", "roast_me", True)
_p(r"^(?:mystery science theater|mst3k)$", "roast_me", True)
_p(r"^(?:stop|dismiss|close|exit|end) (?:roasting|roast|(?:the )?critic|(?:the )?show|roaster|mst3k)$", "roast_stop", True)
# --- Samsung TV (BEFORE generic "switch to X" app pattern below)
_p(r"^(?:switch|change)\s+to\s+(?:the\s+)?(computer|mac|pc)$", "tv_computer", True)
_p(r"^(?:turn|power)\s+on(?:\s+the)?\s+(?:computer|mac|pc)$", "tv_computer", True)
_p(r"^(?:turn|power)(?:\s+the)?\s+(?:computer|mac|pc)\s+on$", "tv_computer", True)
_p(r"^(?:computer|mac|pc)\s+on$", "tv_computer", True)
_p(r"^(?:switch|change)\s+to\s+(?:the\s+)?(?:samsung\s+)?(?:tv|television)$", "tv_tv", True)
_p(r"^(?:samsung\s+)?home$", "tv_tv", True)
_p(r"^(switch|change)( the)? (input|source)( to)? (.+)$", "tv_input")
_p(r"^(?:turn|power)\s+(on|off)(?:\s+the)?\s+(?:samsung\s+)?(?:tv|television)$", "tv_power", True)
_p(r"^(?:turn|power)(?:\s+the)?\s+(?:samsung\s+)?(?:tv|television)\s+(on|off)$", "tv_power", True)
_p(r"^(?:samsung\s+)?(?:tv|television)\s+(on|off)$", "tv_power", True)
_p(r"^set( the)? tv volume to (\d+)$", "tv_vol_set")
_p(r"^(?:turn\s+)?(?:the\s+)?tv\s+volume\s+(up|down)(\s+by\s+\d+)?$", "tv_vol_delta", True)
_p(r"^turn\s+(up|down)\s+(?:the\s+)?tv\s+volume(\s+by\s+\d+)?$", "tv_vol_delta", True)
_p(r"^mute( the)? tv$", "tv_mute", True)
_p(r"^unmute( the)? tv$", "tv_unmute", True)
_p(r"^(pause|play|stop)( the)? tv$", "tv_media", True)
_p(r"^(?:tv |the tv )?(?:art mode|ambient mode|picture mode|screensaver)$", "tv_art", True)
_p(r"^turn on (?:the )?(?:tv )?(?:art mode|ambient mode|screensaver)$", "tv_art", True)
# --- apps & tabs (specific "open tab" / "open X settings" / "open trash" BEFORE generic open)
_p(r"^(new|open)( a)? tab$", "new_tab", True)
_p(r"^close( the)? tab$", "close_tab", True)
_p(r"^(reopen|undo close)( the)? tab$", "reopen_tab", True)
_p(r"^(next tab|tab forward)$", "next_tab", True)
_p(r"^(previous tab|prev tab|tab back)$", "prev_tab", True)
# --- airplay & screen mirroring
_p(r"^(?:open |enable |start )?airplay(?: settings| receiver)?$", "airplay_settings", True)
_p(r"^(?:connect |cast with )?airplay$", "airplay_settings", True)
_p(r"^screen mirroring$", "airplay_settings", True)
_p(r"^open (.+) settings$", "settings", True)
_p(r"^open trash$", "open_trash", True)
# --- streaming services (10-foot couch TV mode)
_p(r"^(?:watch|stream) (youtube|netflix|hulu|disney(?: plus)?|max|hbo|prime(?: video)?|apple tv)$", "watch_stream", True)
_p(r"^(?:open|launch) (youtube|netflix|hulu|disney(?: plus)?|max|hbo|prime(?: video)?)$", "watch_stream", True)
_p(r"^(?:search|find on) youtube (?:for )?(.+)$", "search_youtube")
_p(r"^(open|opens|launch|start) the (.+?) (app|application)$", "open_app", True)
_p(r"^(open|opens|launch|start) (.+)$", "open_app", True)
_p(r"^(next app|app forward)$", "next_app", True)
_p(r"^(previous app|prev app|app back)$", "prev_app", True)
_p(r"^(switch to|focus|bring up) (.+)$", "switch_app", True)
# --- windows / tabs / quit / hide / notifications
_p(r"^(?:close|clear|dismiss)(?: all)?(?: the)? (?:apple )?(?:notifications?|alerts?)(?: in (?:the )?(?:top right|corner)(?: corner)?)?$", "close_notifications", True)
_p(r"^(?:close|clear|dismiss) (?:all )?(?:notifications?|alerts?)$", "close_notifications", True)
_p(r"^(close all( the)? windows?|close every window|close all)$", "close_all_windows", True)
_p(r"^(close( the| this| current| active)? windows?|close this window|close this)$", "close_window", True)
_p(r"^close (the )?(.+?) windows?$", "close_window_named", True)
_p(r"^close$", "close_window", True)
_p(r"^next window$", "next_window", True)
_p(r"^show all windows$", "show_all_windows", True)
_p(r"^show desktop$", "show_desktop", True)
_p(r"^hide everything else$", "hide_others", True)
_p(r"^kill (.+)$", "kill_app")
_p(r"^(quit|close)( the)? (app |application )?(.+)$", "quit_app", True)
_p(r"^(minimize|minimise)( the)? (.+?)( window| app)?$", "minimize_app", True)
_p(r"^(minimize|minimise)( the window)?$", "minimize", True)
_p(r"^(fullscreen|full screen|make it full screen|toggle full screen|enter full screen|exit full screen|theater mode|theatre mode)$", "fullscreen", True)
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
_p(r"^(snap|tile)(?: (?:it|(?:the |this )?window))?(?: to)?(?: the)? left(?: half)?(?: of(?: my)? screen)?$", "snap_left", True)
_p(r"^(snap|tile)(?: (?:it|(?:the |this )?window))?(?: to)?(?: the)? right(?: half)?(?: of(?: my)? screen)?$", "snap_right", True)
_p(r"^(snap|tile)(?: (?:it|(?:the |this )?window))?(?: to)?(?: the)? top(?: half)?(?: of(?: my)? screen)?$", "snap_top", True)
_p(r"^(snap|tile)(?: (?:it|(?:the |this )?window))?(?: to)?(?: the)? bottom(?: half)?(?: of(?: my)? screen)?$", "snap_bottom", True)
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
# --- web & search (free text: final-only)
_p(r"^search mac$", "search_mac", True)
_p(r"^(?:find|search(?: for)?)(?: the)? files?\s+(.+)$", "find_file")
_p(r"^(?:spotlight(?: search)?|search spotlight(?: for)?)\s+(.+)$", "find_file")
_p(r"^find my(?: (?:iphone|phone|ipad|mac|device|devices|watch|apple watch|airpods|tags?|airtags?|keys?|wallet|items?|friends?))?$", "find_my", True)
_p(r"^(search|google|look up|find(?: me)?|look for)(?: the web)? for (.+)$", "web_search")
_p(r"^(search|google|look up|find(?: me)?|look for) (.+)$", "web_search")
_p(r"^(go to|visit|open website) ([a-z0-9][a-z0-9.\-]*\.[a-z]{2,}.*)$", "open_url")
# --- volume / brightness / media
_p(r"^(?:turn\s+)?(?:the\s+)?(volume|sound)\s+up$", "vol_up", True)
_p(r"^turn\s+up\s+(?:the\s+)?(volume|sound)$", "vol_up", True)
_p(r"^(?:turn\s+)?(?:the\s+)?(volume|sound)\s+down$", "vol_down", True)
_p(r"^turn\s+down\s+(?:the\s+)?(volume|sound)$", "vol_down", True)
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
_p(r"^(?:skip|fast forward|jump forward|forward)(?: (\d+))?(?: seconds?)?$", "media_seek_fwd", True)
_p(r"^(?:rewind|skip back|jump back)(?: (\d+))?(?: seconds?)?$", "media_seek_back", True)
_p(r"^go back (\d+) seconds?$", "media_seek_back", True)
_p(r"^what did (they|he|she) say\??$", "media_what_did_they_say", True)
_p(r"^(?:turn )?subtitles (on|off)$", "media_subtitles", True)
_p(r"^(?:toggle )?(subtitles|closed captions|captions)$", "media_subtitles", True)
_p(r"^next episode$", "media_next_episode", True)
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
# --- timers / time / calc (time, date & voice queries before calculate!)
_p(r"^(set|start)( a)? timer for (\d+) (seconds?|minutes?|hours?)$", "timer")
_p(r"^what time is it\??$", "time", True)
_p(r"^what('s| is) the date\??$", "date", True)
# --- technology radar
_p(r"^(?:what(?:'s| is) on (?:the )?)?(?:tech )?radar(?: report)?$", "tech_radar", True)
_p(r"^(?:check|run) (?:the )?(?:tech )?radar$", "tech_radar", True)
# --- voice & tts engine selection
_p(r"^(?:pick|choose|sample|test|rotate|audition)(?: a)? voices?$", "pick_voice", True)
_p(r"^(?:use|set|choose|switch|change)(?: to)? voice(?: to)?\s+([a-zA-Z0-9_-]+)$", "set_voice")
_p(r"^what(?:'s| is) (?:my|the) voice\??$", "get_voice", True)
_p(r"^(?:calculate\s+(.+)|(?:what is|what's)\s+([\d\s+\-*/().%^]+))$", "calculate")



# --- meta
_p(r"^(help|what can you say|list commands|commands)$", "help", True)
_p(r"^(?:never\s*mind|nevermind|stop\s*listening|that'?s\s*all|cancel|dismiss|done|peace|peace\s*out)$", "dismiss", True)
# --- user shortcuts & voice macros (saved locally in ~/.config/free-voice/macros.json, survives updates)
_p(r"^when i say (.+?)(?:,\s*|\s+(?:then|do|run)\s+)(.+)$", "macro_add")
_p(r"^(?:add|create) shortcut (.+?)(?: runs| to| that runs) (.+)$", "macro_add")
_p(r"^(?:teach shortcut|alias) (.+?)(?: means| to) (.+)$", "macro_add")
_p(r"^macro (.+?) runs (.+)$", "macro_add")
_p(r"^(?:(?:list|show|what are)\s+)?(?:my\s+)?(?:shortcuts|macros)$", "macro_list", True)
_p(r"^(?:delete|remove|forget) (?:shortcut|macro) (.+)$", "macro_delete")
# --- fun: ascii art & drawing
_p(r"^(turn|make|convert)( my| the)? screen into ascii( art)?$", "ascii_art", True)
_p(r"^ascii art( of my screen)?$", "ascii_art", True)
_p(r"^(?:draw(?: me)? ascii|ascii draw) (.+)$", "draw_ascii")
_p(r"^(?:draw|make|create|generate|show|render)(?: me)?(?: a| an| the)? (.+?)(?: in| as) ascii(?: art)?$", "draw_ascii")
_p(r"^draw(?: me)?(?: a| an| the)? ascii (?:art|picture|drawing|image)(?: of| for) (.+)$", "draw_ascii")
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

# --- user custom extensions (~/.config/free-voice/extensions.py)
load_custom_extensions()


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


def stream_process_line(line: str, session: PartialSession, require_wake: bool = True) -> bool:
    """Process a raw streaming transcript line from whisper-stream through PartialSession.

    Strips timestamp headers and ANSI escape codes. Applies wake word filtering if required.
    """
    cleaned = re.sub(r"\[\d\d:\d\d:\d\d\.\d\d\d --> \d\d:\d\d:\d\d\.\d\d\d\]", "", line)
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", cleaned).strip().rstrip(".!?").strip()
    if not cleaned:
        return False

    cmd_text = cleaned
    if require_wake and VOICE_WAKE_WORD:
        is_wake, stripped = parse_wake_word(cleaned, VOICE_WAKE_WORD)
        now = time.time()
        global _wake_window_until
        if is_wake:
            if not stripped:
                _wake_window_until = now + WAKE_WINDOW_SEC
                log(f"streaming wake word {VOICE_WAKE_WORD!r} heard alone — listening for {WAKE_WINDOW_SEC}s...")
                update_state("wake_heard", msg="listening for command")
                acknowledge_wake()
                return True
            cmd_text = stripped
        elif now < _wake_window_until:
            cmd_text = cleaned
        else:
            return False

    return session.feed(cmd_text)


def ensure_ggml_model(model_name: str = "tiny.en") -> str | None:
    """Ensure a GGML model file is available for whisper-stream."""
    models_dir = os.path.expanduser("~/.free-voice/models")
    os.makedirs(models_dir, exist_ok=True)
    target = os.path.join(models_dir, f"ggml-{model_name}.bin")
    if os.path.exists(target) and os.path.getsize(target) > 1000000:
        return target
    hb_sample = "/opt/homebrew/Cellar/whisper.cpp/1.9.4/share/whisper.cpp/for-tests-ggml-tiny.bin"
    if os.path.exists(hb_sample):
        return hb_sample
    url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin"
    log(f"downloading {model_name} model for streaming ({url})…")
    part = target + ".part"
    try:
        # download to .part then rename, so a partial/HTML error file never
        # sits at the final path
        urllib.request.urlretrieve(url, part)
        os.replace(part, target)
        return target
    except Exception as e:  # noqa: BLE001
        log(f"could not download ggml model ({e})")
        try:
            os.remove(part)
        except OSError:
            pass
        return None


def stream_whisper_loop(on_command, step_ms: int = 500, length_ms: int = 5000,
                        wake_word: str = "mac") -> None:
    """Run real-time streaming speech recognition via whisper-stream.

    Tokens are processed incrementally through PartialSession.
    Tier 0 commands fire instantly as soon as their words are completed.
    """
    import shutil
    # PATH first (Intel/MacPorts/custom installs), then the Apple Silicon Homebrew default
    stream_bin = shutil.which("whisper-stream") or "/opt/homebrew/bin/whisper-stream"

    if not stream_bin or not os.path.exists(stream_bin):
        log("whisper-stream binary not found — falling back to standard always-listen loop")
        always_listen_loop(
            lambda audio: on_utterance(audio, quiet_miss=True, require_wake_word=bool(wake_word)),
            wake_word=wake_word,
        )
        return

    model_path = ensure_ggml_model(WHISPER_MODEL or "tiny.en")
    if not model_path:
        log("ggml model unavailable — falling back to standard always-listen loop")
        always_listen_loop(
            lambda audio: on_utterance(audio, quiet_miss=True, require_wake_word=bool(wake_word)),
            wake_word=wake_word,
        )
        return

    def on_fire(name, m):
        log(f"streaming Tier 0 hit: {name} <- {m.group(0)!r}")
        update_state("processing", command=m.group(0))
        on_command(name, m)

    session = PartialSession(on_fire)
    cmd = [
        stream_bin,
        "-m", model_path,
        "--step", str(step_ms),
        "--length", str(length_ms),
        "-t", "4",
        "-l", "en",
    ]
    log(f"starting real-time streaming STT: {' '.join(cmd)}")
    log(f"speak commands prefixed with '{wake_word.title()}' (fires instantly mid-speech)")
    update_state("listening", mode="streaming", wake_word=wake_word)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for line in proc.stdout:
            stream_process_line(line, session, require_wake=bool(wake_word))
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


def _num(m: re.Match, i: int) -> int:
    return int(m.group(i))


def execute_match(name: str, m: re.Match, confirm_audio_fn=None,
                  allow_destructive: bool = False,
                  raise_errors: bool = False) -> None:
    """Run the action for a routed (name, match)."""
    try:

        if name in _CUSTOM_HANDLERS:
            _CUSTOM_HANDLERS[name](m)
            return
        if name == "open_app":
            # both "open X" patterns keep the app phrase in group 2
            act_open_app(m.group(2))
        elif name == "switch_app":
            act_switch_app(m.group(2))
        # quit_app is destructive: handled by the confirmation gate below, not here.
        elif name == "close_window":
            act_close_window()
        elif name == "close_window_named":
            act_close_window(m.group(2).strip())
        elif name == "close_all_windows":
            act_close_all_windows()
        elif name == "close_notifications":
            act_close_notifications()
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
        elif name == "find_file":
            query = m.group(1).strip()
            act_spotlight_search(query)
        elif name == "find_my":
            act_open_app("Find My")
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
        elif name == "snap_top":
            act_snap_window("top")
        elif name == "snap_bottom":
            act_snap_window("bottom")
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
        elif name == "media_seek_fwd":
            sec = int(m.group(1)) if m.group(1) else 10
            act_media_seek(sec, forward=True)
        elif name == "media_seek_back":
            sec = int(m.group(1)) if m.group(1) else 10
            act_media_seek(sec, forward=False)
        elif name == "media_what_did_they_say":
            act_media_what_did_they_say()
        elif name == "media_subtitles":
            act_media_subtitles()
        elif name == "media_next_episode":
            act_media_next_episode()
        elif name == "watch_stream":
            act_watch_stream(m.group(1))
        elif name == "search_youtube":
            act_watch_stream("youtube", query=m.group(1))
        elif name == "airplay_settings":
            act_airplay_settings()
        elif name == "tv_computer":
            act_tv_input("computer", "computer")
        elif name == "tv_tv":
            act_tv_input("tv", "TV")
        elif name == "tv_input":
            src = m.group(5).strip(); act_tv_input(src, src)
        elif name == "tv_power":
            act_tv_power(m.group(1) == "on")
        elif name == "tv_vol_set":
            act_tv_volume_set(int(m.group(2)))
        elif name == "tv_vol_delta":
            direction = m.group(1).lower()
            delta = 1
            if len(m.groups()) >= 2 and m.group(2):
                nums = re.findall(r"\d+", m.group(2))
                if nums:
                    delta = int(nums[0])
            act_tv_volume_delta(delta if direction == "up" else -delta)
        elif name == "tv_mute":
            act_tv_mute(True)
        elif name == "tv_unmute":
            act_tv_mute(False)
        elif name == "tv_media":
            act_tv_media(m.group(1).lower())
        elif name == "tv_art":
            act_tv_art()
        elif name == "lock":
            act_lock()
        elif name == "sleep":
            act_sleep()
        elif name in ("shutdown", "restart", "logout", "empty_trash",
                      "quit_app", "kill_app"):
            # Destructive actions ask first (spoken "yes"), unless --yes.
            # In chained commands each destructive part is confirmed on its own.
            if name == "quit_app":
                desc = f"quitting {m.group(m.lastindex)}"
            elif name == "kill_app":
                desc = f"force quitting {m.group(1)}"
            else:
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
            if name == "quit_app":
                act_quit_app(m.group(m.lastindex))
            elif name == "kill_app":
                act_force_quit_app(m.group(1))
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
            expr = m.group(1) or m.group(2) or ""
            act_calculate(expr)
        elif name == "time":
            act_time()
        elif name == "date":
            act_date()
        elif name == "tech_radar":
            act_tech_radar()
        elif name == "roast_me":
            act_masterpiece_roast("start")
        elif name == "roast_stop":
            act_masterpiece_roast("stop")
        elif name == "roast_theme":
            act_set_critic_theme(m.group(1))
        elif name == "roast_with_theme":
            act_set_critic_theme(m.group(1), start_if_needed=True)
        elif name == "roast_pos":
            act_set_critic_pos(m.group(1))
        elif name == "help":
            cmds = sorted({n for _, n, _ in _PATTERNS})
            print("Commands: " + ", ".join(cmds))
            say("I printed the command list in the terminal")
        elif name == "dismiss":
            global _wake_window_until
            _wake_window_until = 0.0
            log("wake window closed by request")
            say("peace")
        elif name == "dictate_start":
            act_dictate_start()
        elif name == "macro_add":
            act_macro_add(m.group(1), m.group(2))
        elif name == "macro_list":
            act_macro_list()
        elif name == "macro_delete":
            act_macro_delete(m.group(1))
        elif name == "pick_voice":
            act_pick_voice()
        elif name == "set_voice":
            act_set_voice(m.group(1))
        elif name == "get_voice":
            act_get_voice()
        elif name == "ascii_art":
            act_ascii_art()
        elif name == "draw_ascii":
            act_draw_ascii(m.group(m.lastindex))
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
        if raise_errors:
            raise



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
    "close_window {}, close_all_windows {}, close_notifications {}, "
    "new_tab {}, close_tab {}, reopen_tab {}, refresh_page {}, "
    "nav_back {}, nav_forward {}, scroll_down {}, scroll_up {}, "
    "next_space {}, prev_space {}, move_next_display {}, "
    "snap_left {}, snap_right {}, maximize_window {}, center_window {}, "
    "type_text {text: string}, web_search {query: string}, open_url {url: string}, "
    "set_volume {level: 0-100}, volume_up {}, volume_down {}, mute_toggle {}, "
    "media {op: playpause|next|previous}, lock {}, sleep {}, "
    "brightness_up {}, brightness_down {}, screenshot {}, "
    "dark_mode {on: true|false}, wifi {on: true|false}, "
    "tv_power {on: true|false}, tv_volume {level: 0-100, direction: up|down}, tv_input {source: computer|tv|string}, tv_mute {mute: true|false}, "
    "timer {amount: number, unit: seconds|minutes|hours}, "
    "calculate {expr: string}, click_button {name: string}, click_link {name: string}. "
    "If the input is a question, small talk, or you are unsure, reply "
    '{"action": "none", "params": {}, "confidence": 0}.'
)

_TIER1_ACTIONS = {
    "open_app", "quit_app", "switch_app", "minimize", "hide",
    "close_window", "close_all_windows", "close_notifications",
    "new_tab", "close_tab", "reopen_tab", "refresh_page",
    "nav_back", "nav_forward", "scroll_down", "scroll_up",
    "next_space", "prev_space", "move_next_display",
    "snap_left", "snap_right", "maximize_window", "center_window",
    "type_text", "web_search", "open_url",
    "set_volume", "volume_up", "volume_down", "mute_toggle", "media",
    "lock", "sleep", "brightness_up", "brightness_down", "screenshot",
    "dark_mode", "wifi", "timer", "calculate", "click_button", "click_link",
    "tv_power", "tv_volume", "tv_input", "tv_mute",
}

_DESTRUCTIVE_ACTIONS = {
    "shutdown", "restart", "logout", "empty_trash",
    "quit_app", "kill_app",
}

_INTENT_EXAMPLES: dict[str, list[str]] = {
    "draw_ascii": [
        "draw ascii picture of a heart",
        "draw an ascii picture of a heart",
        "draw ascii flower",
        "make an ascii picture of a cat",
        "draw something in ascii",
        "ascii drawing of a dog",
        "ascii art picture",
        "sketch ascii art",
    ],
    "draw_svg": [
        "draw a picture of a cat",
        "draw me a flower",
        "sketch a house",
        "generate a drawing of a car",
        "draw an illustration",
    ],
    "ascii_art": [
        "turn screen into ascii",
        "convert screen to ascii art",
        "make my screen ascii",
        "ascii art of my screen",
    ],
    "open_app": [
        "open notes",
        "launch safari",
        "open google chrome",
        "start terminal",
        "open application",
        "bring up spotify",
    ],
    "switch_app": [
        "switch to safari",
        "focus notes",
        "bring up chrome",
        "switch application",
    ],
    "quit_app": [
        "quit notes",
        "close safari app",
        "exit application",
    ],
    "kill_app": [
        "kill notes",
        "force quit application",
    ],
    "close_window": [
        "close window",
        "close windows",
        "close this window",
        "close the window",
        "close active window",
    ],
    "close_all_windows": [
        "close all windows",
        "close all window",
        "close every window",
        "close all the windows",
        "close all",
    ],
    "snap_left": [
        "snap window left",
        "tile left",
        "window to left side",
    ],
    "snap_right": [
        "snap window right",
        "tile right",
        "window to right side",
    ],
    "snap_top": [
        "snap window top",
        "tile top",
        "window to top half",
        "snap to top",
        "top half of screen",
    ],
    "snap_bottom": [
        "snap window bottom",
        "tile bottom",
        "window to bottom half",
        "snap to bottom",
        "bottom half of screen",
    ],
    "maximize_window": [
        "maximize window",
        "zoom window",
        "make window full screen",
    ],
    "center_window": [
        "center window",
        "put window in center",
    ],
    "minimize": [
        "minimize window",
        "minimize this app",
    ],
    "hide": [
        "hide window",
        "hide application",
    ],
    "new_tab": [
        "open new tab",
        "new browser tab",
    ],
    "close_tab": [
        "close current tab",
        "close browser tab",
    ],
    "reopen_tab": [
        "reopen closed tab",
        "undo close tab",
    ],
    "refresh_page": [
        "refresh page",
        "reload this website",
    ],
    "nav_back": [
        "go back",
        "previous page in browser",
    ],
    "nav_forward": [
        "go forward",
        "next page in browser",
    ],
    "scroll_down": [
        "scroll down",
        "page down",
    ],
    "scroll_up": [
        "scroll up",
        "page up",
    ],
    "type_text": [
        "type text",
        "dictate text",
        "type my message",
    ],
    "web_search": [
        "search google for",
        "look up on web",
        "google search",
    ],
    "open_url": [
        "go to website",
        "visit url",
    ],
    "set_volume": [
        "set volume to",
        "turn volume up",
        "turn volume down",
        "make it louder",
        "make it quieter",
    ],
    "mute_toggle": [
        "mute sound",
        "unmute audio",
    ],
    "media": [
        "play music",
        "pause playback",
        "next track",
        "skip song",
        "previous track",
    ],
    "media_seek": [
        "skip forward 10 seconds",
        "rewind 20 seconds",
        "skip back 30 seconds",
    ],
    "timer": [
        "set a timer for 5 minutes",
        "timer 10 minutes",
        "start countdown",
    ],
    "dark_mode": [
        "turn on dark mode",
        "enable light mode",
    ],
    "wifi": [
        "turn on wifi",
        "disable wifi",
    ],
    "screenshot": [
        "take a screenshot",
        "screen capture",
        "screenshot of window",
    ],
    "lock": [
        "lock the screen",
        "lock computer",
    ],
    "sleep": [
        "put computer to sleep",
        "sleep display",
    ],
    "shutdown": [
        "shut down computer",
        "power off mac",
    ],
    "restart": [
        "restart computer",
        "reboot mac",
    ],
    "logout": [
        "log out user",
    ],
    "empty_trash": [
        "empty the trash",
        "clear trash",
    ],
    "status": [
        "are you working",
        "system status",
        "health check",
    ],
    "dismiss": [
        "stop listening",
        "never mind",
        "peace out",
        "that's all",
        "dismiss",
    ],
    "help": [
        "help",
        "what can you do",
        "list commands",
    ],
    "calculate": [
        "calculate math",
        "what is 5 times 8",
    ],
    "tv_power": [
        "turn on the tv",
        "turn off the tv",
        "turn the tv off",
        "turn the tv on",
        "power off the tv",
        "power on the tv",
        "turn television on",
        "turn television off",
        "switch off the tv",
    ],
    "tv_input": [
        "switch to computer",
        "switch to tv",
        "change tv input to computer",
        "switch input to mac",
        "switch tv input",
    ],
    "tv_volume": [
        "turn tv volume up",
        "turn tv volume down",
        "make the tv louder",
        "tv volume louder",
        "set tv volume",
    ],
    "tv_mute": [
        "mute the tv",
        "unmute the tv",
        "mute tv",
    ],
}


def embed_utterance(text: str, dim: int = 1024):
    """Fast, in-process, deterministic feature hashing embedding (~18us)."""
    import numpy as np
    vec = np.zeros(dim, dtype=np.float32)
    tokens = re.findall(r"\b\w+\b", text.lower())
    t_padded = f" {text.lower()} "
    ngrams3 = [t_padded[i:i+3] for i in range(len(t_padded)-2)]
    ngrams4 = [t_padded[i:i+4] for i in range(len(t_padded)-3)]
    for feat in tokens + ngrams3 + ngrams4:
        b = feat.encode("utf-8")
        h = zlib.crc32(b)
        idx = h % dim
        sign = 1.0 if (h & 1) else -1.0
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


_PRECOMPUTED_INTENTS: list[tuple[str, str]] = []
_PRECOMPUTED_MATRIX = None


def _init_intent_embeddings() -> None:
    global _PRECOMPUTED_INTENTS, _PRECOMPUTED_MATRIX
    if _PRECOMPUTED_MATRIX is not None:
        return
    import numpy as np
    rows = []
    intents = []
    for action, examples in _INTENT_EXAMPLES.items():
        for ex in examples:
            rows.append(embed_utterance(ex))
            intents.append((action, ex))
    _PRECOMPUTED_INTENTS = intents
    _PRECOMPUTED_MATRIX = np.array(rows, dtype=np.float32)


def _extract_intent_params(intent: str, text: str) -> dict:
    params = {}
    if intent == "draw_ascii":
        sub = re.sub(r"^(?:please\s+|can you\s+|could you\s+|go ahead and\s+|i want you to\s+)?", "", text, flags=re.I)
        sub = re.sub(r"^(?:draw|sketch|make|create|generate|show|render|call|john)\s+(?:me\s+)?(?:an?\s+)?", "", sub, flags=re.I)
        sub = re.sub(r"^(?:picture|image|drawing)\s+of\s+(?:an?\s+)?", "", sub, flags=re.I)
        sub = re.sub(r"^(?:ascii|askey)\s*(?:art|picture|drawing|image)?(?:\s+(?:of|for))?\s*", "", sub, flags=re.I)
        sub = re.sub(r"^(?:picture|image|drawing)\s+of\s+(?:an?\s+)?", "", sub, flags=re.I)
        sub = re.sub(r"^(?:a|an|the)\s+", "", sub, flags=re.I)
        sub = re.sub(r"\s+(?:in|as)\s+ascii(?:\s+art)?$", "", sub, flags=re.I)
        params["subject"] = sub.strip() or "heart"
    elif intent == "draw_svg":
        sub = re.sub(r"^(?:draw|sketch|make|create|generate)\s+(?:me\s+)?(?:a|an|the)?\s*", "", text, flags=re.I)
        params["subject"] = sub.strip() or "flower"
    elif intent in ("open_app", "switch_app", "quit_app", "kill_app", "minimize", "hide"):
        app = resolve_app(text, prefer_running=(intent == "switch_app"))
        if not app:
            sub = re.sub(r"^(?:open|launch|start|switch to|focus|quit|close|kill|minimize|hide)\s+(?:the\s+)?", "", text, flags=re.I)
            app = resolve_app(sub, prefer_running=(intent == "switch_app"))
        params["app"] = app or ""
    elif intent == "set_volume":
        m = re.search(r"\b(\d{1,3})\b", text)
        if m:
            params["level"] = int(m.group(1))
        elif any(w in text.lower() for w in ("down", "lower", "softer", "quiet")):
            params["direction"] = "down"
        elif any(w in text.lower() for w in ("up", "raise", "louder")):
            params["direction"] = "up"
        elif "mute" in text.lower():
            params["level"] = 0
    elif intent == "media":
        if any(w in text.lower() for w in ("next", "skip")):
            params["op"] = "next"
        elif any(w in text.lower() for w in ("prev", "back", "previous")):
            params["op"] = "previous"
        elif any(w in text.lower() for w in ("pause", "stop")):
            params["op"] = "pause"
        else:
            params["op"] = "play"
    elif intent == "media_seek":
        m = re.search(r"\b(\d+)\b", text)
        params["seconds"] = int(m.group(1)) if m else 15
        params["direction"] = "back" if any(w in text.lower() for w in ("back", "rewind")) else "fwd"
    elif intent == "timer":
        m = re.search(r"\b(\d+)\s*(min|minute|sec|second|hour)", text, re.IGNORECASE)
        if m:
            params["amount"] = int(m.group(1))
            params["unit"] = m.group(2).lower()
        else:
            word_map = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
            }
            val = 5
            for w, num in word_map.items():
                if w in text.lower():
                    val = num
                    break
            params["amount"] = val
            params["unit"] = "minutes"
    elif intent == "web_search":
        sub = re.sub(r"^(?:search for|google|search|look up)\s+", "", text, flags=re.I).strip()
        params["query"] = sub
    elif intent == "type_text":
        sub = re.sub(r"^(?:type|dictate)\s+", "", text, flags=re.I).strip()
        params["text"] = sub
    elif intent == "calculate":
        sub = re.sub(r"^(?:calculate|what is|what's)\s+", "", text, flags=re.I).strip()
        params["expr"] = sub
    elif intent == "dark_mode":
        params["on"] = not any(w in text.lower() for w in ("off", "disable", "light"))
    elif intent == "wifi":
        params["on"] = not any(w in text.lower() for w in ("off", "disable"))
    return params


def tier05_embed_match(text: str, threshold: float = 0.75) -> tuple[str, dict, float] | None:
    """Step (a): In-process embedding cosine-match against example utterances (~1ms budget)."""
    import numpy as np
    _init_intent_embeddings()
    if _PRECOMPUTED_MATRIX is None or len(_PRECOMPUTED_INTENTS) == 0:
        return None
    qv = embed_utterance(text)
    sims = np.dot(_PRECOMPUTED_MATRIX, qv)
    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])
    if best_sim >= threshold:
        action, _ = _PRECOMPUTED_INTENTS[best_idx]
        params = _extract_intent_params(action, text)
        return action, params, best_sim
    return None


_TIER05_DECISION_SYSTEM = (
    "You are a fast voice intent classifier for a Mac assistant. "
    "Classify the spoken command into exactly ONE action and extract parameters into JSON. "
    "Handle acoustic mishearings from speech-to-text gracefully (e.g. 'john askey' -> draw_ascii, 'call ascii' -> draw_ascii). "
    "Allowed actions: "
    "open_app {app: string}, switch_app {app: string}, quit_app {app: string}, "
    "set_volume {level: 0-100 or direction: 'up'|'down'}, media {op: 'play'|'pause'|'next'|'previous'}, "
    "timer {amount: number, unit: 'seconds'|'minutes'|'hours'}, web_search {query: string}, "
    "draw_ascii {subject: string}, draw_svg {subject: string}. "
    "If it is small talk, general question, or not an action, reply: "
    '{"action": "none", "params": {}, "confidence": 0.0}. '
    "Reply with ONLY valid JSON: {\"action\": \"...\", \"params\": {...}, \"confidence\": 0.0-1.0}."
)

_DECISION_CRITERIA = {
    "open_app": "Open, launch, or focus an application or browser",
    # quit_app (not close_app): dispatchable, and gated via _DESTRUCTIVE_ACTIONS
    "quit_app": "Close, quit, or exit an application",
    "switch_app": "Switch, focus, or bring up a running application",
    "set_volume": "Change, raise, lower, or mute audio volume",
    "media": "Play, pause, skip, next, or control music/video playback",
    "timer": "Set a timer, countdown, or reminder",
    "web_search": "Search the web or Google for a topic",
    "draw_ascii": "Draw, generate, or display ASCII text art or pictures (including 'john askey')",
    "draw_svg": "Draw, sketch, or generate a vector drawing or illustration",
    "unknown": "None of the above, complex question, or ambiguous",
}

_COMMAND_VERB_PREFIX_RE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|would you\s+|go ahead and\s+|i want you to\s+|hey mac\s+|hay mac\s+|mac\s+)?"
    r"(?:open|launch|start|switch|change|focus|quit|close|kill|set|turn|mute|unmute|play|pause|stop|skip|next|prev|previous|rewind|fast forward|search|google|find|browse|look up|show|bring up|watch|stream|timer|countdown|alarm|remind|snap|maximize|minimize|center|lock|sleep|restart|shutdown|type|draw|sketch|render|take a screenshot|screenshot|dismiss|never mind|that's all|stop listening|peace out|cancel|(?:john|call)\s+(?:an?\s+)?(?:askey|ascii))\b",
    re.IGNORECASE,
)


def is_voice_command(text: str) -> tuple[bool, float]:
    """Step 1 Gate: Fast pre-routing check to filter out ambient conversation,
    TV dialogue, and statements before invoking the intent router.
    Returns (is_command, confidence).
    """
    if not VOICE_COMMAND_GATE:
        return True, 1.0

    clean = text.strip()
    if not clean:
        return False, 0.0

    # Fast path (0ms): Obvious imperative command verbs bypass model check
    if _COMMAND_VERB_PREFIX_RE.search(clean):
        return True, 1.0

    # Decision model gate check via /v1/systemone
    gate_model = VOICE_COMMAND_GATE_MODEL or OLLAMA_DECISION_MODEL
    url = f"{OLLAMA_HOST}/v1/systemone"
    payload = {
        "model": gate_model,
        "state": clean,
        "questions": {
            "is_command": {
                "type": "choice",
                "instructions": "Is this spoken phrase an imperative command or action directed at a computer assistant?",
                "criteria": {
                    "command": "An instruction or command for the computer to perform an action (e.g. open an app, adjust volume, search, play music, set timer)",
                    "none": "Casual speech, statement, question, TV/podcast dialogue, or not asking the assistant to take an action",
                },
            }
        },
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.load(r)
        ans = data.get("answers", {}).get("is_command")
        if not ans:
            return True, 1.0
        choice = ans.get("choice")
        probs = ans.get("probabilities", {})
        cmd_prob = float(probs.get("command", 0.0))
        is_cmd = (choice == "command") and (cmd_prob >= 0.5)
        log(f"Command gate ({gate_model}): choice={choice!r} cmd_prob={cmd_prob:.2f} -> {'ALLOW' if is_cmd else 'REJECT'}")
        return is_cmd, cmd_prob
    except Exception as e:
        # If gate model endpoint is unreachable or errors, fail-closed to prevent ambient TV false triggers
        log(f"Command gate ({gate_model}) fallback: {e} -> failing closed")
        return False, 0.0


def ollama_decision_route(text: str) -> tuple[str, dict, float] | None:
    """Fast decision model (tev1:0.8b or qwen2.5:1.5b) for intent & slot classification."""
    global _decision_ok
    if not OLLAMA_DECISION_MODEL or _decision_ok is False:
        return None

    # Step 1: Pre-routing Command Gate (rejects background chatter & TV audio)
    is_cmd, gate_conf = is_voice_command(text)
    if not is_cmd:
        log(f"Tier 0.5b dropped non-command: {text!r} (gate conf={gate_conf:.2f})")
        notify_hud(f'Ignored: "{text}"', "rejected")
        return None

    # Step 2 & 3: Fast decision model (/v1/systemone with 8-choice criteria)
    if any(k in OLLAMA_DECISION_MODEL.lower() for k in ("tev1", "julia", "laya", "kev")):
        url = f"{OLLAMA_HOST}/v1/systemone"
        payload = {
            "model": OLLAMA_DECISION_MODEL,
            "state": text,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "What voice action should be taken?",
                    "criteria": _DECISION_CRITERIA,
                }
            },
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.load(r)
            _decision_ok = True
            ans = data.get("answers", {}).get("action", {})
            choice = ans.get("choice")
            if choice and choice not in ("unknown", "none"):
                prob = float(ans.get("probabilities", {}).get(choice, 0.0))
                min_conf = DECISION_MIN_CONFIDENCE
                if prob >= min_conf:
                    params = _extract_intent_params(choice, text)
                    log(f"Tier 0.5b ({OLLAMA_DECISION_MODEL}) -> {choice} {params} prob={prob:.2f}")
                    return choice, params, prob
                else:
                    log(f"Tier 0.5b ({OLLAMA_DECISION_MODEL}) low confidence: {choice} prob={prob:.2f} < {min_conf:.2f}")
            return None
        except Exception as e:
            log(f"Tier 0.5b decision model unreachable ({OLLAMA_DECISION_MODEL}): {e}")
            pass

    # Standard Ollama JSON mode for qwen2.5:1.5b (fallback)
    t0 = time.time()
    body = {
        "model": OLLAMA_DECISION_MODEL,
        "format": "json",
        "stream": False,
        "keep_alive": "60m",
        "options": {"temperature": 0, "num_predict": 64, "num_ctx": 1024},
        "messages": [
            {"role": "system", "content": _TIER05_DECISION_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
        _decision_ok = True
    except Exception as e:
        log(f"Tier 0.5 decision model unreachable ({OLLAMA_DECISION_MODEL}): {e}")
        return None

    dt = time.time() - t0
    raw = data.get("message", {}).get("content", "").strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return None

    action = str(parsed.get("action", "none")).strip().lower()
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    params = parsed.get("params", {}) or {}

    log(f"Tier 0.5b ({OLLAMA_DECISION_MODEL}) -> {action} {params} conf={conf:.2f} in {dt:.2f}s")
    min_conf = DECISION_MIN_CONFIDENCE
    if action in ("none", "unknown") or conf < min_conf:
        return None
    return action, params, conf


def split_compound_commands(text: str) -> list[str]:
    """Split compound multi-step utterance on natural conjunctions and punctuation."""
    t = text.strip().rstrip(".!?").strip()
    if not t:
        return []
    pattern = r"(?:,\s*(?:and\s+then|then|and)\s*|\s+(?:and\s+then|then|and)\s+|,\s+)"
    return [p.strip().rstrip(".!?") for p in re.split(pattern, t, flags=re.IGNORECASE) if p.strip()]


_MULTI_ACTION_PLANNER_SYSTEM = (
    "You are an Apple macOS voice assistant action planner. "
    "Given a compound multi-step user request, output a JSON object containing a sequential array of executable actions. "
    "Allowed actions and parameter format:\n"
    "- open_app: {\"app\": \"Safari\"}\n"
    "- web_search: {\"query\": \"search text\"}\n"
    "- open_url: {\"url\": \"https://...\"}\n"
    "- snap_left: {}\n"
    "- snap_right: {}\n"
    "- snap_top: {}\n"
    "- snap_bottom: {}\n"
    "- maximize_window: {}\n"
    "- center_window: {}\n"
    "- type_text: {\"text\": \"string\"}\n"
    "- click_button: {\"name\": \"button text\"}\n"
    "- set_volume: {\"level\": 0-100}\n"
    "- media: {\"op\": \"playpause\"|\"next\"|\"previous\"}\n"
    "- screenshot: {\"target\": \"full\"}\n"
    "- close_window: {}\n"
    "- new_tab: {}\n"
    "Reply with ONLY valid JSON: {\"actions\": [{\"action\": \"...\", \"params\": {...}}]}.\n"
    "If not a multi-step tool command, return {\"actions\": []}."
)


def ollama_multi_action_plan(text: str) -> list[dict] | None:
    """Use fast planner model (qwen2.5:1.5b) to decompose complex multi-step prompts into actions."""
    global _decision_ok
    if not OLLAMA_PLANNER_MODEL or _decision_ok is False:
        return None
    planner_model = OLLAMA_PLANNER_MODEL
    body = {
        "model": planner_model,
        "format": "json",
        "stream": False,
        "keep_alive": "60m",
        "options": {"temperature": 0, "num_predict": 128, "num_ctx": 2048},
        "messages": [
            {"role": "system", "content": _MULTI_ACTION_PLANNER_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
        _decision_ok = True
        content = data.get("message", {}).get("content", "").strip()
        parsed = json.loads(content)
        actions = parsed.get("actions", [])
        if isinstance(actions, list) and len(actions) >= 2:
            log(f"multi-action planner: {len(actions)} actions in {time.time()-t0:.2f}s")
            return actions
    except Exception as e:
        log(f"Multi-action planner failed: {e}")
    return None


def tier05_route(text: str) -> tuple[str, dict, float] | None:
    """Tier 0.5: in-process embedding match (a) + fast LLM intent classifier (b).
    Mandatory safety gate: destructive intents are blocked here."""
    # (a) In-process embedding cosine match (threshold 0.75)
    match = tier05_embed_match(text, threshold=0.75)
    if match is not None:
        action, params, score = match
        if action in _DESTRUCTIVE_ACTIONS:
            log(f"Tier 0.5 blocked destructive action {action!r} (Tier 0 regex safety gate required)")
            say(f"For safety, please say 'Mac, {action.replace('_', ' ')}' directly")
            return "blocked", {}, 1.0
        log(f"Tier 0.5a (embedding cosine={score:.2f}) -> {action} {params}")
        return action, params, score

    # (b) Fast decision model (qwen2.5:1.5b) in JSON mode
    dec = ollama_decision_route(text)
    if dec is not None:
        action, params, conf = dec
        if action in _DESTRUCTIVE_ACTIONS:
            log(f"Tier 0.5 blocked destructive action {action!r} (Tier 0 regex safety gate required)")
            say(f"For safety, please say 'Mac, {action.replace('_', ' ')}' directly")
            return "blocked", {}, 1.0
        return action, params, conf

    return None


def ollama_route(text: str):
    """Tier 1: ask the local model for a typed action.

    Tries Tier 0.5 fast local decision model first, then falls back to VLM.
    Returns (action, params, confidence) or None on miss/unreachable.
    """
    global _ollama_ok, _ollama_last_failure
    if not OLLAMA_TIER1:
        return None
    # Don't hammer Ollama if it failed within the last 30s, but do retry afterwards
    if _ollama_ok is False and (time.time() - _ollama_last_failure < 30.0):
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
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
        _ollama_ok = True
    except Exception as e:  # noqa: BLE001
        _ollama_ok = False
        _ollama_last_failure = time.time()
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
    if action == "blocked":
        return
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
        act_close_window(str(p("app", "")))
    elif action == "close_all_windows":
        act_close_all_windows()
    elif action == "close_notifications":
        act_close_notifications()
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
    elif action == "snap_top":
        act_snap_window("top")
    elif action == "snap_bottom":
        act_snap_window("bottom")
    elif action == "snap_window":
        side = str(p("side") or p("direction") or "left")
        act_snap_window(side)
    elif action == "maximize_window":
        act_snap_window("maximize")
    elif action == "center_window":
        act_snap_window("center")
    elif action == "type_text":
        act_type_text(str(p("text", ""))); say("Typed")
    elif action == "web_search":
        act_web_search(str(p("query", "")))
    elif action == "find_file":
        act_spotlight_search(str(p("query") or p("file") or p("text") or ""))
    elif action == "find_my":
        act_open_app("Find My")
    elif action == "open_url":
        act_open_url(str(p("url", "")))
    elif action in ("set_volume", "volume_up", "volume_down"):
        if action == "volume_up":
            act_volume_delta(10)
        elif action == "volume_down":
            act_volume_delta(-10)
        else:
            level = p("level")
            direction = str(p("direction") or "").lower()
            if str(level).lower() in ("up", "down"):
                act_volume_delta(10 if str(level).lower() == "up" else -10)
            elif level is None and direction in ("up", "down"):
                act_volume_delta(10 if direction == "up" else -10)
            else:
                try:
                    act_set_volume(int(p("level", 50)))
                except (TypeError, ValueError):
                    say("I didn't get a volume level")
    elif action == "tv_power":
        act_tv_power(bool(p("on", True)))
    elif action == "tv_volume":
        if p("level") is not None:
            try:
                act_tv_volume_set(int(p("level")))
            except (TypeError, ValueError):
                act_tv_volume_delta(1)
        else:
            direction = str(p("direction") or "up").lower()
            act_tv_volume_delta(1 if direction == "up" else -1)
    elif action == "tv_input":
        inp = str(p("input") or p("source") or "computer")
        act_tv_input(inp, inp)
    elif action == "tv_mute":
        act_tv_mute(bool(p("mute", True)))
    elif action == "tv_art":
        act_tv_art()
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
        unit = _normalize_time_unit(p("unit", "minutes"))
        act_timer(amount, unit)
    elif action == "calculate":
        act_calculate(str(p("expr", "")))
    elif action == "click_button":
        act_click_button(str(p("name", "")))
    elif action == "click_link":
        act_click_link(str(p("name", "")))
    elif action == "draw_ascii":
        subj = str(p("subject") or p("subj") or p("text") or "heart")
        act_draw_ascii(subj)
    elif action == "draw_svg":
        subj = str(p("subject") or p("subj") or p("text") or "flower")
        act_draw_svg(subj)
    elif action == "ascii_art":
        act_ascii_art()
    elif action == "status":
        act_status()
    elif action == "help":
        cmds = sorted({n for _, n, _ in _PATTERNS})
        print("Commands: " + ", ".join(cmds))
        say("I printed the command list in the terminal")
    elif action == "dismiss":
        global _wake_window_until
        _wake_window_until = 0.0
        log("wake window closed by request")
        say("peace")
    elif action == "media_seek":
        sec = int(p("seconds") or p("amount") or 15)
        direction = str(p("direction") or "fwd").lower()
        act_media_seek(seconds=sec, forward=direction not in ("back", "backward", "rewind"))
    else:
        say("I couldn't map that to an action")


# ---------------------------------------------------------------- Tier 2: Gemini free-tier Q&A (search-grounded)

def ollama_answer(prompt: str) -> bool:
    """Answer a free-form question with the local model (offline fallback).

    Used when Gemini's free tier is exhausted (HTTP 429) or unreachable.
    """
    global _ollama_ok, _ollama_last_failure
    if _ollama_ok is False and (time.time() - _ollama_last_failure < 30.0):
        return False
    body = {
        "model": OLLAMA_MODEL,
        "keep_alive": "60m",
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 256},
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
        _ollama_last_failure = time.time()
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


def prewarm_speech() -> None:
    """Load Whisper and Kokoro models into memory at startup in background."""
    if DRY_RUN:
        return

    def _load() -> None:
        try:
            import numpy as np
            transcribe(np.zeros(16000, dtype=np.float32))
            log(f"prewarmed whisper ({WHISPER_MODEL})")
        except Exception:
            pass
        try:
            _get_kokoro()
        except Exception:
            pass

    threading.Thread(target=_load, daemon=True, name="speech-prewarm").start()



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
           f"{GEMINI_MODEL}:generateContent")
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": GEMINI_API_KEY})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        say(text)
        return True
    except urllib.error.HTTPError as e:
        try:
            if e.code == 429:
                log("gemini 429 — free-tier quota hit, falling back to local model")
                say("Google's free limit is hit, answering from the on-device model")
            else:
                note_error(f"Gemini HTTP {e.code}")
                log(f"gemini HTTP {e.code}: {e} — falling back to local model")
                say("Google didn't answer, trying the on-device model")
        finally:
            try:
                e.close()
            except Exception:
                pass
        return ollama_answer(prompt)
    except Exception as e:  # noqa: BLE001
        note_error("Gemini unreachable")
        log(f"gemini failed ({e}) — falling back to local model")
        say("Google didn't answer, trying the on-device model")
        return ollama_answer(prompt)


# ---------------------------------------------------------------- the cascade

def handle_command(text: str, confirm_audio_fn=None,
                   allow_destructive: bool = False,
                   quiet_miss: bool = False,
                   require_wake_word: bool = False) -> bool:
    """Route one transcript through the tiers. Returns True if handled.

    quiet_miss=True (always-listening mode): a total miss is logged, not
    spoken, so background chatter never makes the Mac talk to itself.

    require_wake_word=True (always-listening mode): requires the utterance to
    start with the wake word (e.g. 'Mac, ...') or to arrive within an active
    wake window. Conversational background chatter is ignored.
    """
    global _wake_window_until
    t = text.strip().rstrip(".!?").strip()
    if not t:
        return False

    if VOICE_WAKE_WORD:
        is_wake, cmd = parse_wake_word(t, VOICE_WAKE_WORD)
        if require_wake_word:
            now = time.time()
            global _wake_window_opened_at
            if is_wake:
                if not cmd:
                    # Spoke wake word alone: open a listening window
                    _wake_window_opened_at = now
                    _wake_window_until = now + WAKE_WINDOW_SEC
                    log(f"wake word {VOICE_WAKE_WORD!r} heard alone — listening for {WAKE_WINDOW_SEC}s...")
                    update_state("wake_heard", msg="listening for command")
                    acknowledge_wake()
                    return True
                # Wake word + command in one sentence: execute immediately & keep window open for follow-ups
                _wake_window_opened_at = now
                _wake_window_until = now + WAKE_WINDOW_SEC
                t = cmd
            elif now < _wake_window_until:
                # Arrived within active wake window: execute immediately & refresh window for follow-ups
                # Guard: hard cap continuous extensions so TV chatter cannot hold it open forever
                if _wake_window_opened_at and (now - _wake_window_opened_at >= WAKE_WINDOW_MAX_CAP_SEC):
                    _wake_window_until = now  # Close window after max continuous duration
                    log("wake window closed: reached maximum continuous duration cap")
                else:
                    _wake_window_until = min(now + WAKE_WINDOW_SEC, (_wake_window_opened_at or now) + WAKE_WINDOW_MAX_CAP_SEC)
                rem = max(0.0, _wake_window_until - now)
                log(f"within wake window ({rem:.1f}s remaining): {t!r}")
                # Command gate protection: ensure ambient follow-up speech is an actual command before routing
                is_cmd, gate_conf = is_voice_command(t)
                if not is_cmd:
                    log(f"wake window follow-up dropped by command gate: {t!r} (conf={gate_conf:.2f})")
                    notify_hud(f'Ignored: "{t}"', "rejected")
                    return False
            else:
                log(f"ignored (no wake word {VOICE_WAKE_WORD!r}): {t!r}")
                return False

        else:
            # Wake word not strictly required, but strip if user said it
            if is_wake and cmd:
                t = cmd

    if not t:
        return False

    log(f"heard: {t!r}")
    update_state("processing", command=t)
    notify_hud(f'"{t}"', "transcribed")

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

    # Compound command chaining: if single Tier 0 missed, try chaining (e.g. "open notes and snap left" or "Open Safari, find Apple's latest 10-K, and snap it to the left half of my screen")
    if any(sep in t.lower() for sep in (" and ", " then ", ", ")):
        parts = split_compound_commands(t)
        if len(parts) > 1:
            plan = []
            all_resolved = True
            for p in parts:
                sub_r = route(p, partial=False)
                if sub_r:
                    plan.append(("tier0", sub_r[0], sub_r[1]))
                    continue
                # Try Tier 0.5 for sub-command
                t05 = tier05_route(p)
                if t05 and t05[0] != "blocked" and t05[2] >= 0.5:
                    plan.append(("tier1", t05[0], t05[1]))
                    continue
                all_resolved = False
                break

            if all_resolved and len(plan) == len(parts):
                log(f"Multi-action chain resolved ({len(plan)} actions): {parts}")
                for step in plan:
                    try:
                        if step[0] == "tier0":
                            execute_match(step[1], step[2], confirm_audio_fn, allow_destructive, raise_errors=True)
                        elif step[0] == "tier1":

                            dispatch_tier1(step[1], step[2], allow_destructive)
                    except Exception as e:
                        failed_name = step[1] if len(step) > 1 else "step"
                        log(f"Chain step '{failed_name}' failed: {e}. Aborting remaining actions for safety.")
                        say(f"Couldn't complete {failed_name}, stopping chain")
                        break
                    time.sleep(0.35)
                return True

            # If rule/regex chaining didn't resolve all parts, fall back to agentic multi-action LLM planner
            llm_actions = ollama_multi_action_plan(t)
            if llm_actions:
                log(f"Agentic multi-action chain ({len(llm_actions)} actions): {llm_actions}")
                for act in llm_actions:
                    act_name = str(act.get("action", "")).strip().lower()
                    act_params = act.get("params", {}) or {}
                    try:
                        dispatch_tier1(act_name, act_params, allow_destructive)
                    except Exception as e:
                        log(f"Agentic chain step '{act_name}' failed: {e}. Aborting remaining actions for safety.")
                        say(f"Couldn't complete {act_name}, stopping chain")
                        break
                    time.sleep(0.35)
                return True


    # Tier 0.5: embedding cosine-match + fast decision model (tev1:0.8b / qwen2.5:1.5b)
    t05 = tier05_route(t)
    if t05:
        action, params, conf = t05
        # Ask before acting when router is unsure (medium confidence 0.50 <= conf < 0.70)
        if 0.50 <= conf < 0.70 and confirm_audio_fn is not None:
            desc = describe_action_for_prompt(action, params)
            if not confirm_spoken(desc, confirm_audio_fn):
                say("Action cancelled")
                return True
        try:
            dispatch_tier1(action, params, allow_destructive)
        except Exception as e:  # noqa: BLE001
            say("That didn't work")
            log(f"tier 0.5 action failed: {e}")
        return True

    # Tier 1: local vision-language model (only on Tier 0 & 0.5 miss)
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


def on_utterance(audio, quiet_miss: bool = False, require_wake_word: bool = False) -> None:
    if audio is not None and len(audio) > 16000 * 0.4:
        is_user, sim = verify_speaker(audio)
        if not is_user:
            log(f"Speaker mismatch (sim={sim:.2f} < {SPEAKER_THRESHOLD}) — dropped ambient/TV voice")
            notify_hud(f"Ignored: other voice ({sim:.2f})", "rejected")
            return
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
    handle_command(text, confirm_audio_fn=record_fixed, quiet_miss=quiet_miss,
                   require_wake_word=require_wake_word)


def cmd_speaker_status() -> None:
    enrolled = is_speaker_enrolled()
    print("==> Speaker Verification Status:")
    print(f"    Microphone:   {describe_input_device()}")
    print(f"    Enrolled:     {'YES' if enrolled else 'NO'}")
    print(f"    Profile path: {get_profile_path()}")
    print(f"    Model path:   {get_model_path()}")
    print(f"    Threshold:    {SPEAKER_THRESHOLD}")
    if not enrolled:
        print("    Tip: Run './.venv/bin/python free_voice.py --enroll' to register your voice.")


def cmd_reset_speaker() -> None:
    if reset_speaker():
        print("==> Enrolled voice profile deleted. Speaker verification is now disabled.")
    else:
        print("==> No enrolled voice profile found.")


def cmd_enroll_speaker() -> None:
    print("==> Speaker Voice Profile Enrollment")
    print(f"    Active Microphone: {describe_input_device()}")
    print("    Note: Please speak from your normal commanding position and distance.")
    print("    This records 2 short phrases to calibrate your voiceprint.")
    print()
    usable, _ = _usable_input()
    if not usable:
        _notice_no_mic()
        return

    ensure_model()

    input("Press [Enter] and speak phrase 1: 'Hey Mac, set volume to 50 and open Safari' ... ")
    print("Recording 4 seconds...")
    audio1 = record_fixed(4.0)
    print("Sample 1 captured!\n")

    input("Press [Enter] and speak phrase 2: 'The quick brown fox jumps over the lazy dog' ... ")
    print("Recording 4 seconds...")
    audio2 = record_fixed(4.0)
    print("Sample 2 captured!\n")

    try:
        consistency = enroll_speaker([audio1, audio2])
        print(f"==> Voice profile enrolled successfully! Consistency score: {consistency:.2f}")
        print(f"    Saved to: {get_profile_path()}")
        print(f"    Active Microphone: {describe_input_device()}")
        print("    Speaker verification is now ACTIVE. Background TV and podcast speech will be rejected.")
    except Exception as e:
        print(f"==> Enrollment failed: {e}")


def main() -> None:
    global DRY_RUN, VOICE_MIC, VOICE_WAKE_WORD, ALWAYS_MODE
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
    ap.add_argument("--enroll-speaker", "--enroll", action="store_true",
                    help="enroll your voice profile to eliminate background TV and chatter false triggers")
    ap.add_argument("--reset-speaker", action="store_true",
                    help="delete enrolled voice profile")
    ap.add_argument("--speaker-status", action="store_true",
                    help="show speaker verification enrollment status")
    ap.add_argument("--yes", action="store_true",
                    help="allow destructive actions in --text mode")
    ap.add_argument("--always", action="store_true",
                    help="listen continuously with voice activity detection; "
                         "Ctrl-C quits (requires wake word by default)")
    ap.add_argument("--stream", action="store_true",
                    help="listen continuously using real-time whisper.cpp streaming "
                         "(sub-500ms mid-speech firing via PartialSession)")
    ap.add_argument("--sensitivity", type=float, default=VOICE_VAD_SENSITIVITY, metavar="X",
                    help=f"always-listen mic sensitivity multiplier (default {VOICE_VAD_SENSITIVITY})")
    ap.add_argument("--wake-word", metavar="WORD", default=VOICE_WAKE_WORD,
                    help=f"wake word required in --always mode (default: {VOICE_WAKE_WORD!r})")
    ap.add_argument("--no-wake-word", action="store_true",
                    help="disable wake word requirement in --always mode (open mic)")
    ap.add_argument("--mic", metavar="NAME",
                    help="use the input device whose name contains NAME "
                         "(e.g. --mic iPhone); overrides VOICE_MIC")
    args = ap.parse_args()
    DRY_RUN = args.dry_run
    if args.mic:
        VOICE_MIC = args.mic
    if args.wake_word is not None:
        VOICE_WAKE_WORD = args.wake_word.strip().lower()
    if args.no_wake_word:
        VOICE_WAKE_WORD = ""

    if args.speaker_status:
        cmd_speaker_status()
        return
    if args.reset_speaker:
        cmd_reset_speaker()
        return
    if args.enroll_speaker:
        cmd_enroll_speaker()
        return

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
    prewarm_speech()  # load Whisper and Kokoro so first interaction is instant
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
    if args.stream:
        ALWAYS_MODE = True
        require_wake = bool(VOICE_WAKE_WORD) and not args.no_wake_word
        active_wake = VOICE_WAKE_WORD if require_wake else ""
        stream_whisper_loop(
            lambda name, m: execute_match(name, m),
            wake_word=active_wake,
        )
        return
    if args.always:
        ALWAYS_MODE = True
        require_wake = bool(VOICE_WAKE_WORD) and not args.no_wake_word
        active_wake = VOICE_WAKE_WORD if require_wake else ""
        always_listen_loop(
            lambda audio: on_utterance(audio, quiet_miss=True, require_wake_word=require_wake),
            sensitivity=args.sensitivity,
            wake_word=active_wake,
        )
        return
    push_to_talk_loop(on_utterance)


if __name__ == "__main__":
    main()
