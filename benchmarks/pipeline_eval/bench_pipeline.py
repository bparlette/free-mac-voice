#!/usr/bin/env python3
"""End-to-end check of the assistant's real pipeline (handle_command: wake word -> Tier 0 regex -> 0.5a embeddings -> 0.5b decision
model -> Tier 1 LLM) in dry-run, on the held-out half of the synthetic test set. STT and the wake-word detector are not part of
this; the utterance text is fed in as if transcribed. Nothing is executed or spoken, and the assistant's history/state files are not
written. External APIs (Gemini) are disabled.

Two paths are measured, because they differ in production:
  after_wake : "Mac, <phrase>"  -> straight to Tier 0; no command gate. Commands only: how many are routed to the right action.
  window     : wake window already open (the seconds after a real command), <phrase> arrives bare. Here the command gate (is_voice_command)
               guards anything Tier 0 does not match. Both commands and adversarial non-commands: false triggers = non-commands acted on.

  ./.venv/bin/python benchmarks/pipeline_eval/bench_pipeline.py [--n 80]
"""
import argparse, collections, json, os, random, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test, ACCEPT  # noqa: E402
import free_voice as fv  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=80); ap.add_argument("--out", default=os.path.join(HERE, "results"))
ap.add_argument("--no-decision-tier", action="store_true", help="disable Tier 0.5b (the tev1 decision-model router); the command gate keeps using tev1")
args = ap.parse_args()

ACC = {k: set(v) for k, v in ACCEPT.items()}
ACC["set_volume"] |= {"volume_up", "volume_down"}; ACC["mute"] |= {"tv_mute", "mute_toggle"}
for k in ("media_play_pause", "next_track"): ACC[k] |= {"media"}

fv.DRY_RUN = True; fv.GEMINI_API_KEY = ""
fv.say = lambda *a, **k: None; fv.notify_hud = lambda *a, **k: None; fv.update_state = lambda *a, **k: None
fv.record_attempt = lambda *a, **k: None; fv.acknowledge_wake = lambda *a, **k: None
fv.VOICE_WAKE_WORD = "mac"
if args.no_decision_tier:
    fv.VOICE_COMMAND_GATE_MODEL = fv.VOICE_COMMAND_GATE_MODEL or fv.OLLAMA_DECISION_MODEL; fv.OLLAMA_DECISION_MODEL = ""
logs = []
fv.log = lambda m, *a, **k: logs.append(str(m))

TIER_RE = [("Tier 0", re.compile(r"Tier 0 hit: (\w+)")), ("Tier 0.5a", re.compile(r"Tier 0\.5a \(embedding cosine=[\d.]+\) -> (\w+)")),
           ("Tier 0.5b", re.compile(r"Tier 0\.5b .*?-> (\w+)")), ("Tier 1", re.compile(r"Tier 1 \(.*?\) -> (\w+)"))]
def outcome(lines):
    for tier, rx in TIER_RE:
        for l in lines:
            m = rx.search(l)
            if m and m.group(1) != "none": return tier, m.group(1)
    if any("dropped by command gate" in l for l in lines): return "gate", None
    return None, None

def run(text, window):
    logs.clear()
    fv._wake_window_until = time.time() + 30 if window else 0.0
    fv._wake_window_opened_at = time.time() if window else 0.0
    t = time.perf_counter()
    try: fv.handle_command(("" if window else "mac, ") + text, quiet_miss=True, require_wake_word=True)
    except Exception as e: logs.append(f"EXC {e}")
    return outcome(list(logs)), time.perf_counter() - t

rows = split_test(0)[1]; rnd = random.Random(11)
cmds = rnd.sample([r for r in rows if r["is_command"]], args.n); non = rnd.sample([r for r in rows if not r["is_command"]], args.n)
res = {"n": args.n, "no_decision_tier": args.no_decision_tier}
for mode, window in (("after_wake", False), ("window", True)):
    tiers = collections.Counter(); ok = wrong = none = 0; lat = []; wrong_ex = []
    for r in cmds:
        (tier, act), dt = run(r["text"], window); lat.append(dt)
        if act is None: none += 1
        elif act in ACC[r["intent"]]: ok += 1; tiers[tier] += 1
        else: wrong += 1; wrong_ex.append((r["text"], r["intent"], act, tier))
    d = {"commands_correct": ok, "wrong_action": wrong, "not_handled": none, "which_tier_got_it_right": dict(tiers), "median_s": round(sorted(lat)[len(lat) // 2], 2), "wrong_examples": wrong_ex[:5]}
    if window:
        fa = collections.Counter(); fa_ex = []; gate_dropped = 0; nlat = []
        for r in non:
            (tier, act), dt = run(r["text"], True); nlat.append(dt)
            if tier == "gate": gate_dropped += 1
            elif act is not None: fa[tier] += 1; fa_ex.append((r["text"], act, tier))
        d.update({"non_commands": args.n, "false_triggers": sum(fa.values()), "false_triggers_by_tier": dict(fa), "stopped_by_gate": gate_dropped,
                  "false_trigger_examples": fa_ex[:8], "median_s_non_commands": round(sorted(nlat)[len(nlat) // 2], 2)})
    res[mode] = d
    print(mode, json.dumps({k: v for k, v in d.items() if "examples" not in k}), flush=True)
json.dump(res, open(os.path.join(args.out, "pipeline_end_to_end_no_0.5b.json" if args.no_decision_tier else "pipeline_end_to_end.json"), "w"), indent=1)
