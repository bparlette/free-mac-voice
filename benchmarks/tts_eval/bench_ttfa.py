#!/usr/bin/env python3
"""Time to first spoken audio for replies of different lengths (no playback, no microphone needed).

The assistant currently synthesizes the WHOLE reply with Kokoro and only then starts playing it (free_voice._synthesize_kokoro + afplay),
so the wait before the first word grows with reply length. This measures that wait, and compares it with synthesizing only the first
sentence (what sentence-by-sentence streaming would give) and with macOS `say`.
  ./.venv/bin/python benchmarks/tts_eval/bench_ttfa.py
"""
import json, os, re, statistics, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
from kokoro_onnx import Kokoro

d = os.path.expanduser("~/.config/free-voice/models/kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
VOICE = "af_heart"
BASE = ("The meeting is at three in the afternoon, so you have about two hours to finish the report. "
        "I would start with the summary because everyone reads that first. "
        "After that, check the numbers in the table against the spreadsheet, since a mismatch is the easiest thing to get caught on. "
        "If there is time left, add a short paragraph about next steps and send it to the team before the call starts. ")
words = BASE.split()
CASES = {n: " ".join((words * (n // len(words) + 1))[:n]).rstrip(",") + "." for n in (6, 20, 50, 100, 200)}
first_sentence = lambda t: re.split(r"(?<=[.!?])\s+", t.strip())[0]

def synth(text):
    t = time.time(); s, sr = k.create(text, voice=VOICE, speed=1.0, lang="en-us"); return time.time() - t, len(s) / sr

synth("warm up")
out = {}
for n, text in CASES.items():
    full = [synth(text) for _ in range(3)]
    first = [synth(first_sentence(text)) for _ in range(3)]
    out[n] = {"words": n, "audio_s": round(full[0][1], 1), "whole_reply_synth_s": round(statistics.median(x[0] for x in full), 2),
              "first_sentence_synth_s": round(statistics.median(x[0] for x in first), 2), "first_sentence_audio_s": round(first[0][1], 1)}
    print(out[n])
json.dump({"voice": VOICE, "note": "median of 3, Kokoro ONNX on CPU; machine was also running other jobs", "cases": out}, open(os.path.join(HERE, "results", "time_to_first_audio.json"), "w"), indent=1)
