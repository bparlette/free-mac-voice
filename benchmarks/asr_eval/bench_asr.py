#!/usr/bin/env python3
"""Speech recognizers compared on the assistant's own commands: Whisper base.en (live) vs Apple SpeechAnalyzer (macOS 26, built in)
vs NVIDIA Parakeet TDT 0.6B v3 (parakeet-mlx, run in a throwaway uv environment, nothing installed into the project).

Audio: held-out commands (intent set, seed 0) spoken by Kokoro in 4 voices (2 female, 2 male), 16 kHz. Synthetic speech is a proxy
for a real voice: it is clean and well articulated, so absolute error rates are optimistic; the ranking is what matters.
Scores: word error rate (lower-case, punctuation removed, numbers spelled out) and whether the router (Tier 0 regex, then the
embedding tier, exactly as live) still picks an acceptable action from the transcript.
  ./.venv/bin/python benchmarks/asr_eval/bench_asr.py [--n 80]   (needs /tmp/speech_analyzer: xcrun swiftc -O -parse-as-library -o /tmp/speech_analyzer benchmarks/asr_eval/speech_analyzer.swift)
"""
import argparse, json, os, random, re, statistics, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
from common import split_test, ACCEPT  # noqa: E402
import free_voice as fv  # noqa: E402
fv.DRY_RUN = True; fv.log = lambda *a, **k: None
ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=80); args = ap.parse_args()

WORK = "/tmp/asr_bench"; os.makedirs(WORK, exist_ok=True)
rows = random.Random(3).sample([r for r in split_test(0)[1] if r["is_command"]], args.n)
voices = ["af_heart", "af_bella", "am_fenrir", "am_michael"]
k = fv._get_kokoro(); clips = []
for v in voices:
    for i, r in enumerate(rows):
        s, sr = k.create(r["text"], voice=v, speed=1.0, lang="en-us")
        p = f"{WORK}/{v}_{i}.wav"; sf.write(p, resample_poly(np.asarray(s, np.float32), 16000, sr).astype(np.float32), 16000, subtype="PCM_16")
        clips.append({"path": p, "voice": v, "ref": r["text"], "intent": r["intent"]})
print(f"{len(clips)} clips", flush=True)

hyp = {"whisper base.en (live)": {}, "Apple SpeechAnalyzer": {}, "Parakeet TDT 0.6B v3": {}}; ms = {n: [] for n in hyp}
fv.transcribe(np.zeros(16000, np.float32))
for c in clips:
    a, _ = sf.read(c["path"], dtype="float32"); t = time.time()
    hyp["whisper base.en (live)"][c["path"]] = fv.transcribe(a); ms["whisper base.en (live)"].append(1000 * (time.time() - t))
paths = "\n".join([clips[0]["path"]] + [c["path"] for c in clips]) + "\n"   # first clip twice: warm-up
for name, cmd in (("Apple SpeechAnalyzer", ["/tmp/speech_analyzer"]),
                  ("Parakeet TDT 0.6B v3", ["uv", "run", "--no-project", "--python", "3.12", "--with", "parakeet-mlx", "python", "/tmp/parakeet_run.py"])):
    out = [json.loads(l) for l in subprocess.run(cmd, input=paths, capture_output=True, text=True, check=True).stdout.splitlines() if l.startswith("{")][1:]
    for o in out: hyp[name][o["path"]] = o["text"]; ms[name].append(o["ms"])

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
def num(n):
    n = int(n)
    if n < 20: return ONES[n]
    if n < 100: return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n == 100: return "one hundred"
    return str(n)
def words(t):
    t = re.sub(r"(\d+)\s*%", lambda m: num(m.group(1)) + " percent", t.lower())
    t = re.sub(r"\d+", lambda m: num(m.group(0)), t)
    return re.sub(r"[^a-z' ]+", " ", t.replace("-", " ")).split()
def wer(r, h):
    r, h = words(r), words(h); d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)], len(r)
def action(t):
    x = fv.route(t)
    if x: return x[0]
    m = fv.tier05_embed_match(t, threshold=0.75)
    return m[0] if m else None

ref_ok = sum(action(c["ref"]) in ACCEPT[c["intent"]] for c in clips)
res = {"clips": len(clips), "voices": voices, "router_correct_on_reference_text": f"{ref_ok}/{len(clips)}", "engines": {}}
for name in hyp:
    e = n = ok = exact = 0; bad = []
    for c in clips:
        h = hyp[name].get(c["path"], ""); de, dn = wer(c["ref"], h); e += de; n += dn; exact += de == 0
        good = action(h) in ACCEPT[c["intent"]]; ok += good
        if de and len(bad) < 8: bad.append([c["ref"], h])
    res["engines"][name] = {"wer_pct": round(100 * e / n, 1), "exact_transcripts": f"{exact}/{len(clips)}", "router_correct": f"{ok}/{len(clips)}",
                            "median_ms_per_clip": round(statistics.median(ms[name])), "missing": len(clips) - len(hyp[name]), "examples_of_errors": bad}
print(json.dumps(res, indent=1)); json.dump(res, open(os.path.join(HERE, "results", "asr_engines.json"), "w"), indent=1)
