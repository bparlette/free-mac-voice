#!/usr/bin/env python3
"""Turn the assistant's own log (~/.free-voice/attempts.jsonl) into a short list of phrases worth labelling, and add the confirmed ones to the
private training file (~/.free-voice/tier05_personal.jsonl, read by the Tier 0.5a classifier at startup; never committed).

  python3 scripts/review_attempts.py                 # list candidates, newest first, with a suggested label
  python3 scripts/review_attempts.py --apply 3:open_app 5:none 7:      # confirm: N:action (N: = accept the suggestion, N:none = not a command)
Candidates: phrases the assistant could not route, routed by a model tier (not the exact regex) or rejected by the gate, one row per
distinct phrase. Router action names are the keys of free_voice._INTENT_EXAMPLES. Restart the assistant to retrain.
"""
import argparse, json, os, re, sys
HOME = os.path.expanduser("~")
ATTEMPTS = os.path.join(HOME, ".free-voice", "attempts.jsonl")
PERSONAL = os.environ.get("TIER05_PERSONAL_FILE", os.path.join(HOME, ".free-voice", "tier05_personal.jsonl"))
CACHE = os.path.join(HOME, ".free-voice", "tier05_review_candidates.json")
SKIP_TIERS = {"Hallucination", "Speaker ID", "Tier 0"}   # known noise, and phrases the exact regex already handles


def router_actions():
    """Valid labels = the router's own action names (keys of free_voice._INTENT_EXAMPLES)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    os.environ.setdefault("FREE_VOICE_QUIET", "1")
    try:
        import free_voice
        return set(free_voice._INTENT_EXAMPLES)
    except Exception:  # noqa: BLE001
        return set()


def norm(t): return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


def candidates(limit, actions=None):
    actions = router_actions() if actions is None else actions
    seen_personal = set()
    if os.path.exists(PERSONAL):
        with open(PERSONAL, encoding="utf-8") as f:
            for ln in f:
                try: seen_personal.add(norm(json.loads(ln)["text"]))
                except (ValueError, KeyError): pass
    rows, seen = [], set()
    with open(ATTEMPTS, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for ln in reversed(lines):
        try: r = json.loads(ln)
        except ValueError: continue
        u = (r.get("utterance") or "").strip(); k = norm(u)
        if not k or k in seen or k in seen_personal or u.startswith("(") or r.get("tier") in SKIP_TIERS: continue
        seen.add(k)
        if r.get("status") == "SUCCESS" and r.get("action") in actions: sug = r["action"]
        elif r.get("tier") in ("Command Gate", "Gate") or "gate" in (r.get("detail") or "").lower(): sug = "none"
        else: sug = ""
        rows.append({"text": u, "suggested": sug, "status": r.get("status"), "tier": r.get("tier"), "time": r.get("time")})
        if len(rows) >= limit: break
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", nargs="*"); ap.add_argument("--limit", type=int, default=40); a = ap.parse_args()
    if a.apply is None:
        rows = candidates(a.limit)
        with open(CACHE, "w") as f: json.dump(rows, f)
        for i, r in enumerate(rows, 1):
            print(f"{i:3d}. {r['text'][:70]:70s} | {r['status']} {r['tier']} | suggest: {r['suggested'] or '?'}")
        print(f"\n{len(rows)} candidates. Confirm with: --apply N:action  (N: accepts the suggestion; N:none = not a command)")
        return
    with open(CACHE) as f: rows = json.load(f)
    added = 0; valid = router_actions()
    os.makedirs(os.path.dirname(PERSONAL), exist_ok=True)
    with open(PERSONAL, "a", encoding="utf-8") as f:
        for tok in a.apply:
            n, _, act = tok.partition(":")
            try: r = rows[int(n) - 1]
            except (ValueError, IndexError): print("skip", tok); continue
            act = act or r["suggested"]
            if not act: print("skip (no label)", tok); continue
            if act != "none" and act not in valid: print("skip (not a router action):", tok); continue
            f.write(json.dumps({"text": r["text"], "action": act}) + "\n"); added += 1
    print(f"added {added} phrases to {PERSONAL}")


if __name__ == "__main__":
    main()
