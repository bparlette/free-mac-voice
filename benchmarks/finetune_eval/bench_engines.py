#!/usr/bin/env python3
"""Engine and ONNX benchmarks for the router's embedding step (Tier 0.5a), same data and scoring as eval.py.

  # old vs new Ollama (two servers: e.g. old on 11434, new on 11500)
  ./.venv/bin/python benchmarks/finetune_eval/bench_engines.py ollama --old http://127.0.0.1:11434 --new http://127.0.0.1:11500

  # ONNX variants run with onnxruntime (model folders under /tmp/onnx_models/<name>/, see README)
  ./.venv/bin/python benchmarks/finetune_eval/bench_engines.py onnx

Metric: leak-free nearest-example matching, Tier 0 regex first (as in production). 'correct@FA<=16%' is the best
correct-command rate over a fine threshold grid subject to <=16% non-command false accepts (the router's own level),
which makes models with different cosine scales comparable.
"""
import argparse, json, os, resource, statistics, sys, time, urllib.request
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

sys.path.insert(0, ROOT)
import free_voice as fv  # noqa: E402
fv.DRY_RUN = True; fv.log = lambda *a, **k: None

rows = split_test(0)[1]
all_norms = {norm(r["text"]) for r in load_test()}
clean = [(t, a) for t, a in production_examples() if norm(t) not in all_norms]
t0s = []
for r in rows:
    x = fv.route(r["text"]); t0s.append(x[0] if x else None)
GRID = [round(x, 3) for x in np.arange(0.40, 0.985, 0.005)]


def score(E, Q, with_t0):
    """E: example matrix, Q: query matrix (both L2-normalised). Returns (best correct@FA<=16, thr, correct, fa, table)."""
    sims = Q @ E.T
    j = sims.argmax(1); s = sims.max(1)
    acts = [clean[k][1] for k in j]
    n_c = sum(r["is_command"] for r in rows); n_n = len(rows) - n_c
    best = (0.0, None, 0.0, 0.0); table = {}
    for th in GRID:
        right = fa = 0
        for r, a, sc, a0 in zip(rows, acts, s, t0s):
            act, ok_conf = (a0, True) if (with_t0 and a0) else (a, sc >= th)
            if not ok_conf:
                continue
            if r["is_command"]:
                right += act in ACCEPT[r["intent"]]
            else:
                fa += 1
        table[th] = (100 * right / n_c, 100 * fa / n_n)
        if fa / n_n <= 0.16 and right / n_c > best[0]:
            best = (right / n_c, th, right / n_c, fa / n_n)
    return best, table


def fmt(name, mode, best, extra):
    c, th, _, fa = best
    return f"{name:58s} {mode:9s} correct@FA<=16%: {100*c:5.1f}% (thr {th}, FA {100*fa:4.1f}%)  {extra}"


def l2(M):
    M = np.asarray(M, np.float32); return M / np.linalg.norm(M, axis=1, keepdims=True)


# ---------------------------------------------------------------- Ollama old vs new
def ollama_embed(host, model, texts, timeout=120):
    req = urllib.request.Request(host + "/api/embed", data=json.dumps({"model": model, "input": texts, "keep_alive": "10m"}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["embeddings"]


def ollama_ps(host):
    return json.load(urllib.request.urlopen(host + "/api/ps", timeout=10)).get("models", [])


def ollama_gen_speed(host, model="qwen2.5:1.5b"):
    def run():
        req = urllib.request.Request(host + "/api/generate", data=json.dumps({"model": model, "prompt": "Write the numbers one to forty in words.", "stream": False, "options": {"num_predict": 96, "temperature": 0}, "keep_alive": "1m"}).encode(), headers={"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=300)); return d["eval_count"] / (d["eval_duration"] / 1e9)
    run(); return statistics.median([run() for _ in range(3)])


def bench_ollama(args):
    cases = [("old", args.old, "embeddinggemma"), ("new", args.new, "embeddinggemma"), ("new", args.new, "embeddinggemma-2:270m")]
    out = []
    for label, host, model in cases:
        ver = json.load(urllib.request.urlopen(host + "/api/version"))["version"]
        for prefix in ([""] if model == "embeddinggemma" else ["", "task: classification | query: "]):
            try:
                ollama_embed(host, model, ["warm up"])
                E = l2(ollama_embed(host, model, [prefix + t for t, _ in clean]))
                lat = []
                for r in rows[:60]:
                    t = time.time(); ollama_embed(host, model, [prefix + r["text"]]); lat.append(time.time() - t)
                t = time.time(); Q = []
                for i in range(0, len(rows), 50): Q += ollama_embed(host, model, [prefix + r["text"] for r in rows[i:i + 50]])
                thr_s = len(rows) / (time.time() - t)
                Q = l2(Q)
                ps = [m for m in ollama_ps(host) if m["name"].startswith(model)]
                mem = f"loaded {ps[0]['size']/2**20:.0f} MB" if ps else "n/a"
                best, _ = score(E, Q, True)
                line = fmt(f"Ollama {label} v{ver}: {model}", "prefix" if prefix else "no prefix", best, f"| single-query median {1000*statistics.median(lat):.0f} ms, batch {thr_s:.0f} texts/s, {mem}")
            except Exception as e:
                line = f"Ollama {label} v{ver}: {model}: FAILED {type(e).__name__}: {str(e)[:100]}"
            print(line); out.append(line)
    for label, host in (("old", args.old), ("new", args.new)):
        try:
            sp = ollama_gen_speed(host); line = f"Ollama {label} v{json.load(urllib.request.urlopen(host + '/api/version'))['version']}: qwen2.5:1.5b generation speed {sp:.0f} tokens/s (median of 3, 96 tokens)"
        except Exception as e:
            line = f"Ollama {label}: generation speed FAILED {str(e)[:80]}"
        print(line); out.append(line)
    return out


# ---------------------------------------------------------------- ONNX variants
def bench_onnx(args):
    import onnxruntime as ort
    from tokenizers import Tokenizer
    out = []
    base = args.onnx_dir
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        tk = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
        for f in sorted(os.listdir(os.path.join(d, "onnx"))):
            if not f.endswith(".onnx"): continue
            for provider in args.providers:
                if args.only and args.only != f"{name}/{f[:-5]}/{provider}": continue
                tag = f"ONNX {name} {f[:-5]} [{provider}]"
                try:
                    r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30
                    t = time.time()
                    sess = ort.InferenceSession(os.path.join(d, "onnx", f), providers=[provider + "ExecutionProvider"])
                    load_s = time.time() - t
                    names = [i.name for i in sess.get_inputs()]
                    outname = [o.name for o in sess.get_outputs() if o.name == "sentence_embedding"][0]

                    def emb(texts):
                        enc = [tk.encode(x).ids for x in texts]; L = max(map(len, enc))
                        ids = np.array([e + [0] * (L - len(e)) for e in enc], np.int64)
                        mask = np.array([[1] * len(e) + [0] * (L - len(e)) for e in enc], np.int64)
                        feed = {"input_ids": ids, "attention_mask": mask}
                        for n in names:
                            if n.endswith("_features"): feed[n] = np.zeros((0, 512), np.float32)
                        return sess.run([outname], feed)[0]

                    for prefix in ["", "task: classification | query: "]:
                        E = l2(np.concatenate([emb([prefix + t for t, _ in clean[i:i + 32]]) for i in range(0, len(clean), 32)]))
                        lat = []
                        for r in rows[:40]:
                            t = time.time(); emb([prefix + r["text"]]); lat.append(time.time() - t)
                        t = time.time()
                        Q = l2(np.concatenate([emb([prefix + r["text"] for r in rows[i:i + 32]]) for i in range(0, len(rows), 32)]))
                        thr_s = len(rows) / (time.time() - t)
                        best, _ = score(E, Q, True)
                        mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30 - r0
                        line = fmt(tag, "prefix" if prefix else "no prefix", best, f"| weights {sum(os.path.getsize(os.path.join(d, 'onnx', g)) for g in os.listdir(os.path.join(d, 'onnx')) if g.startswith(f)) / 2**20:.0f} MB | single {1000*statistics.median(lat):.0f} ms, batch {thr_s:.0f}/s, load {load_s:.1f}s, mem +{mem:.2f} GB")
                        print(line); out.append(line)
                    del sess
                except Exception as e:
                    line = f"{tag}: FAILED {type(e).__name__}: {str(e)[:110]}"
                    print(line); out.append(line)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("ollama"); o.add_argument("--old", required=True); o.add_argument("--new", required=True)
    x = sub.add_parser("onnx"); x.add_argument("--onnx-dir", default="/tmp/onnx_models"); x.add_argument("--providers", nargs="*", default=["CPU", "CoreML"]); x.add_argument("--only", help="name/file/provider, e.g. embeddinggemma1/model_q4/CPU (run in a fresh process for clean memory numbers)")
    a = ap.parse_args()
    print(f"{len(rows)} held-out rows; {len(clean)} leak-free examples\n")
    (bench_ollama if a.cmd == "ollama" else bench_onnx)(a)
