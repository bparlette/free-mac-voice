#!/usr/bin/env python3
"""Compare the Tier 1 / vision model size (qwen3-vl 8B vs 4B) on the jobs the assistant actually gives it, using the
assistant's own functions (ollama_route, vision_ask) with only OLLAMA_MODEL changed.

  ./.venv/bin/python benchmarks/model_size_eval/bench_qwen.py qwen3-vl:8b
  ./.venv/bin/python benchmarks/model_size_eval/bench_qwen.py qwen3-vl:4b

Needs Ollama running with the model pulled, and screenshots in /tmp/shots (any page screenshots with the expected text; the
question/answer list below was written for the Off The Rip site screenshots, so replace it with your own images + answers).
Run one model at a time with the other unloaded (keep_alive 0) so memory numbers and speed are not contended.
"""
import argparse, json, os, random, statistics, sys, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("--shots", default="/tmp/shots")
ap.add_argument("--n-route", type=int, default=60, help="commands AND non-commands to route (each)")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))
args = ap.parse_args()
os.environ["OLLAMA_MODEL"] = args.model  # read by free_voice at import
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test, ACCEPT  # noqa: E402
import free_voice as fv  # noqa: E402
fv.log = lambda *a, **k: None
H = fv.OLLAMA_HOST

ACC = {k: set(v) for k, v in ACCEPT.items()}
ACC["set_volume"] |= {"volume_up", "volume_down"}
ACC["mute"] |= {"tv_mute"}
for k in ("media_play_pause", "next_track"): ACC[k] |= {"media"}


def post(path, body, timeout=300):
    req = urllib.request.Request(H + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def warm():
    post("/api/generate", {"model": args.model, "prompt": "hi", "stream": False, "keep_alive": "20m", "options": {"num_predict": 1}})


res = {"model": args.model}
warm()

# ---- 1. generation / prompt speed + resident size
def gen():
    d = post("/api/generate", {"model": args.model, "prompt": "Explain in plain words why the sky is blue, in about eighty words.", "stream": False,
                               "think": False, "keep_alive": "20m", "options": {"num_predict": 96, "temperature": 0}})
    return d["eval_count"] / (d["eval_duration"] / 1e9), d["prompt_eval_count"] / max(d["prompt_eval_duration"] / 1e9, 1e-9)
g = [gen() for _ in range(3)]
ps = [m for m in json.load(urllib.request.urlopen(H + "/api/ps"))["models"] if m["name"].startswith(args.model.split(":")[0]) and m["name"] == args.model]
res["generation_tok_s"] = round(statistics.median(x[0] for x in g), 1)
res["prompt_tok_s"] = round(statistics.median(x[1] for x in g))
res["resident_gb"] = round(ps[0]["size"] / 2**30, 2) if ps else None
print(f"{args.model}: {res['generation_tok_s']} tok/s generation, {res['prompt_tok_s']} tok/s prompt, resident {res['resident_gb']} GB", flush=True)

# ---- 2. Tier 1 routing (the assistant's own prompt) on held-out commands and adversarial non-commands
rows = split_test(0)[1]
rnd = random.Random(7)
cmds = rnd.sample([r for r in rows if r["is_command"]], args.n_route)
non = rnd.sample([r for r in rows if not r["is_command"]], args.n_route)
# warm-up with the assistant's exact request shape (a different context size reloads the model; that cold load must not count),
# and reset the assistant's "Ollama failed recently, skip for 30 s" latch before every call so one slow call cannot cascade
try:
    post("/api/chat", {"model": args.model, "format": "json", "keep_alive": "20m", "think": False, "options": {"temperature": 0, "num_predict": 64, "num_ctx": 1024},
                       "messages": [{"role": "system", "content": fv._TIER1_SYSTEM}, {"role": "user", "content": "open safari"}, {"role": "assistant", "content": '{"action": "'}], "stream": False})
except Exception as e:  # e.g. HTTP 501 "structured output is unavailable": the assistant's current routing code cannot use this model
    print(f"[note] routing warm-up failed ({e}); the assistant's current routing (format=json) cannot use {args.model} - see route_prefill_ab.py for a no-format run", flush=True)
right = wrong = miss = 0; lat = []; wrong_ex = []
for r in cmds:
    fv._ollama_ok = None; fv._ollama_last_failure = 0.0
    t = time.perf_counter(); out = fv.ollama_route(r["text"]); lat.append(time.perf_counter() - t)
    if out is None: miss += 1
    elif out[0] in ACC[r["intent"]]: right += 1
    else: wrong += 1; wrong_ex.append((r["text"], r["intent"], out[0]))
fa = 0; fa_ex = []
for r in non:
    fv._ollama_ok = None; fv._ollama_last_failure = 0.0
    t = time.perf_counter(); out = fv.ollama_route(r["text"]); lat.append(time.perf_counter() - t)
    if out is not None: fa += 1; fa_ex.append((r["text"], out[0]))
res["routing"] = {"commands": len(cmds), "correct": right, "wrong_action": wrong, "no_answer": miss, "non_commands": len(non), "false_accepts": fa,
                  "median_s": round(statistics.median(lat), 2), "p95_s": round(sorted(lat)[int(len(lat) * .95)], 2),
                  "wrong_examples": wrong_ex[:6], "false_accept_examples": fa_ex[:6]}
print(f"routing: {right}/{len(cmds)} correct, {wrong} wrong action, {miss} no answer; {fa}/{len(non)} false accepts; median {res['routing']['median_s']}s", flush=True)

# ---- 3. screen understanding (the assistant's own vision_ask: 800 px downscale, temperature 0)
VQA = [("home.png", "What does the large lime-green headline say?", ["sold on ebay"]),
       ("home.png", "How many losses in a row does the dark card say? Answer with the number.", ["7", "seven"]),
       ("home.png", "What percent positive feedback is shown?", ["100"]),
       ("verdict.png", "What is the page title?", ["daily pack verdict"]),
       ("verdict.png", "What is the profit-loss record shown in the stats?", ["0-7", "0–7", "0 - 7"]),
       ("chart.png", "What dollar amount is written next to the last point on the chart?", ["71"]),
       ("chart.png", "What is the net dollar total in the third stat box?", ["71"]),
       ("hq.png", "What does the page heading say?", ["backstage"]),
       ("hq.png", "How many tasks are done out of how many in total?", ["of 39", "1/39", "39"]),
       ("hq.png", "What percentage is complete?", ["3%", "3 %"]),
       ("wantlist.png", "What does the main heading say?", ["hunting a card"]),
       ("day.png", "Which card game is named in the heading?", ["dragon ball"])]
fv._ollama_ok = None; fv._ollama_last_failure = 0.0
fv.vision_ask("What is the page title?", os.path.join(args.shots, "verdict.png"))  # warm-up (context-size reload), not counted
ok = 0; vlat = []; vfail = []
for img, q, keys in VQA:
    fv._ollama_ok = None; fv._ollama_last_failure = 0.0
    p = os.path.join(args.shots, img)
    t = time.perf_counter(); a = fv.vision_ask(q, p) or ""; vlat.append(time.perf_counter() - t)
    hit = any(k in a.lower() for k in keys); ok += hit
    if not hit: vfail.append((img, q, a[:80]))
res["vision"] = {"questions": len(VQA), "correct": ok, "median_s": round(statistics.median(vlat), 1), "misses": vfail}
print(f"vision: {ok}/{len(VQA)} correct; median {res['vision']['median_s']}s", flush=True)

# ---- 4. general questions, exactly as the assistant's ollama_answer() asks them (same system prompt, 256 tokens, final answer only)
QA = [("What is the capital of Australia?", ["canberra"]), ("How many days are in a leap year?", ["366"]), ("What is 17 times 6?", ["102"]),
      ("Who wrote Pride and Prejudice?", ["austen"]), ("Which planet is known as the Red Planet?", ["mars"]),
      ("What is the boiling point of water in Celsius at sea level?", ["100"]), ("What is the chemical symbol for gold?", ["au"]),
      ("How many minutes are in three hours?", ["180"]), ("Spell the word necessary backwards.", ["yrassecen"]),
      ("Which is larger, three quarters or two thirds? Name the fraction.", ["3/4", "three quarters", "three-quarters", "0.75"])]
SYSTEM = "You are a concise Mac voice assistant. Answer in one or two short spoken sentences. Plain text only, no markdown, no lists."
post("/api/chat", {"model": args.model, "stream": False, "think": False, "keep_alive": "20m", "options": {"temperature": 0, "num_predict": 8},
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Say hi."}]})  # warm-up, not counted
qok = empty = 0; qlat = []; qmiss = []; qtok = []
for q, keys in QA:
    t = time.perf_counter()
    d = post("/api/chat", {"model": args.model, "stream": False, "think": False, "keep_alive": "20m", "options": {"temperature": 0, "num_predict": 256},
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]})
    qlat.append(time.perf_counter() - t); qtok.append(d.get("eval_count", 0))
    text = d["message"]["content"].strip(); a = text.lower().replace(",", "")
    if not text: empty += 1  # the assistant would say nothing: the model used its whole budget on hidden reasoning
    hit = any(k in a for k in keys); qok += hit
    if not hit: qmiss.append((q, text[:80] or "<EMPTY>"))
res["qa"] = {"questions": len(QA), "correct": qok, "empty_replies": empty, "median_s": round(statistics.median(qlat), 1),
             "median_tokens_used": int(statistics.median(qtok)), "misses": qmiss}
print(f"general Q&A: {qok}/{len(QA)} correct, {empty} empty (nothing would be spoken); median {res['qa']['median_s']}s, {res['qa']['median_tokens_used']} tokens", flush=True)

tag = args.model.replace(":", "_").replace("/", "_")
json.dump(res, open(os.path.join(args.out, f"{tag}.json"), "w"), indent=1)
