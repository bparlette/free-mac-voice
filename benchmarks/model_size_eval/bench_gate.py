#!/usr/bin/env python3
"""The command gate on its own: for a held-out half of the intent set (204 commands / 200 non-commands), how often does a decision model say
"command" (via /v1/systemone, same question as free_voice.is_voice_command)? Reports acceptance of commands, acceptance of non-commands (false
accepts) and latency, at several probability cut-offs.  Works with any server that implements /v1/systemone (Ollama tev1:*, llama-server for Kev/d1).

  ./.venv/bin/python benchmarks/model_size_eval/bench_gate.py tev1:4b --name tev1_4b
  ./.venv/bin/python benchmarks/model_size_eval/bench_gate.py x --host http://127.0.0.1:8190 --name kev_4b_q4km
"""
import argparse, json, os, statistics, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("model"); ap.add_argument("--host", default="http://localhost:11434"); ap.add_argument("--name", required=True)
args = ap.parse_args()
rows = split_test(0)[1]
Q = {"is_command": {"type": "choice", "instructions": "Is this spoken phrase an imperative command or action directed at a computer assistant?",
     "criteria": {"command": "An instruction or command for the computer to perform an action (e.g. open an app, adjust volume, search, play music, set timer)",
                  "none": "Casual speech, statement, question, TV/podcast dialogue, or not asking the assistant to take an action"}}}

def ask(text):
    body = {"model": args.model, "state": text, "questions": Q, "keep_alive": "10m"}
    req = urllib.request.Request(args.host + "/v1/systemone", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.time(); d = json.load(urllib.request.urlopen(req, timeout=60)); dt = time.time() - t
    return float(d["answers"]["is_command"]["probabilities"].get("command", 0.0)), dt

ask("warm up")
res = []
for r in rows:
    p, dt = ask(r["text"]); res.append((r["is_command"], p, dt))
nc = sum(1 for c, _, _ in res if c); nn = len(res) - nc
out = {"model": args.model, "name": args.name, "commands": nc, "non_commands": nn, "median_ms": round(1000 * statistics.median(x[2] for x in res)), "cutoffs": {}}
for th in (0.3, 0.5, 0.7, 0.9):
    out["cutoffs"][str(th)] = {"commands_accepted_pct": round(100 * sum(1 for c, p, _ in res if c and p >= th) / nc, 1),
                               "non_commands_accepted_pct": round(100 * sum(1 for c, p, _ in res if not c and p >= th) / nn, 1)}
print(json.dumps(out, indent=1))
json.dump(out, open(os.path.join(HERE, "results", f"gate_{args.name}.json"), "w"), indent=1)
