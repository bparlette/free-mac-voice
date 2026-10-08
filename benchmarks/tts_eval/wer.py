#!/usr/bin/env python3
"""Intelligibility check: transcribe each synthesized wav with Whisper and compute word error rate against the input text.
Same recogniser for every engine, so the comparison is fair; absolute numbers are inflated by formatting differences
(Whisper writes "$389" and "3:45 PM", the reference says it out loud), so only compare engines to each other.

  ./.venv/bin/python benchmarks/tts_eval/wer.py [/tmp/tts_samples] [base.en]
"""
import glob, json, os, re, sys

d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tts_samples"
model_name = sys.argv[2] if len(sys.argv) > 2 else "base.en"
HERE = os.path.dirname(os.path.abspath(__file__))
refs = [l.strip() for l in open(os.path.join(HERE, "sentences.txt")) if l.strip()]
import soundfile as sf
from scipy.signal import resample_poly
from faster_whisper import WhisperModel
model = WhisperModel(model_name, device="cpu", compute_type="int8")

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def num_words(n):
    if n < 20: return ONES[n]
    if n < 100: return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000: return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + num_words(n % 100))
    return str(n)


def norm(s):
    s = s.lower().replace("$", " dollars ").replace("%", " percent ").replace("zoom dot us", "zoom.us")
    s = re.sub(r"(\d+):(\d+)", lambda m: f"{m.group(1)} {m.group(2)}", s)
    s = re.sub(r"\d+", lambda m: num_words(int(m.group(0))), s)
    s = re.sub(r"\b(\w+?)(st|nd|rd|th)\b", lambda m: m.group(0) if not m.group(1).isalpha() else m.group(0), s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return s.split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev = cur
    return d[len(h)], len(r)


engines = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(os.path.join(d, "*_[0-9][0-9].wav"))})
out = {}
for e in engines:
    errs = words = 0; per = []
    for i, ref in enumerate(refs, 1):
        wav = os.path.join(d, f"{e}_{i:02d}.wav")
        audio, sr = sf.read(wav, dtype="float32")
        if audio.ndim > 1: audio = audio.mean(axis=1)
        audio = resample_poly(audio, 16000, sr).astype("float32")  # decode ourselves: faster-whisper's loader needs a newer `av`
        segs, _ = model.transcribe(audio, language="en", beam_size=5)
        hyp = " ".join(s.text.strip() for s in segs)
        n, w = wer(ref, hyp); errs += n; words += w; per.append((i, n, w, hyp))
    out[e] = {"wer_percent": round(100 * errs / words, 1), "errors": errs, "words": words, "worst": sorted(per, key=lambda x: -x[1] / x[2])[:2]}
    print(f"{e:20s} WER {out[e]['wer_percent']:5.1f}%  ({errs} errors / {words} words)")
    for i, n, w, hyp in out[e]["worst"]:
        if n: print(f"    #{i} {n}/{w} errors: heard {hyp!r}")
json.dump(out, open(os.path.join(d, "wer.json"), "w"), indent=1)
