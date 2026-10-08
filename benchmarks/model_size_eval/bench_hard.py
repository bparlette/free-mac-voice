#!/usr/bin/env python3
"""Harder screen-reading and reasoning questions than bench_qwen.py, to see where smaller models fall behind.
  - 10 screen questions about SMALL text in dense pages (task list, results table; the assistant downsizes screenshots to 800 px, so the text is tiny)
  - 10 multi-step reasoning / arithmetic questions with a single checkable answer, asked exactly as the assistant's answer function asks (256 tokens, final answer only)

  ./.venv/bin/python benchmarks/model_size_eval/bench_hard.py qwen3-vl:8b-instruct       # screenshots in /tmp/shots (see VQA below)
"""
import argparse, json, os, statistics, sys, time, urllib.request
ap = argparse.ArgumentParser(); ap.add_argument("model"); ap.add_argument("--shots", default="/tmp/shots")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))
args = ap.parse_args(); os.environ["OLLAMA_MODEL"] = args.model
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import free_voice as fv  # noqa: E402
fv.log = lambda *a, **k: None
H = fv.OLLAMA_HOST
def post(path, body, timeout=300):
    req = urllib.request.Request(H + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}); return json.load(urllib.request.urlopen(req, timeout=timeout))

VQA = [("hq.png", "What is the title of task T05?", ["cloudflare"]),
       ("hq.png", "What is the title of task T09?", ["kit", "convertkit"]),
       ("hq.png", "Which task comes right after 'Register the domains'?", ["github"]),
       ("hq.png", "What does the progress line under the bar say about tasks done? Quote the numbers.", ["39"]),
       ("hq.png", "According to the progress line, about how many hours of work are left?", ["20"]),
       ("hq.png", "According to the progress line, how many tasks need Clayton or another human?", ["12"]),
       ("verdict.png", "In the table, what product is listed for Day 21?", ["pitch black"]),
       ("verdict.png", "In the table, which day number is the Union Arena row?", ["22"]),
       ("verdict.png", "What date is shown for Day 20?", ["oct 3", "october 3", "oct. 3"]),
       ("verdict.png", "How many days are logged according to the stats boxes?", ["7"])]
QA = [("A train leaves at 3:45 PM and the trip takes 2 hours 50 minutes. What time does it arrive?", ["6:35"]),
      ("If 3 notebooks cost $7.50, how much do 8 notebooks cost?", ["20"]),
      ("I have 17 apples, give away 5, then buy twice as many as I have left. How many apples do I have now?", ["36"]),
      ("What is 15 percent of 240?", ["36"]),
      ("Which is heavier: a kilogram of feathers or a kilogram of steel?", ["same", "equal", "neither"]),
      ("If today is Wednesday, what day of the week is it 10 days from now?", ["saturday"]),
      ("A recipe needs 3 eggs for 12 muffins. How many eggs for 20 muffins?", ["5", "five"]),
      ("What is the next number in the sequence 2, 6, 12, 20, 30?", ["42"]),
      ("How many minutes are there between 9:50 AM and 1:15 PM?", ["205"]),
      ("A shirt costs $40 after a 20 percent discount. What was the original price?", ["50"])]
SYSTEM = "You are a concise Mac voice assistant. Answer in one or two short spoken sentences. Plain text only, no markdown, no lists."
post("/api/generate", {"model": args.model, "prompt": "hi", "stream": False, "keep_alive": "20m", "options": {"num_predict": 1}})
fv._ollama_ok = None; fv.vision_ask("What is the page title?", os.path.join(args.shots, "verdict.png"))   # warm-up for the 4096 context
ok = 0; vlat = []; vmiss = []
for img, q, keys in VQA:
    fv._ollama_ok = None; fv._ollama_last_failure = 0.0
    t = time.perf_counter(); a = fv.vision_ask(q, os.path.join(args.shots, img)) or ""; vlat.append(time.perf_counter() - t)
    hit = any(k in a.lower() for k in keys); ok += hit
    if not hit: vmiss.append((q[:60], a[:70] or "<EMPTY>"))
post("/api/chat", {"model": args.model, "stream": False, "think": False, "keep_alive": "20m", "options": {"temperature": 0, "num_predict": 8}, "messages": [{"role": "user", "content": "Say hi."}]})
qok = empty = 0; qlat = []; qmiss = []
for q, keys in QA:
    t = time.perf_counter()
    d = post("/api/chat", {"model": args.model, "stream": False, "think": False, "keep_alive": "20m", "options": {"temperature": 0, "num_predict": 256},
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]})
    qlat.append(time.perf_counter() - t); text = d["message"]["content"].strip(); a = text.lower().replace(",", "")
    empty += not text; hit = any(k in a for k in keys); qok += hit
    if not hit: qmiss.append((q[:50], text[:70] or "<EMPTY>"))
res = {"model": args.model, "vision_hard": {"questions": len(VQA), "correct": ok, "median_s": round(statistics.median(vlat), 1), "misses": vmiss},
       "reasoning": {"questions": len(QA), "correct": qok, "empty_replies": empty, "median_s": round(statistics.median(qlat), 1), "misses": qmiss}}
json.dump(res, open(os.path.join(args.out, f"hard_{args.model.replace(':','_')}.json"), "w"), indent=1)
print(f"{args.model}: hard screen questions {ok}/{len(VQA)} (median {res['vision_hard']['median_s']} s) | reasoning {qok}/{len(QA)} ({empty} silent, median {res['reasoning']['median_s']} s)")
for m in vmiss[:3]: print("   screen miss:", m)
for m in qmiss[:3]: print("   reasoning miss:", m)
