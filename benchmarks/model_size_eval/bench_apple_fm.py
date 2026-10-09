#!/usr/bin/env python3
"""Apple's on-device Foundation Model (macOS 26+, no download, no Ollama memory) as (1) the command gate and (2) the action router.
Uses guided generation: the model must answer with one value of a Swift enum, so there is no 26-option limit and no free text.
A tiny Swift helper is generated and compiled into /tmp, then fed the held-out half of the intent set (seed 0) as JSON lines.
No probabilities come back, so there is no threshold to sweep: one operating point per question.
  ./.venv/bin/python benchmarks/model_size_eval/bench_apple_fm.py [--few-shot]
"""
import json, os, statistics, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test, ACCEPT, norm, load_test  # noqa: E402
import free_voice as fv  # noqa: E402
fv.DRY_RUN = True; fv.log = lambda *a, **k: None

rows = split_test(0)[1]
test_norms = {norm(r["text"]) for r in load_test()}
actions = list(fv._INTENT_EXAMPLES)
menu = "\n".join(f"- {a}: e.g. \"{next((t for t in fv._INTENT_EXAMPLES[a] if norm(t) not in test_norms), fv._INTENT_EXAMPLES[a][0])}\"" for a in actions)
GATE_INSTR = ("Decide whether a transcribed spoken phrase is an imperative command or action directed at a computer assistant (open an app, "
              "adjust volume, search, play music, set a timer). Casual speech, statements, questions, TV or podcast dialogue are notCommand.")
ROUTE_INSTR = ("You route transcribed voice commands for a Mac assistant. Pick the single action that the phrase asks for, or no_action if the "
               "phrase is not a command for the assistant (casual speech, TV dialogue, statements). Actions:\n" + menu)
if "--few-shot" in sys.argv:   # add labelled examples (training negatives + router examples, none from the test set) to both instructions
    negs = [l.strip() for l in open(os.path.join(HERE, "..", "finetune_eval", "negatives.txt")) if l.strip() and norm(l) not in test_norms][:20]
    pos = [t for a in actions for t in fv._INTENT_EXAMPLES[a][:1] if norm(t) not in test_norms][:20]
    shots = "\nExamples that are NOT commands (answer notCommand / no_action):\n" + "\n".join(f"- {t}" for t in negs) + \
            "\nExamples that ARE commands:\n" + "\n".join(f"- {t}" for t in pos)
    GATE_INSTR += shots; ROUTE_INSTR += shots
swift = f'''import Foundation
import FoundationModels
@Generable enum Gate: String {{ case command, notCommand }}
@Generable enum Action: String {{ case {", ".join(actions)}, no_action }}
@main struct Run {{
  static func main() async {{
    let gi = {json.dumps(GATE_INSTR)}
    let ri = {json.dumps(ROUTE_INSTR)}
    let opts = GenerationOptions(sampling: .greedy)
    while let line = readLine() {{
      guard let text = try? JSONDecoder().decode(String.self, from: Data(line.utf8)) else {{ continue }}
      var gate = "error", action = "error"; var gms = 0.0, rms = 0.0
      do {{ let t = Date(); let s = LanguageModelSession(instructions: gi)
            gate = try await s.respond(to: text, generating: Gate.self, options: opts).content.rawValue; gms = Date().timeIntervalSince(t) * 1000 }} catch {{ gate = "error"; FileHandle.standardError.write(Data("gate error: \\(error)\\n".utf8)) }}
      do {{ let t = Date(); let s = LanguageModelSession(instructions: ri)
            action = try await s.respond(to: text, generating: Action.self, options: opts).content.rawValue; rms = Date().timeIntervalSince(t) * 1000 }} catch {{ action = "error"; FileHandle.standardError.write(Data("route error: \\(error)\\n".utf8)) }}
      let out: [String: Any] = ["gate": gate, "action": action, "gate_ms": gms, "route_ms": rms]
      print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!); fflush(stdout)
    }}
  }}
}}
'''
os.makedirs("/tmp/apple_fm_bench", exist_ok=True)
open("/tmp/apple_fm_bench/run.swift", "w").write(swift)
subprocess.run(["xcrun", "swiftc", "-O", "-parse-as-library", "-o", "/tmp/apple_fm_bench/run", "/tmp/apple_fm_bench/run.swift"], check=True)
inp = "\n".join(json.dumps(t) for t in ["warm up"] + [r["text"] for r in rows]) + "\n"
proc = subprocess.run(["/tmp/apple_fm_bench/run"], input=inp, capture_output=True, text=True, check=True)
res = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()][1:]
assert len(res) == len(rows), (len(res), len(rows))
nc = sum(r["is_command"] for r in rows); nn = len(rows) - nc
t0s = [(fv.route(r["text"]) or (None,))[0] for r in rows]
def route_score(use_t0):
    right = fa = 0
    for r, x, a0 in zip(rows, res, t0s):
        act = a0 if (use_t0 and a0) else (x["action"] if x["action"] not in ("no_action", "error") else None)
        if not act: continue
        if r["is_command"]: right += act in ACCEPT[r["intent"]]
        else: fa += 1
    return {"correct_pct": round(100 * right / nc, 1), "false_accept_pct": round(100 * fa / nn, 1)}
if all(x["gate"] == "error" and x["action"] == "error" for x in res):   # e.g. the model is still downloading: do not overwrite real results with nothing
    sys.exit("Every call failed, no results written. First error: " + (proc.stderr.splitlines() or ["(none)"])[0])
out = {"heldout_rows": len(rows), "errors": sum(x["gate"] == "error" or x["action"] == "error" for x in res),
       "gate": {"commands_accepted_pct": round(100 * sum(x["gate"] == "command" for r, x in zip(rows, res) if r["is_command"]) / nc, 1),
                "non_commands_accepted_pct": round(100 * sum(x["gate"] == "command" for r, x in zip(rows, res) if not r["is_command"]) / nn, 1),
                "median_ms": round(statistics.median(x["gate_ms"] for x in res))},
       "router_alone": route_score(False), "router_after_tier0": route_score(True),
       "router_median_ms": round(statistics.median(x["route_ms"] for x in res))}
print(json.dumps(out, indent=1)); json.dump(out, open(os.path.join(HERE, "results", "apple_fm_gate_router" + ("_few_shot" if "--few-shot" in sys.argv else "") + ".json"), "w"), indent=1)
