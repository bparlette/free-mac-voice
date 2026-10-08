#!/usr/bin/env python3
"""Replay the assistant's own logged utterances (real speech-to-text output, ~/.free-voice/attempts.jsonl) through both embedding backends
and compare what Tier 0.5a would do: ONNX EmbeddingGemma q4 (in-process, the default) vs Ollama embeddinggemma. Only aggregate numbers are
written (the log holds personal speech and is never copied into the repo).
  ./.venv/bin/python benchmarks/finetune_eval/replay_logged_utterances.py [--threshold 0.82]
"""
import argparse, json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import production_examples  # noqa: E402
import free_voice as fv  # noqa: E402
fv.log = lambda *a, **k: None
ap = argparse.ArgumentParser(); ap.add_argument("--threshold", type=float, default=0.82); args = ap.parse_args()

seen, utts = set(), []
for line in open(os.path.expanduser("~/.free-voice/attempts.jsonl")):
    try: u = json.loads(line)["utterance"].strip()
    except Exception: continue
    k = u.lower()
    if not k or k.startswith("(") or k in seen: continue
    seen.add(k); utts.append(u)
ex = production_examples(); texts = [t for t, _ in ex]; acts = [a for _, a in ex]

def run(embed, name):
    def many(ts):
        out = []
        for i in range(0, len(ts), 64): out += list(embed(ts[i:i + 64]))
        M = np.asarray(out, np.float32); return M / np.linalg.norm(M, axis=1, keepdims=True)
    t = time.time(); E = many(texts); Q = many(utts); dt = time.time() - t
    S = Q @ E.T; j = S.argmax(1); return [acts[k] for k in j], S.max(1), dt
a_on, s_on, t_on = run(fv._onnx_embed, "onnx"); a_ol, s_ol, t_ol = run(fv._ollama_embed, "ollama")
th = args.threshold
acc_on, acc_ol = s_on >= th, s_ol >= th
both = acc_on & acc_ol
res = {"unique_utterances": len(utts), "examples": len(texts), "threshold": th,
       "accepted_by_onnx": int(acc_on.sum()), "accepted_by_ollama": int(acc_ol.sum()), "accepted_by_both": int(both.sum()),
       "accepted_by_only_onnx": int((acc_on & ~acc_ol).sum()), "accepted_by_only_ollama": int((~acc_on & acc_ol).sum()),
       "same_action_when_both_accept": int(sum(1 for i in np.where(both)[0] if a_on[i] == a_ol[i])),
       "same_top1_action_all": int(sum(1 for x, y in zip(a_on, a_ol) if x == y)),
       "mean_cosine_onnx": round(float(s_on.mean()), 3), "mean_cosine_ollama": round(float(s_ol.mean()), 3),
       "seconds_onnx": round(t_on, 1), "seconds_ollama": round(t_ol, 1)}
print(json.dumps(res, indent=1)); json.dump(res, open(os.path.join(HERE, "results", "replay_logged_utterances.json"), "w"), indent=1)
