"""Build mlx_lm LoRA data. Variant A: production examples + hand-written negatives only.
Variant B: A + a stratified half of the test set (evaluate on the other half).
Test phrases are never duplicated into training in variant A (exact-match filter)."""
import json, os, random, sys
from common import *

def write(dirname, pairs, seed=1):
    os.makedirs(dirname, exist_ok=True); rnd = random.Random(seed); rnd.shuffle(pairs)
    cut = max(20, len(pairs) // 10)
    for name, part in (("train", pairs[cut:]), ("valid", pairs[:cut])):
        with open(os.path.join(dirname, name + ".jsonl"), "w") as f:
            for t, lab in part: f.write(json.dumps({"prompt": prompt(t), "completion": " " + lab}) + "\n")
    print(dirname, "train", len(pairs) - cut, "valid", cut)

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ft"
test_norms = {norm(r["text"]) for r in load_test()}
ex = [(t, a) for t, a in production_examples() if norm(t) not in test_norms]
neg = [(l.strip(), "none") for l in open(os.path.join(HERE, "negatives.txt")) if l.strip() and norm(l) not in test_norms]
print("examples", len(ex), "(dropped", len(production_examples()) - len(ex), "that appear in the test set); negatives", len(neg))
write(os.path.join(out, "A"), ex + neg)
half, _ = split_test(0)
extra = [(r["text"], PRIMARY[r["intent"]] if r["is_command"] else "none") for r in half]
write(os.path.join(out, "B"), ex + neg + extra)
