#!/usr/bin/env python3
"""Wake-word recall and false wakes, through the assistant's REAL speech recognizer, using synthetic speech (Kokoro, several voices) as a proxy.

  recall     : "Mac, <command>" spoken in N voices -> transcribe() -> how often does the parser see a wake word, and what first word did
               the recognizer actually write?  (Which spellings of "Mac" are really needed?)
  false wake : speech WITHOUT the wake word (adversarial non-commands + TV-style lines that start with a name or verb such as "Mike, ...",
               "Make sure ...") -> transcribe() -> how often does the parser still see a wake word?

The same transcripts are scored under three parsers: the current permissive one (10 accepted spellings), a medium one (mac / mack / macs / max / matt),
and a strict one (mac / mack / macs). Synthetic voices are not the user's voice: treat recall as indicative, not exact.
  ./.venv/bin/python benchmarks/wake_eval/bench_wake.py [--n-cmds 60] [--voices 6]
"""
import argparse, collections, json, os, random, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
import numpy as np
from scipy.signal import resample_poly
from common import split_test
import free_voice as fv  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--n-cmds", type=int, default=60); ap.add_argument("--n-non", type=int, default=120)
ap.add_argument("--voices", type=int, default=6); ap.add_argument("--out", default=os.path.join(HERE, "results"))
args = ap.parse_args(); fv.log = lambda *a, **k: None

TV_LINES = ["Mike, turn the volume down, I can't hear myself think", "Mark, close the window before the bugs get in", "Max, play fetch with the ball",
            "Matt, open the door, it's raining", "Make sure you lock the door", "Make it quick", "Match the colors on the left", "Mark my words, you'll regret this",
            "Make some noise for the champions", "Mike, pause the game for a second", "Matt, set an alarm for seven", "Mark, mute the TV", "Max, lock the screen",
            "Mike, search for the best pizza", "Match point, Federer", "Make way for the king", "Max out your credit card and come see me",
            "Mock all you want, I'm still going", "Mark the spot with an X", "Make a timer for ten minutes"]

def parser(allowed):
    greeting = r"(?:(?:hey|hay|hi|hello|ok|okay|yo|ay|ey|and|a|an)[,\s]*)*"
    rx = re.compile(rf"^{greeting}(?:(?:{'|'.join(allowed)})\b[,\s:!\.-]*)+", re.IGNORECASE)
    return lambda t: bool(rx.match(t.strip().lstrip(".-~ \t")))
PARSERS = {"current (10 spellings)": lambda t: fv.parse_wake_word(t, "mac")[0],
           "medium (mac mack macs max matt)": parser(["mac", "mack", "macs", "mac's", "max", "matt"]),
           "strict (mac mack macs)": parser(["mac", "mack", "macs", "mac's"])}

kokoro = fv._get_kokoro(); assert kokoro is not None, "Kokoro not available"
voices = []
for _, v in fv.KOKORO_VOICES.values():
    if v not in voices: voices.append(v)
female = [v for v in voices if v[1] == "f"]; male = [v for v in voices if v[1] == "m"]   # e.g. af_heart / am_fenrir / bf_emma / bm_george
half = max(1, args.voices // 2); voices = female[:half] + male[:half]                      # balanced, so the result is not about one gender
def speak(text, voice):
    s, sr = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
    return resample_poly(np.asarray(s, np.float32), 16000, sr).astype(np.float32)
def hear(text, voice): return fv.transcribe(speak(text, voice)).strip()

rows = split_test(0)[1]; rnd = random.Random(5)
cmds = [r["text"] for r in rnd.sample([r for r in rows if r["is_command"]], args.n_cmds)]
non = [r["text"] for r in rnd.sample([r for r in rows if not r["is_command"]], args.n_non)]
first = lambda t: (re.sub(r"[^a-z']", "", (t.lower().split() or [""])[0]))
res = {"voices": voices, "recall": {}, "false_wake": {}}
t0 = time.time()
print('voices:', voices, flush=True)
rec = []   # (voice, transcript)
for v in voices:
    for c in cmds: rec.append((v, hear("Mac, " + c, v)))
print(f"recall set done: {len(rec)} clips in {time.time()-t0:.0f}s", flush=True)
for name, p in PARSERS.items():
    ok = [p(t) for _, t in rec]
    res["recall"][name] = {"overall": round(100 * sum(ok) / len(ok), 1), "by_voice": {v: round(100 * sum(o for (vv, _), o in zip(rec, ok) if vv == v) / (len(rec) // len(voices)), 1) for v in voices}}
res["recall"]["first_word_written_by_recognizer"] = collections.Counter(first(t) for _, t in rec).most_common(14)
res["recall"]["examples_not_woken_by_current_parser"] = [t for _, t in rec if not PARSERS["current (10 spellings)"](t)][:8]
fw = []   # false-wake clips
for v in (voices[:1] + voices[half:half + 2]):   # one female + two male voices for the false-wake set
    for t in non: fw.append(("non-command", v, hear(t, v)))
    for t in TV_LINES: fw.append(("tv-name-line", v, hear(t, v)))
for kind in ("non-command", "tv-name-line"):
    sub = [t for k, _, t in fw if k == kind]
    res["false_wake"][kind] = {"clips": len(sub), **{name: sum(p(t) for t in sub) for name, p in PARSERS.items()}}
res["false_wake"]["examples_current_parser_woke_on"] = [t for k, _, t in fw if PARSERS["current (10 spellings)"](t)][:10]
json.dump(res, open(os.path.join(args.out, "wake_word.json"), "w"), indent=1)
print("RECALL ('Mac, <command>' through the real recognizer):")
for name in PARSERS: print(f"  {name:34s} {res['recall'][name]['overall']}%   by voice {res['recall'][name]['by_voice']}")
print("  first word the recognizer wrote:", res["recall"]["first_word_written_by_recognizer"])
print("FALSE WAKES (speech without the wake word):")
for kind, d in res["false_wake"].items():
    if isinstance(d, dict): print(f"  {kind:14s} {d['clips']} clips: " + ", ".join(f"{n.split(' (')[0]} {d[n]}" for n in PARSERS))
