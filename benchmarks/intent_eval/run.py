#!/usr/bin/env python3
"""Score the router on testset.jsonl (400 commands + 400 adversarial non-commands).

    ./.venv/bin/python benchmarks/intent_eval/run.py [--threshold 0.70 0.74 0.78 0.82] [--gate]

Nothing is executed: only route() (Tier 0 regex), tier05_embed_match() (Tier 0.5a
embeddings) and, with --gate, is_voice_command() (the gate model) are called.
Reports per-stage command recall and non-command false-accept rate, and writes
misses/false accepts to benchmarks/intent_eval/last_run.json.
"""
import argparse, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import free_voice as fv  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--threshold", type=float, nargs="*", default=[0.70, 0.74, 0.78, 0.82, 0.86])
ap.add_argument("--allow-hashing", action="store_true", help="report even if neural embeddings are unavailable")
ap.add_argument("--gate", action="store_true", help="also run the Ollama command gate (slow)")
args = ap.parse_args()

rows = [json.loads(l) for l in open(os.path.join(HERE, "testset.jsonl")) if l.strip()]
fv.DRY_RUN = True
fv.log = lambda *a, **k: None  # silence per-call logging


def t0(text):
    r = fv.route(text)
    return r[0] if r else None


import numpy as np  # noqa: E402
fv._init_intent_embeddings()
if not fv._EMBED_NEURAL and fv.OLLAMA_EMBED_MODEL and not args.allow_hashing:
    sys.exit(f"Ollama embeddings ({fv.OLLAMA_EMBED_MODEL}) unavailable: numbers would be from the legacy hashing "
             "fallback, not production. Start Ollama / pull the model, or pass --allow-hashing.")
print(f"Tier 0.5a mode: {('neural ' + ('ONNX EmbeddingGemma' if fv._use_onnx() else fv.OLLAMA_EMBED_MODEL)) if fv._EMBED_NEURAL else 'HASHING fallback'}; "
      f"{len(rows)} phrases\n")


def sims(text):
    """Best (action, cosine) over the example matrix, same query path as tier05_embed_match."""
    qv = None
    if fv._EMBED_NEURAL:
        q = fv._onnx_embed([text]) if fv._use_onnx() else fv._ollama_embed([text], timeout=10)
        qv = q[0] if q is not None else None
    else:
        qv = fv.embed_utterance(text)
    if qv is None:
        return None, 0.0
    s = np.dot(fv._PRECOMPUTED_MATRIX, qv)
    i = int(np.argmax(s))
    return fv._PRECOMPUTED_INTENTS[i][0], float(s[i])


res = []
for r in rows:
    text = r["text"]
    a, s = sims(text)
    res.append({**r, "t0": t0(text), "e_action": a, "e_sim": s})

cmds = [r for r in res if r["is_command"]]
non = [r for r in res if not r["is_command"]]
pct = lambda a, b: f"{100 * a / max(b, 1):5.1f}%"

n_t0_c = sum(1 for r in cmds if r["t0"])
n_t0_n = sum(1 for r in non if r["t0"])
print(f"Tier 0 regex          : commands matched {n_t0_c:3d}/{len(cmds)} ({pct(n_t0_c, len(cmds))})   "
      f"non-commands matched {n_t0_n:3d}/{len(non)} ({pct(n_t0_n, len(non))})")
print("\nTier 0.5a embeddings (matches that Tier 0 did NOT already catch):")
print("  thr    cmd recall (t0 + 0.5a)   non-cmd false accepts")
for th in args.threshold:
    rc = sum(1 for r in cmds if r["t0"] or r["e_sim"] >= th)
    fa = sum(1 for r in non if r["e_sim"] >= th)
    print(f" {th:.2f}   {rc:3d}/{len(cmds)} {pct(rc, len(cmds))}            {fa:3d}/{len(non)} {pct(fa, len(non))}")

by = collections.defaultdict(lambda: [0, 0])
for r in cmds:
    by[r["intent"]][1] += 1
    by[r["intent"]][0] += bool(r["t0"] or r["e_sim"] >= fv.TIER05_EMBED_THRESHOLD)
print(f"\nPer-intent command recall at the production threshold ({fv.TIER05_EMBED_THRESHOLD}):")
for k, (a, b) in sorted(by.items(), key=lambda kv: kv[1][0] / kv[1][1]):
    print(f"  {k:18s} {a:3d}/{b:3d} {pct(a, b)}")

fa_prod = [r for r in non if r["e_sim"] >= fv.TIER05_EMBED_THRESHOLD]
print(f"\nNon-commands wrongly accepted by Tier 0.5a at {fv.TIER05_EMBED_THRESHOLD}: {len(fa_prod)}")
for r in sorted(fa_prod, key=lambda r: -r["e_sim"])[:15]:
    print(f"  {r['e_sim']:.2f} {r['e_action']:14s} {r['text']!r}")
fa_t0 = [r for r in non if r["t0"]]
print(f"\nNon-commands wrongly matched by Tier 0 regex: {len(fa_t0)}")
for r in fa_t0[:25]:
    print(f"  {r['t0']:18s} {r['text']!r}")

if args.gate:
    ok = fp = 0
    gate_fa = []
    for r in res:
        isc, _ = fv.is_voice_command(r["text"])
        if r["is_command"]:
            ok += isc
        elif isc:
            fp += 1
            gate_fa.append(r["text"])
    print(f"\nCommand gate: commands passed {ok}/{len(cmds)} ({pct(ok, len(cmds))}); "
          f"non-commands passed {fp}/{len(non)} ({pct(fp, len(non))})")

json.dump({"mode": "neural" if fv._EMBED_NEURAL else "hashing",
           "tier0_false": [r["text"] for r in fa_t0],
           "t05_false": [[r["text"], r["e_action"], r["e_sim"]] for r in fa_prod],
           "missed_commands": [[r["text"], r["intent"], r["e_action"], round(r["e_sim"], 3)]
                               for r in cmds if not (r["t0"] or r["e_sim"] >= fv.TIER05_EMBED_THRESHOLD)]},
          open(os.path.join(HERE, "last_run.json"), "w"), indent=1)
