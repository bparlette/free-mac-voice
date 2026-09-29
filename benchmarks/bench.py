#!/usr/bin/env python3
"""Component benchmarks for free-mac-voice.

Measures each pipeline stage separately so regressions show up by name:

    python3 benchmarks/bench.py            # full run
    python3 benchmarks/bench.py --quick   # fewer iterations (CI / smoke)
    python3 benchmarks/bench.py --json    # machine-readable output

Anything that needs hardware it doesn't have (mic, screen, Ollama, whisper)
is reported as SKIPPED with a reason instead of failing — exit code stays 0
so this is safe to run on Linux CI and on the Mac.

Results print as a Markdown table and append to benchmarks/results.jsonl
(one JSON object per benchmark per run) for tracking over time.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import free_voice as fv

RESULTS = os.path.join(os.path.dirname(__file__), "results.jsonl")
QUICK = False


def _stats(samples):
    s = sorted(samples)
    n = len(s)
    p = lambda q: s[min(n - 1, int(q * n))]
    return {
        "n": n,
        "mean_s": round(statistics.mean(s), 4),
        "p50_s": round(p(0.50), 4),
        "p95_s": round(p(0.95), 4),
    }


def _run(name, component, fn, iterations=1, note=""):
    """Time fn() `iterations` times. Never raises: failures become SKIPPED."""
    rec = {"name": name, "component": component, "note": note,
           "status": "ok"}
    try:
        reason = fn_check(fn)
        if reason:
            rec.update(status="skipped", note=reason)
            return rec
        samples = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - t0)
        rec.update(_stats(samples))
    except Exception as e:  # noqa: BLE001 - a broken bench must not kill the run
        rec.update(status="skipped", note=f"{type(e).__name__}: {e}"[:160])
    return rec


def fn_check(fn):
    """Optional pre-flight: fn.check() returns a skip reason or None."""
    check = getattr(fn, "check", None)
    return check() if check else None


# ---------------------------------------------------------------- benchmarks
def bench_tier0_route():
    corpus = [
        "open notes", "close notes", "quit safari", "switch to mail",
        "set volume to 30", "volume up", "mute", "play", "pause",
        "snap left", "snap right", "maximize window", "minimize",
        "what time is it", "type today's date", "type the time",
        "dictate hello world", "read clipboard", "what's on my screen",
        "click the Reply button", "search cats and dogs",
        "open notes and snap left", "empty trash",
    ]
    for c in corpus:
        fv.route(c, partial=False)
bench_tier0_route.check = lambda: None


def bench_tier0_partial():
    prefixes = ["o", "op", "ope", "open", "open ", "open n", "open no",
                "open not", "open note", "open notes"]
    for p in prefixes:
        fv.route(p, partial=True)
bench_tier0_partial.check = lambda: None


def bench_chain_dispatch():
    with mock.patch.object(fv, "act_open_app", lambda n: None), \
         mock.patch.object(fv, "act_snap_window", lambda s: None), \
         mock.patch.object(fv, "say", lambda t: None), \
         mock.patch("time.sleep"):
        fv.handle_command("open notes and snap left", quiet_miss=True)
bench_chain_dispatch.check = lambda: None


def bench_tier1_cold():
    fv._ollama_ok = None
    r = fv.ollama_route("could you please open notes")
    if r is None and fv._ollama_ok is False:
        raise RuntimeError("ollama_unreachable")
def _check_ollama():
    try:
        subprocess.run(["curl", "-sf", "http://localhost:11434/api/tags"],
                       capture_output=True, timeout=5, check=True)
    except Exception:
        return "Ollama not reachable at localhost:11434"
    return None
bench_tier1_cold.check = _check_ollama


def bench_tier1_warm():
    r = fv.ollama_route("could you please open notes")
    if r is None and fv._ollama_ok is False:
        raise RuntimeError("ollama_unreachable")
bench_tier1_warm.check = _check_ollama


def bench_screenshot():
    path = fv.capture_screenshot()
    if not path:
        raise RuntimeError("screencapture_failed")
    os.unlink(path)
def _check_macos_screen():
    if platform.system() != "Darwin":
        return "needs macOS (screencapture)"
    return None
bench_screenshot.check = _check_macos_screen


def bench_vision_describe():
    with mock.patch.object(fv, "say", lambda t: None):
        fv.act_describe_screen()
def _check_vision():
    s = _check_macos_screen()
    if s:
        return s
    return _check_ollama()
bench_vision_describe.check = _check_vision


def bench_vision_locate():
    path = fv.capture_screenshot()
    if not path:
        raise RuntimeError("screencapture_failed")
    try:
        loc = fv.vision_ask(
            "Find the Apple menu bar clock. Reply with ONLY two integers "
            "X Y as 0-1000 fractions of screen size, or exactly: NONE", path)
        if not loc:
            raise RuntimeError("vision_no_answer")
    finally:
        os.unlink(path)
bench_vision_locate.check = _check_vision


def _synth_wav(seconds=3):
    import wave
    import math
    import struct
    path = os.path.join(tempfile.gettempdir(), "fv_bench.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(16000 * seconds))
        w.writeframes(frames)
    return path


def bench_transcribe():
    wav = _synth_wav()
    fv.transcribe(wav)
def _check_whisper():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "faster-whisper not installed"
    return None
bench_transcribe.check = _check_whisper


def bench_chime():
    fv.play_chime("Tink.aiff")
def _check_macos_audio():
    if platform.system() != "Darwin":
        return "needs macOS (afplay)"
    return None
bench_chime.check = _check_macos_audio


BENCHMARKS = [
    ("tier0_route", "Tier 0 routing", bench_tier0_route, 200,
     "corpus of 23 commands incl. one chained command"),
    ("tier0_partial_gating", "Tier 0 partial gating", bench_tier0_partial, 200,
     "10 growing prefixes of 'open notes'"),
    ("chain_dispatch", "chain dispatch overhead", bench_chain_dispatch, 50,
     "handle_command('open notes and snap left'), executors mocked"),
    ("tier1_cold", "Tier 1 cold (model load)", bench_tier1_cold, 1,
     "first ollama_route call; includes model load into memory"),
    ("tier1_warm", "Tier 1 warm", bench_tier1_warm, 5,
     "subsequent calls, model resident (keep_alive 60m)"),
    ("screenshot_capture", "screenshot capture", bench_screenshot, 5,
     "screencapture to temp file"),
    ("vision_describe_e2e", "vision: describe screen e2e", bench_vision_describe, 3,
     "screenshot + qwen3-vl inference, spoken output mocked"),
    ("vision_locate", "vision: locate element", bench_vision_locate, 3,
     "screenshot + coordinate query inference"),
    ("transcribe_3s", "whisper tiny.en, 3s audio", bench_transcribe, 3,
     "synthetic 440Hz tone; real speech may differ"),
    ("chime", "earcon playback", bench_chime, 5,
     "afplay spawn latency"),
]


def main():
    global QUICK
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="fewer iterations for a smoke run")
    ap.add_argument("--json", action="store_true",
                    help="print raw JSON instead of a Markdown table")
    args = ap.parse_args()
    QUICK = args.quick

    fv.DRY_RUN = False  # benchmarks measure real paths; vision_click not benched
    print(f"# free-voice benchmark — {platform.node()} "
          f"({platform.system()} {platform.machine()}), "
          f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", flush=True)
    print(f"# model: {fv.OLLAMA_MODEL}\n", flush=True)

    records = []
    for name, component, fn, iters, note in BENCHMARKS:
        n = 1 if (args.quick and iters > 1 and "tier1" not in name
                  and "vision" not in name) else iters
        if args.quick and iters >= 50:
            n = 10
        rec = _run(name, component, fn, iterations=n, note=note)
        rec.update({
            "ts": datetime.now(timezone.utc).isoformat(),
            "host": platform.node(),
            "platform": f"{platform.system()} {platform.machine()}",
            "model": fv.OLLAMA_MODEL,
        })
        records.append(rec)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec) + "\n")

    if args.json:
        print(json.dumps(records, indent=2))
        return

    print("| benchmark | n | mean | p50 | p95 | status | note |")
    print("|---|---|---|---|---|---|---|")
    for r in records:
        if r["status"] == "ok":
            print(f"| {r['component']} | {r['n']} | {r['mean_s']}s | "
                  f"{r['p50_s']}s | {r['p95_s']}s | ok | {r['note']} |")
        else:
            print(f"| {r['component']} | — | — | — | — | "
                  f"SKIPPED | {r['note']} |")
    print(f"\nresults appended to {RESULTS}")


if __name__ == "__main__":
    main()
