#!/usr/bin/env python3
"""Compare the production router (Tier 0 regex + Tier 0.5a embeddings) with a LoRA fine-tuned small LM.
Metric = picked the CORRECT action (not just 'matched something'), plus non-command false accepts.

  ./.venv/bin/python benchmarks/finetune_eval/eval.py --adapter /tmp/ft/adA [--model ...] [--split heldout|all] [--baseline]
"""
import argparse, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="mlx-community/Qwen2.5-0.5B-Instruct-4bit")
ap.add_argument("--adapter", action="append", default=[])
ap.add_argument("--split", choices=["heldout", "all"], default="heldout")
ap.add_argument("--baseline", action="store_true")
ap.add_argument("--eg2", action="store_true", help="EmbeddingGemma 2 q4 ONNX as a drop-in for the router's embedding model (needs --baseline; files in /tmp/eg2)")
ap.add_argument("--laya", action="store_true", help="zero-shot Laya decision model (pip install --target /tmp/laya_pkgs laya)")
ap.add_argument("--laya-device", default="cpu")
ap.add_argument("--laya-model", default="english", help="single Laya checkpoint: english | multilingual")
ap.add_argument("--out", default="/tmp/ft/results.json")
args = ap.parse_args()

rows = split_test(0)[1] if args.split == "heldout" else load_test()
print(f"{len(rows)} test rows ({args.split}): {sum(r['is_command'] for r in rows)} commands, {sum(not r['is_command'] for r in rows)} non-commands")
results = {}

def ok(r, action): return r["is_command"] and action in ACCEPT[r["intent"]]

def report(name, preds, thresholds):
    """preds: per row (action_or_None, confidence). Returns table rows."""
    cmds = [(r, p) for r, p in zip(rows, preds) if r["is_command"]]
    non = [(r, p) for r, p in zip(rows, preds) if not r["is_command"]]
    print(f"\n{name}\n  thr    correct-command   wrong-command   missed   non-cmd false accepts")
    tab = []
    for th in thresholds:
        acc = lambda a, c: a not in (None, "none") and c >= th
        right = sum(1 for r, (a, c) in cmds if acc(a, c) and ok(r, a))
        wrong = sum(1 for r, (a, c) in cmds if acc(a, c) and not ok(r, a))
        fa = sum(1 for r, (a, c) in non if acc(a, c))
        tab.append({"thr": th, "correct": right, "wrong": wrong, "missed": len(cmds) - right - wrong, "false_accept": fa})
        print(f"  {th:.2f}   {right:3d}/{len(cmds)} {100*right/len(cmds):5.1f}%     {wrong:3d}          {len(cmds)-right-wrong:3d}      {fa:3d}/{len(non)} {100*fa/len(non):4.1f}%")
    results[name] = {"n_cmd": len(cmds), "n_non": len(non), "table": tab}

if args.baseline:
    import numpy as np
    sys.path.insert(0, ROOT)
    import free_voice as fv
    fv.DRY_RUN = True; fv.log = lambda *a, **k: None
    fv._init_intent_embeddings()
    assert fv._EMBED_NEURAL, "neural embeddings unavailable; start Ollama"
    def t0(text):
        r = fv.route(text); return r[0] if r else None
    embs = []
    for i in range(0, len(rows), 50):
        embs.extend(list(fv._ollama_embed([r["text"] for r in rows[i:i + 50]], timeout=60)))
    es = []
    for q in embs:
        s = np.dot(fv._PRECOMPUTED_MATRIX, q); j = int(np.argmax(s)); es.append((fv._PRECOMPUTED_INTENTS[j][0], float(s[j])))
    t0s = [t0(r["text"]) for r in rows]
    report("baseline AS DEPLOYED (examples include 75 phrases that are also in the test set: inflated)", [((a0, 1.0) if a0 else (a, s)) for a0, (a, s) in zip(t0s, es)], [0.78, 0.82, 0.86])
    # leak-free: same embedding model, examples that do not appear in the test set
    all_norms = {norm(r["text"]) for r in load_test()}
    clean = [(t, a) for t, a in production_examples() if norm(t) not in all_norms]
    cv = fv._ollama_embed([t for t, _ in clean], timeout=120)
    M = np.array(cv); M = M / np.linalg.norm(M, axis=1, keepdims=True)
    les = []
    for q in embs:
        q = np.array(q); q = q / np.linalg.norm(q); sc = M @ q; j = int(np.argmax(sc)); les.append((clean[j][1], float(sc[j])))
    results["_leakfree_es"] = les
    report("baseline LEAK-FREE: Tier0 + Tier0.5a (fair comparison)", [((a0, 1.0) if a0 else (a, s)) for a0, (a, s) in zip(t0s, les)], [0.70, 0.74, 0.78, 0.82, 0.86])

    if args.eg2:
        import resource, time, onnxruntime as ort
        from tokenizers import Tokenizer
        rssg = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30
        r0 = rssg()
        sess = ort.InferenceSession("/tmp/eg2/onnx/model_q4.onnx", providers=["CPUExecutionProvider"])
        tk = Tokenizer.from_file("/tmp/eg2/tokenizer.json")
        z = lambda n: np.zeros((0, 512), np.float32)
        def emb(text, prefix):
            ids = np.array([tk.encode(prefix + text).ids], np.int64)
            v = sess.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": np.ones_like(ids), "image_features": z(0), "video_features": z(0), "audio_features": z(0)})[0][0]
            return v / np.linalg.norm(v)
        for prefix in ["", "task: classification | query: "]:
            tag = "prefix" if prefix else "no prefix"
            Mx = np.stack([emb(t, prefix) for t, _ in clean])
            lat, es2 = [], []
            for r in rows:
                t_ = time.time(); q = emb(r["text"], prefix); lat.append(time.time() - t_)
                sc = Mx @ q; j = int(np.argmax(sc)); es2.append((clean[j][1], float(sc[j])))
            lat.sort()
            name = f"EmbeddingGemma-2 270M-class q4 ONNX ({tag}) + Tier0, leak-free"
            print(f"\n{name}: process memory growth ~{rssg() - r0:.2f} GB; embed latency median {1000*lat[len(lat)//2]:.0f} ms (CPU)")
            report(name, [((a0, 1.0) if a0 else (a, s)) for a0, (a, s) in zip(t0s, es2)], [0.75, 0.80, 0.84, 0.86, 0.88, 0.90, 0.92])
            results[name]["median_ms"] = round(1000 * lat[len(lat) // 2]); results[name]["mem_gb"] = round(rssg() - r0, 2)
            results[name + " [no Tier0]"] = None
            report(name.replace("+ Tier0, ", "alone, "), es2, [0.75, 0.80, 0.84, 0.86, 0.88, 0.90, 0.92])

    results["_t0"] = t0s

for ad in args.adapter:
    import mlx.core as mx
    from mlx_lm import load
    from mlx_lm.models.cache import make_prompt_cache
    import resource, time as _t
    _rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30
    _r0 = _rss()
    model, tok = load(args.model, adapter_path=ad)
    eos = tok.eos_token_id
    def decode(text):
        cache = make_prompt_cache(model)
        ids = mx.array(tok.apply_chat_template([{"role": "user", "content": prompt(text)}], add_generation_prompt=True))[None]
        out, lp = [], 0.0
        for _ in range(10):
            logits = model(ids, cache=cache)[:, -1, :]
            logp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
            t = int(mx.argmax(logp, axis=-1).item())
            if t == eos or tok.decode([t]).strip().startswith("<|"): break
            lp += float(logp[0, t].item()); out.append(t); ids = mx.array([[t]])
            if "\n" in tok.decode(out): break
        return tok.decode(out).strip().split("\n")[0], math.exp(lp)
    _lat, preds = [], []
    for r in rows:
        _t0 = _t.time(); preds.append(decode(r["text"])); _lat.append(_t.time() - _t0)
    _lat.sort()
    print(f"\nfine-tuned model [{os.path.basename(ad)}]: peak process memory growth ~{_rss() - _r0:.2f} GB (MLX buffers on the GPU may add to this); decode latency median {1000*_lat[len(_lat)//2]:.0f} ms, p95 {1000*_lat[int(len(_lat)*.95)]:.0f} ms")
    name = f"fine-tuned {args.model.split('/')[-1]} [{os.path.basename(ad)}]"
    report(name, preds, [0.0, 0.5, 0.7, 0.8, 0.9, 0.95])
    results[name]["raw"] = [[r["text"], r["intent"] if r["is_command"] else None, a, round(c, 3)] for r, (a, c) in zip(rows, preds)]
    if args.baseline:  # production Tier 0 first, fine-tune replaces Tier 0.5a
        report(name + " + Tier0 first", [((a0, 1.0) if a0 else p) for a0, p in zip(results["_t0"], preds)], [0.5, 0.7, 0.8, 0.9, 0.95])
if args.laya:
    import resource, time
    sys.path.insert(0, "/tmp/laya_pkgs")
    rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20 / 1024  # GB (macOS reports bytes)
    from laya import Router
    labels = sorted({a for _, a in production_examples()})
    before = rss()
    router = Router(device=args.laya_device, preload=False)
    load_gb = rss() - before
    questions = {
        "action": {"type": "choice", "instructions": "Which action is the user asking their computer assistant to do?",
                   "criteria": {**{a: a.replace("_", " ") for a in labels}, "none": "not a command to the assistant: conversation, TV, narration"}},
        "is_command": {"type": "noul", "instructions": "Is the speaker giving a direct command to a voice assistant?"},
    }
    preds, times = [], []
    router.predict("open safari", questions, model=args.laya_model)  # warm-up: loads the checkpoint
    load_gb = rss() - before
    for r in rows:
        t = time.time(); res = router.predict(r["text"], questions, model=args.laya_model); times.append(time.time() - t)
        a = res["answers"]; act = a["action"]["choice"]
        preds.append((act, a["action"]["probabilities"].get(act, 0.0) * a["is_command"]["noul"]))
    times.sort()
    print(f"\nLaya ({args.laya_device}): extra process memory after load ~{load_gb:.2f} GB (peak RSS now {rss():.2f} GB); latency median {1000*times[len(times)//2]:.0f} ms, p95 {1000*times[int(len(times)*.95)]:.0f} ms")
    report(f"Laya zero-shot ({args.laya_model}, {args.laya_device})", preds, [0.0, 0.3, 0.5, 0.7, 0.9])
    results[f"Laya zero-shot ({args.laya_model}, {args.laya_device})"]["load_gb"] = round(load_gb, 2); results[f"Laya zero-shot ({args.laya_model}, {args.laya_device})"]["median_ms"] = round(1000 * times[len(times) // 2])
    results[f"Laya zero-shot ({args.laya_model}, {args.laya_device})"]["raw"] = [[r["text"], r["intent"] if r["is_command"] else None, a, round(c, 3)] for r, (a, c) in zip(rows, preds)]
    if args.baseline:
        report("Laya + Tier0 first", [((a0, 1.0) if a0 else p) for a0, p in zip(results["_t0"], preds)], [0.3, 0.5, 0.7, 0.9])

json.dump(results, open(args.out, "w"), indent=1)
