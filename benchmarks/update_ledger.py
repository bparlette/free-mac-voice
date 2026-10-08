#!/usr/bin/env python3
"""Regenerate the AUTO sections of benchmarks/README.md from the saved result files, so the headline tables cannot drift from the data.

  ./.venv/bin/python benchmarks/update_ledger.py           # rewrite the tables in place (also refreshes the ledger section of the main README)
  ./.venv/bin/python benchmarks/update_ledger.py --check   # exit 1 if README.md is out of date (use before committing)

Everything outside the <!-- AUTO:... --> markers is hand-written history: add a dated row to the timeline when you run something new.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
README = os.path.join(HERE, "README.md")
ROOT_README = os.path.join(HERE, "..", "README.md")
ORDER = ["qwen3-vl:8b", "qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct", "qwen3-vl:4b", "qwen3-vl:2b-instruct", "qwen3.5:2b", "qwen3.5:0.8b"]


def models_table():
    rows = []
    for f in glob.glob(os.path.join(HERE, "model_size_eval", "results", "qwen3*.json")):
        rows.append(json.load(open(f)))
    rows.sort(key=lambda r: ORDER.index(r["model"]) if r["model"] in ORDER else 99)
    out = ["| Model | Resident | Speed | Routing, assistant's current code (of commands) | Non-commands acted on | Screen questions | General questions |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        rt, v, q = r["routing"], r["vision"], r["qa"]
        empty = q.get("empty_replies")
        out.append(f"| `{r['model']}` | {r['resident_gb']} GB | {r['generation_tok_s']} tok/s | {rt['correct']}/{rt['commands']} right, {rt['no_answer']} no answer ({rt['median_s']} s) "
                   f"| {rt['false_accepts']}/{rt['non_commands']} | {v['correct']}/{v['questions']} ({v['median_s']} s) "
                   f"| {q['correct']}/{q['questions']} ({q['median_s']} s{', ' + str(empty) + ' silent' if empty else ''}) |")
    return "\n".join(out)


def pipeline_table():
    names = [("pipeline_end_to_end.json", "Current pipeline"), ("pipeline_end_to_end_no_0.5b.json", "Tier 0.5b disabled"),
             ("pipeline_end_to_end_no_0.5b_8b_instruct.json", "Tier 0.5b disabled + 8B instruct (live setup)")]
    cols = []
    for fname, label in names:
        f = os.path.join(HERE, "pipeline_eval", "results", fname)
        if os.path.exists(f): cols.append((label, json.load(open(f))))
    if len(cols) < 2: return "_(pipeline results not found)_"
    n = cols[0][1]["n"]
    def row(label, fn): return "| " + label + " | " + " | ".join(fn(d) for _, d in cols) + " |"
    out = ["| | " + " | ".join(l for l, _ in cols) + " |", "|---|" + "---|" * len(cols),
           row("After wake word: commands routed to an acceptable action", lambda d: f"{d['after_wake']['commands_correct']}/{n}"),
           row("Wake window: commands correct", lambda d: f"{d['window']['commands_correct']}/{n}"),
           row("Wake window: wrong action", lambda d: str(d['window']['wrong_action'])),
           row("**Wake window: non-commands wrongly acted on**", lambda d: f"**{d['window']['false_triggers']}/{n}**"),
           row("...by tier", lambda d: json.dumps(d['window']['false_triggers_by_tier']))]
    return "\n".join(out)


SECTIONS = {"models": models_table, "pipeline": pipeline_table}


def render(text):
    for key, fn in SECTIONS.items():
        pat = re.compile(rf"(<!-- AUTO:{key}:START -->)\n.*?(<!-- AUTO:{key}:END -->)", re.S)
        if not pat.search(text): raise SystemExit(f"marker AUTO:{key} missing from README.md")
        text = pat.sub(lambda m: m.group(1) + "\n" + fn() + "\n" + m.group(2), text)
    return text


# Sections of benchmarks/README.md that are mirrored into the main README (between AUTO:ledger-<key> markers).
MIRROR = {"live": "What is live in the assistant because of these benchmarks", "standings": "Current standings",
          "timeline": "Timeline", "dropped": "Tried and dropped, or ruled out without running", "backlog": "Backlog (priority order)"}


def section(ledger, heading):
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", ledger, re.S | re.M)
    if not m: raise SystemExit(f"section '{heading}' not found in benchmarks/README.md")
    body = re.sub(r"^<!-- AUTO:[^\n]*-->\n", "", m.group(1), flags=re.M)   # drop the ledger's own generator markers
    return body.strip("\n")


def render_root(root_text, ledger_text):
    for key, heading in MIRROR.items():
        pat = re.compile(rf"(<!-- AUTO:ledger-{key}:START -->)\n.*?(<!-- AUTO:ledger-{key}:END -->)", re.S)
        if not pat.search(root_text): raise SystemExit(f"marker AUTO:ledger-{key} missing from the main README.md")
        body = section(ledger_text, heading).replace("](#", "](benchmarks/README.md#")   # in-page links in the ledger must point at the ledger file from the main README
        root_text = pat.sub(lambda m: m.group(1) + "\n" + body + "\n" + m.group(2), root_text)
    return root_text


if __name__ == "__main__":
    led_old = open(README).read(); led_new = render(led_old)
    root_old = open(ROOT_README).read(); root_new = render_root(root_old, led_new)
    if "--check" in sys.argv:
        if led_new != led_old or root_new != root_old:
            print("benchmark docs are out of date: run benchmarks/update_ledger.py"); sys.exit(1)
        print("benchmarks/README.md and the main README are up to date"); sys.exit(0)
    open(README, "w").write(led_new); open(ROOT_README, "w").write(root_new)
    print("updated" if (led_new != led_old or root_new != root_old) else "already up to date")
