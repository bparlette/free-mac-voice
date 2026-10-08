# Router intent eval

`testset.jsonl`: 400 real commands (17 intents, casual phrasing, STT mishearings) and 400 adversarial
non-commands (TV / podcast / ads / household talk that sits close to commands).

    ./.venv/bin/python benchmarks/intent_eval/run.py            # Tier 0 + Tier 0.5a
    ./.venv/bin/python benchmarks/intent_eval/run.py --gate     # also the Ollama command gate

Nothing is executed. Needs Ollama + `embeddinggemma` (the script refuses to report hashing-fallback numbers).

## Read the numbers carefully
* The test phrases are **not** all unseen: any phrase that also appears in `_INTENT_EXAMPLES` is trivially
  matched. Report recall only on phrases that are not in the example set (see the leak check in the git
  history of this README) and never add test phrases verbatim as examples.
* The set is deliberately nasty. Real TV false-accept rates are lower, and in production Tier 0.5a is only
  reached after the wake word + speaker ID.

## Results (2026-10-06, embeddinggemma, leak-free)
| examples | threshold | command recall | non-command false accepts |
|---|---|---|---|
| old (162) | 0.78 | 72.6% | 9.0% |
| new (252) | 0.78 | 84.3% | 11.7% |
| new (252) | **0.82 (now default)** | 77.5% | 6.5% |
| new (252) | 0.86 | 65.8% | 2.2% |

Tier 0 regex alone matches 10.8% of the non-commands ("open the door", "close but no cigar", "search no
further…"), and the Ollama command gate passed 44.8% of non-commands vs 71.5% of commands, so it is a weak
filter. Candidate next steps: tighten Tier 0 patterns that take free text (`open|close|quit|search (.+)`)
so the object must resolve to a real app, and replace the gate model.
An "anti-example" filter (reject when a known non-command is closer than the best command) cut held-out false
accepts from 13.5% to 10% at ~0 recall cost but is not adopted yet.

> **Correction (see `benchmarks/finetune_eval/`):** the "command recall" above counts a command as recalled if *any*
> action matched, not if the *correct* action matched, and the 75 leaked phrases flatter it further. Scored on the
> correct action with leaked examples removed, the router is about 62% correct at 16% false accepts on a held-out half.
