# End-to-end pipeline check (dry-run)

Runs the assistant's real `handle_command` (wake word -> Tier 0 regex -> 0.5a embeddings -> 0.5b decision model -> Tier 1 LLM, with
the command gate) on the held-out half of the synthetic test set: 80 commands and 80 adversarial non-commands (TV, podcast, narration
that contain command words). Speech-to-text and the wake-word detector are not included (text goes straight in). Nothing is executed or
spoken, the assistant's history/state files are not written, and external APIs are off. `bench_pipeline.py`.

How the real pipeline differs by path (read from `handle_command`):
- **After the wake word** ("Mac, ..."): goes straight to Tier 0. **No command gate.**
- **Inside the open wake window** (the seconds after a real command): the gate (`is_voice_command`) only runs when Tier 0 did *not* match.
  Anything the regex matches is acted on without the gate.

## Results (one run each, 80 + 80 phrases; differences under about 5 points are noise)

| | Current pipeline | **Tier 0.5b (decision model) disabled** |
|---|---|---|
| After wake word: commands routed to an acceptable action | 53 / 80 | **55 / 80** |
| After wake word: wrong action | 26 | 23 |
| Wake window: commands correct | 37 / 80 | **39 / 80** |
| Wake window: wrong action | 20 | 17 |
| **Wake window: non-commands wrongly acted on** | **33 / 80** | **22 / 80** |
| ...by tier | 0.5b: 12, Tier 0 regex: 12, 0.5a: 6, Tier 1: 3 | Tier 0 regex: 12, 0.5a: 6, Tier 1: 4 |
| Non-commands stopped by the command gate | 41 | 40 |

## What it says

1. **Tier 0.5b (the `tev1:0.8b` decision-model router) is a net negative here.** It got 2 commands right and caused 12 false triggers
   plus bad misroutes ("snap it left mate" -> `quit_app`). Turning it off improved every row. (`OLLAMA_DECISION_MODEL=` empty disables it;
   the command gate uses a separate setting, `VOICE_COMMAND_GATE_MODEL`, and is unaffected.)
2. **The Tier 0 regex is now the largest source of false triggers (12 of 80)**, e.g. "minimise the risk of fire by unplugging chargers"
   -> `minimize_app`. It also maps "close this one" to `quit_app`. The gate cannot help because the regex runs first.
3. **About a quarter of non-commands still get through in the wake window** after fix 1. Real TV chatter is less adversarial than this
   set, and the wake word itself must be said first, but the window is the exposed path.

## Caveats
- Synthetic, adversarial set (built to contain command words). Real false-trigger rates will be lower; the wake-word false-wake rate on
  real TV audio was **not** measured.
- "Wrong action" includes some naming differences between the regex tier's action names and the labels (e.g. `open system settings` ->
  `settings`, which is arguably correct), so wrong counts are slightly overstated.
- One run per setting; Ollama and the voice service were running, so latencies are indicative only.

## Reproduce
```bash
./.venv/bin/python benchmarks/pipeline_eval/bench_pipeline.py --n 80
./.venv/bin/python benchmarks/pipeline_eval/bench_pipeline.py --n 80 --no-decision-tier
```
