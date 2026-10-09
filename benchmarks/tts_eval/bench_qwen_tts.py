#!/usr/bin/env python3
"""Qwen3-TTS (Base = voice cloning from a short reference clip) on this Mac via mlx-audio, run in a throwaway uv environment.
Measures load time, time to first audio, real-time factor, memory, and how well Whisper (the assistant's recognizer) understands the output.
Reference voice here is a SYNTHETIC Kokoro clip (no real person's voice is used or stored), so speaker similarity is NOT measured: only speed,
memory and intelligibility.  Run (the script re-launches itself inside uv):
  ./.venv/bin/python benchmarks/tts_eval/bench_qwen_tts.py [--model mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit]
"""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
if "--inner" not in sys.argv:
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
    sys.exit(subprocess.call(["uv", "run", "--no-project", "--python", "3.12", "--with", "mlx-audio", "--with", "soundfile", "python", __file__, "--inner", "--model", model]))
import glob, resource, shutil, time, types
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))   # mlx-audio imports it for playback; this script never plays audio, and a hung CoreAudio would block the import
import numpy as np, soundfile as sf
model_id = sys.argv[sys.argv.index("--model") + 1]
SENT = ["Opening your notes now.", "The meeting is at three in the afternoon, so you have about two hours left to finish the report.",
        "I would start with the summary because everyone reads that first. After that, check the numbers in the table against the spreadsheet."]
from mlx_audio.tts.utils import load_model
from mlx_audio.tts.generate import generate_audio
t = time.time(); model = load_model(model_id); load_s = time.time() - t
ref = "/tmp/qtts/ref.wav"; out = []
for i, text in enumerate([SENT[0]] + SENT):
    t = time.time(); prefix = f"/tmp/qtts/out_{i}"
    generate_audio(model=model, text=text, ref_audio=ref, ref_text="Hi, this is a short sample of my voice for the assistant to copy. I speak at a normal pace.", file_prefix=prefix, verbose=False)
    dt = time.time() - t; f = sorted(glob.glob(prefix + "*.wav"))[0]; a, sr = sf.read(f)
    if i: out.append({"words": len(text.split()), "synth_s": round(dt, 2), "audio_s": round(len(a) / sr, 2), "rtf": round(dt / (len(a) / sr), 2), "wav": f})
res = {"model": model_id, "load_s": round(load_s, 1), "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30, 2), "clips": out}
print(json.dumps(res))
