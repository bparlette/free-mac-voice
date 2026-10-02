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
    if r is None:
        if fv._ollama_ok is False:
            raise RuntimeError("ollama_unreachable")
        raise RuntimeError("tier1_route_failed")
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
    if r is None:
        if fv._ollama_ok is False:
            raise RuntimeError("ollama_unreachable")
        raise RuntimeError("tier1_route_failed")
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
        fv.act_describe_screen_vlm()
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


def _make_speech_audio():
    """Return a float32 numpy array of real TTS speech (macOS) or a synthetic
    signal (non-mac / CI). faster-whisper accepts float32 numpy arrays directly,
    bypassing av.open entirely (works with av 19 which dropped metadata_errors)."""
    import numpy as np
    if platform.system() == "Darwin":
        # Use macOS `say` + `afconvert` to generate real speech audio
        import wave, tempfile
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff = f.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            subprocess.run(
                ["say", "-o", aiff, "could you please open notes for me"],
                check=True, capture_output=True)
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", aiff, wav_path],
                check=True, capture_output=True)
            with wave.open(wav_path, "rb") as w:
                raw = w.readframes(w.getnframes())
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            pass  # fall through to synthetic
        finally:
            for p in (aiff, wav_path):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
    # Synthetic fallback: multi-harmonic signal to survive VAD filter
    import math
    n, sr = 16000 * 2, 16000
    t = [i / sr for i in range(n)]
    sig = [(0.5 * math.sin(2 * math.pi * 150 * x) +
            0.3 * math.sin(2 * math.pi * 300 * x) +
            0.2 * math.sin(2 * math.pi * 450 * x)) for x in t]
    return np.array(sig, dtype=np.float32)


def bench_transcribe():
    audio = _make_speech_audio()
    from faster_whisper import WhisperModel
    if fv._whisper is None:
        fv._whisper = WhisperModel(fv.WHISPER_MODEL, device="auto", compute_type="int8")
    segs, _ = fv._whisper.transcribe(audio, beam_size=1, vad_filter=True)
    _ = list(segs)  # consume generator — timing includes full decode


def _check_whisper():
    try:
        import faster_whisper  # noqa: F401
        import numpy as np    # noqa: F401
    except ImportError as e:
        return f"missing dependency: {e}"
    return None
bench_transcribe.check = _check_whisper


def bench_chime():
    fv.play_chime("Tink.aiff")
def _check_macos_audio():
    if platform.system() != "Darwin":
        return "needs macOS (afplay)"
    return None
bench_chime.check = _check_macos_audio


def bench_quartz_summary():
    s = fv.quartz_window_summary()
    if s is None:
        raise RuntimeError("quartz_summary_failed")
def _check_quartz():
    if platform.system() != "Darwin":
        return "needs macOS (Quartz)"
    if fv._load_quartz() is None:
        return "Quartz framework unavailable"
    return None
bench_quartz_summary.check = _check_quartz


def bench_ocr_locate():
    path = fv.capture_screenshot(fresh=True)
    if not path:
        raise RuntimeError("screencapture_failed")
    try:
        fv.ocr_locate("File", path)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
def _check_ocr():
    if platform.system() != "Darwin":
        return "needs macOS (Vision)"
    v, _ = fv._load_vision_framework()
    if v is None:
        return "Vision framework unavailable"
    return None
bench_ocr_locate.check = _check_ocr


def bench_ocr_locate_cached():
    path = fv.capture_screenshot(fresh=False)
    if not path:
        raise RuntimeError("screencapture_failed")
    fv.ocr_locate("File", path)
bench_ocr_locate_cached.check = _check_ocr


def bench_tier0_click_e2e():
    with mock.patch.object(fv, "_click_xy", return_value=True), \
         mock.patch.object(fv, "say", lambda t: None):
        fv.handle_command("click on the File button", quiet_miss=True)
bench_tier0_click_e2e.check = _check_ocr


BENCHMARKS = [
    ("tier0_route", "Tier 0 routing", bench_tier0_route, 200,
     "corpus of 23 commands incl. one chained command"),
    ("tier0_partial_gating", "Tier 0 partial gating", bench_tier0_partial, 200,
     "10 growing prefixes of 'open notes'"),
    ("chain_dispatch", "chain dispatch overhead", bench_chain_dispatch, 50,
     "handle_command('open notes and snap left'), executors mocked"),
    ("tier0_click_e2e", "Tier 0 click e2e", bench_tier0_click_e2e, 10,
     "Tier 0 regex -> xa11y -> cached OCR fast path -> click"),
    ("quartz_window_summary", "Quartz window summary", bench_quartz_summary, 20,
     "CGWindowListCopyWindowInfo layer-0 parse + spoken summary (no screenshot)"),
    ("ocr_locate_fresh", "Apple Vision OCR (fresh)", bench_ocr_locate, 5,
     "fresh screencapture + Fast VNRecognizeTextRequest locate"),
    ("ocr_locate_cached", "Apple Vision OCR (cached)", bench_ocr_locate_cached, 5,
     "8s cached screenshot reuse + Fast VNRecognizeTextRequest locate"),
    ("tier1_cold", "Tier 1 cold (model load)", bench_tier1_cold, 1,
     "first ollama_route call; includes model load into memory"),
    ("tier1_warm", "Tier 1 warm", bench_tier1_warm, 5,
     "subsequent calls, model resident (keep_alive 60m)"),
    ("screenshot_capture", "screenshot capture", bench_screenshot, 5,
     "screencapture to temp file"),
    ("vision_describe_e2e", "vision: describe screen e2e", bench_vision_describe, 3,
     "screenshot + qwen3-vl inference (800px), spoken output mocked"),
    ("vision_locate", "vision: locate element", bench_vision_locate, 3,
     "screenshot + coordinate query inference (800px)"),
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
