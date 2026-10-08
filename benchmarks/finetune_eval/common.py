"""Shared bits for the fine-tune experiment. Label vocabulary = the router's own action names + 'none'."""
import json, os, random, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
TESTSET = os.path.join(ROOT, "benchmarks", "intent_eval", "testset.jsonl")

# test-set intent name -> router action names that count as correct
ACCEPT = {"open_app": {"open_app"}, "switch_app": {"switch_app"}, "quit_app": {"quit_app"}, "set_volume": {"set_volume"},
          "mute": {"mute_toggle"}, "media_play_pause": {"media"}, "next_track": {"media"}, "timer": {"timer"},
          "web_search": {"web_search"}, "snap_left": {"snap_left"}, "snap_right": {"snap_right"},
          "maximize": {"maximize_window"}, "minimize": {"minimize"}, "close_window": {"close_window"},
          "screenshot": {"screenshot"}, "lock_screen": {"lock"}, "tv_power": {"tv_power"}}
PRIMARY = {k: sorted(v)[0] for k, v in ACCEPT.items()}

def norm(t): return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()
def prompt(text): return f"Voice command: {text}\nAction:"
def load_test(): return [json.loads(l) for l in open(TESTSET) if l.strip()]

def split_test(seed=0):
    """Stratified 50/50 split of the test set: (train_half, heldout_half)."""
    rows = load_test(); rnd = random.Random(seed); groups = {}
    for r in rows: groups.setdefault(r["intent"] if r["is_command"] else "_none", []).append(r)
    a, b = [], []
    for g in groups.values():
        rnd.shuffle(g); h = len(g) // 2; a += g[:h]; b += g[h:]
    return a, b

def production_examples():
    sys.path.insert(0, ROOT)
    os.environ.setdefault("FREE_VOICE_QUIET", "1")
    import free_voice as fv
    return [(t, act) for act, lst in fv._INTENT_EXAMPLES.items() for t in lst]
