#!/usr/bin/env python3
"""Tier 0.5a as a trained classifier instead of nearest-example matching. Same embeddings (ONNX EmbeddingGemma q4), same held-out half
(seed 0), same scoring as bench_engines.py: Tier 0 regex first, then the embedding tier; 'correct@FA<=16%' = best correct-command rate
over a threshold sweep with at most 16% of non-commands acted on.

Classifier: softmax regression (numpy, L2-regularised) over the router's actions + 'none'.
  A) trained on the leak-free production examples + negatives.txt only (what the live router has)
  B) A plus the OTHER half of the intent test set (more data; the held-out half is never trained on)
  ./.venv/bin/python benchmarks/finetune_eval/bench_classifier.py
"""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from common import *  # noqa: F401,F403
sys.path.insert(0, ROOT)
import free_voice as fv  # noqa: E402
fv.DRY_RUN = True; fv.log = lambda *a, **k: None

train_half, rows = split_test(0)
all_norms = {norm(r["text"]) for r in load_test()}
ex = [(t, a) for t, a in production_examples() if norm(t) not in all_norms]
neg = [l.strip() for l in open(os.path.join(HERE, "negatives.txt")) if l.strip() and norm(l) not in all_norms]
t0s = [(fv.route(r["text"]) or (None,))[0] for r in rows]

def emb(texts):
    out = []
    for i in range(0, len(texts), 64): out += list(fv._onnx_embed(texts[i:i + 64]))
    M = np.asarray(out, np.float32); return M / np.linalg.norm(M, axis=1, keepdims=True)

def train(X, y, n_cls, l2=1e-3, lr=0.5, epochs=400, temp=20.0):
    W = np.zeros((X.shape[1], n_cls), np.float32); b = np.zeros(n_cls, np.float32)
    Y = np.eye(n_cls, dtype=np.float32)[y]
    for _ in range(epochs):
        Z = temp * (X @ W) + b; Z -= Z.max(1, keepdims=True); P = np.exp(Z); P /= P.sum(1, keepdims=True)
        G = (P - Y) / len(X)
        W -= lr * (temp * X.T @ G + l2 * W); b -= lr * G.sum(0)
    return W, b

def predict(W, b, X, temp=20.0):
    Z = temp * (X @ W) + b; Z -= Z.max(1, keepdims=True); P = np.exp(Z); return P / P.sum(1, keepdims=True)

def score(acts, confs, grid):
    nc = sum(r["is_command"] for r in rows); nn = len(rows) - nc; best = (0, None, 0)
    for th in grid:
        right = fa = 0
        for r, a, c, a0 in zip(rows, acts, confs, t0s):
            act = a0 if a0 else (a if (c >= th and a != "none") else None)
            if not act: continue
            if r["is_command"]: right += act in ACCEPT[r["intent"]]
            else: fa += 1
        if fa / nn <= 0.16 and right / nc > best[0]: best = (right / nc, th, fa / nn)
    return {"correct_pct": round(100 * best[0], 1), "threshold": best[1], "false_accept_pct": round(100 * best[2], 1)}

Q = emb([r["text"] for r in rows]); out = {"heldout_rows": len(rows), "examples": len(ex), "negatives": len(neg)}
E = emb([t for t, _ in ex]); S = Q @ E.T
out["nearest_example (live)"] = score([ex[j][1] for j in S.argmax(1)], S.max(1), np.arange(0.40, 0.985, 0.005))
for name, extra in (("classifier A (examples + negatives)", []),
                    ("classifier B (+ other half of test set)", [(r["text"], PRIMARY[r["intent"]] if r["is_command"] else "none") for r in train_half])):
    data = ex + [(t, "none") for t in neg] + extra
    labels = sorted({a for _, a in data}); idx = {a: i for i, a in enumerate(labels)}
    X = emb([t for t, _ in data]); y = np.array([idx[a] for _, a in data])
    t = time.time(); W, b = train(X, y, len(labels)); tt = time.time() - t
    P = predict(W, b, Q); j = P.argmax(1)
    out[name] = score([labels[k] for k in j], P.max(1), np.arange(0.05, 1.0, 0.01)) | {"train_rows": len(data), "train_s": round(tt, 1)}
print(json.dumps(out, indent=1)); json.dump(out, open(os.path.join(HERE, "results", "classifier_vs_nearest.json"), "w"), indent=1)
