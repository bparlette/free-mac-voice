#!/usr/bin/env python3
"""Decision model as a *re-ranker* behind the embedding tier. The decision API (/v1/systemone) accepts only 2-26 options per `choice`
question, so it cannot choose among all ~46 actions at once. Design tested here: the embedding tier shortlists the top-K candidate
actions (leak-free examples), the decision model picks one of them or 'none' with a calibrated probability.
Same 60 held-out commands / 60 adversarial non-commands as bench_qwen.py (seed 7).

  ./.venv/bin/python benchmarks/model_size_eval/bench_decision_router.py tev1:0.8b --shortlist 8
"""
import argparse, json, os, random, statistics, sys, time, urllib.request
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test, ACCEPT, norm, load_test, production_examples  # noqa: E402
import free_voice as fv  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("model"); ap.add_argument("--host", default=fv.OLLAMA_HOST)
ap.add_argument("--shortlist", type=int, default=8); ap.add_argument("--out", default=os.path.join(HERE, "results"))
args = ap.parse_args()
ACC = {k: set(v) for k, v in ACCEPT.items()}; ACC["set_volume"] |= {"volume_up", "volume_down"}; ACC["mute"] |= {"tv_mute"}
for k in ("media_play_pause", "next_track"): ACC[k] |= {"media"}

test_norms = {norm(r["text"]) for r in load_test()}
ex = [(t, a) for t, a in production_examples() if norm(t) not in test_norms]          # leak-free examples
E = fv._onnx_embed([t for t, _ in ex]); ex_actions = [a for _, a in ex]
assert E is not None, "ONNX embedder unavailable"

def shortlist(text, k):
    q = fv._onnx_embed([text])[0]; sims = E @ q; best = {}
    for s, a in zip(sims, ex_actions): best[a] = max(best.get(a, -1), float(s))
    ranked = sorted(best.items(), key=lambda kv: -kv[1]); return ranked[:k], ranked[0]

def decide(text, cands):
    criteria = {a: (fv._DECISION_CRITERIA.get(a) or a.replace("_", " ")) for a, _ in cands}
    criteria["none"] = "Not a command for the computer: conversation, narration, TV or podcast dialogue, or a question about something else"
    body = {"model": args.model, "state": text, "questions": {"action": {"type": "choice", "instructions": "Which action is the speaker asking the computer to do right now?", "criteria": criteria}}}
    req = urllib.request.Request(args.host + "/v1/systemone", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter(); d = json.load(urllib.request.urlopen(req, timeout=60)); dt = time.perf_counter() - t
    a = d["answers"]["action"]; return a["choice"], float(a["probabilities"].get(a["choice"], 0)), dt

rows = split_test(0)[1]; rnd = random.Random(7)
cmds = rnd.sample([r for r in rows if r["is_command"]], 60); non = rnd.sample([r for r in rows if not r["is_command"]], 60)
decide("warm up", [("open_app", 1.0)])
cm = []; nm = []; lat = []
for r in cmds:
    sl, top = shortlist(r["text"], args.shortlist); a, p, dt = decide(r["text"], sl); lat.append(dt); cm.append((r, top, a, p, any(x in ACC[r["intent"]] for x, _ in sl)))
for r in non:
    sl, top = shortlist(r["text"], args.shortlist); a, p, dt = decide(r["text"], sl); lat.append(dt); nm.append((r, top, a, p))
print(f"shortlist top-{args.shortlist} contains the right action for {sum(c[4] for c in cm)}/60 commands (the ceiling for the decision step)")
print(f"{args.model} decision call: median {1000*statistics.median(lat):.0f} ms")
res = {"model": args.model, "shortlist": args.shortlist, "ceiling_correct_in_shortlist": sum(c[4] for c in cm), "median_ms": round(1000 * statistics.median(lat)), "embedding_only": {}, "with_decision_model": {}}
print("\nembedding tier alone (top-1, cosine threshold):")
for th in (0.74, 0.78, 0.82, 0.86):
    right = sum(1 for r, top, *_ in cm if top[1] >= th and top[0] in ACC[r["intent"]]); wrong = sum(1 for r, top, *_ in cm if top[1] >= th and top[0] not in ACC[r["intent"]])
    fa = sum(1 for r, top, *_ in nm if top[1] >= th); res["embedding_only"][th] = [right, wrong, fa]
    print(f"  cos>={th:.2f}: {right}/60 correct, {wrong} wrong | {fa}/60 non-commands acted on")
print("\nembedding shortlist + decision model (probability threshold):")
for th in (0.0, 0.5, 0.7, 0.85):
    right = sum(1 for r, top, a, p, _ in cm if a != "none" and p >= th and a in ACC[r["intent"]]); wrong = sum(1 for r, top, a, p, _ in cm if a != "none" and p >= th and a not in ACC[r["intent"]])
    none = 60 - right - wrong; fa = sum(1 for r, top, a, p in nm if a != "none" and p >= th); res["with_decision_model"][th] = [right, wrong, fa]
    print(f"  p>={th:.2f}: {right}/60 correct, {wrong} wrong, {none} none | {fa}/60 non-commands acted on")
json.dump(res, open(os.path.join(args.out, f"decision_shortlist{args.shortlist}_{args.model.replace(':','_')}.json"), "w"), indent=1, default=str)
