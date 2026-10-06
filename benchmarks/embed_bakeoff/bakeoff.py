"""Tier 0.5a bake-off: current n-gram hashing vs nomic-embed-text vs EmbeddingGemma v1 (Ollama) vs v2 (HF).
Index = the real _INTENT_EXAMPLES and embed_utterance() pulled verbatim from free_voice.py via ast."""
import ast, json, re, statistics, sys, time, urllib.request, zlib
import numpy as np
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from testset import POS, DESTRUCTIVE, NEG

SRC = __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "free_voice.py")
tree = ast.parse(open(SRC).read())
ns = {"re": re, "zlib": zlib}
for node in tree.body:
    if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_INTENT_EXAMPLES":
        INTENTS = ast.literal_eval(node.value)
    if isinstance(node, ast.FunctionDef) and node.name == "embed_utterance":
        exec(compile(ast.Module([node], []), SRC, "exec"), ns)
DESTRUCTIVE_SET = {"shutdown", "restart", "logout", "empty_trash", "kill_app"}
EX = [(a, e) for a, exs in INTENTS.items() for e in exs]

def norm(m):
    m = np.asarray(m, dtype=np.float32)
    return m / np.linalg.norm(m, axis=-1, keepdims=True)

def ollama(model, prefix):
    def f(texts):
        req = urllib.request.Request("http://localhost:11434/api/embed",
            data=json.dumps({"model": model, "input": [prefix + t for t in texts]}).encode(),
            headers={"Content-Type": "application/json"})
        return norm(json.load(urllib.request.urlopen(req, timeout=120))["embeddings"])
    return f

_st = {}
def st(prefix, device):
    def f(texts):
        if device not in _st:
            from sentence_transformers import SentenceTransformer
            _st[device] = SentenceTransformer("google/embeddinggemma-2", device=device)
        return norm(_st[device].encode([prefix + t for t in texts], normalize_embeddings=True))
    return f

hashf = lambda texts: np.stack([ns["embed_utterance"](t) for t in texts])
G = "task: classification | query: "
CONTENDERS = [
    ("current: n-gram hash", hashf),
    ("nomic-embed-text (raw)", ollama("nomic-embed-text", "")),
    ("nomic-embed-text (classification:)", ollama("nomic-embed-text", "classification: ")),
    ("embeddinggemma v1 (raw)", ollama("embeddinggemma", "")),
    ("embeddinggemma v1 (task prompt)", ollama("embeddinggemma", G)),
    ("embeddinggemma-2 MPS (raw)", st("", "mps")),
    ("embeddinggemma-2 MPS (task prompt)", st(G, "mps")),
    ("embeddinggemma-2 CPU (task prompt)", st(G, "cpu")),
]
only = sys.argv[1:]  # optional substring filter

def run(name, f):
    idx = f([e for _, e in EX])
    def top(texts):
        s = f(texts) @ idx.T
        b = s.argmax(1)
        return [(EX[i][0], float(s[r, i])) for r, i in enumerate(b)]
    pos, neg, dst = top([t for t, _, _ in POS]), top(NEG), top([t for t, _ in DESTRUCTIVE])
    lat = []
    f(["warm up"])
    for t, _, _ in POS[:25]:
        t0 = time.perf_counter(); f([t]); lat.append((time.perf_counter() - t0) * 1000)
    negs = sorted((s for _, s in neg), reverse=True)
    out = {"name": name, "top1": sum(p[0] == g for p, (_, g, _) in zip(pos, POS)) / len(POS),
           "p50_ms": statistics.median(lat), "thr": {}}
    thresholds = {"0 neg accepted": negs[0] + 1e-6, "<=2 neg accepted": negs[2] + 1e-6}
    if name.startswith("current"):
        thresholds["prod 0.75"] = 0.75
    for label, t in thresholds.items():
        ok = sum(p[0] == g and p[1] >= t for p, (_, g, _) in zip(pos, POS))
        wrong = sum(p[0] != g and p[1] >= t for p, (_, g, _) in zip(pos, POS))
        fa = sum(s >= t for _, s in neg)
        bad = [t_ for (a, s), (t_, g) in zip(dst, DESTRUCTIVE) if s >= t and a not in DESTRUCTIVE_SET]
        dhit = sum(a == g and s >= t for (a, s), (_, g) in zip(dst, DESTRUCTIVE))
        out["thr"][label] = dict(t=round(t, 3), hit=ok / len(POS), misroute=wrong / len(POS),
                                 neg_fa=fa, destr_caught=dhit, destr_misrouted=bad)
    by_src = {}
    for p, (_, g, src) in zip(pos, POS):
        by_src.setdefault(src, []).append(p[0] == g)
    out["top1_by_source"] = {k: f"{sum(v)}/{len(v)}" for k, v in by_src.items()}
    out["pos_misses"] = [(t, g, p[0]) for p, (t, g, _) in zip(pos, POS) if p[0] != g]
    return out

results = []
for name, f in CONTENDERS:
    if only and not any(o in name for o in only):
        continue
    t0 = time.time()
    try:
        r = run(name, f)
    except Exception as e:
        r = {"name": name, "error": f"{type(e).__name__}: {e}"[:300]}
    r["wall_s"] = round(time.time() - t0, 1)
    results.append(r)
    print(json.dumps(r), flush=True)
json.dump(results, open(__file__.rsplit("/", 1)[0] + "/bakeoff_results.json", "w"), indent=1)
