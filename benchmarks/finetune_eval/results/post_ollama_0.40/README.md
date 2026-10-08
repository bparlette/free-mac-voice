# Re-run after upgrading Ollama 0.35.0 -> 0.40.0 (2026-10-08)

Same code, same held-out split, same adapters. Compare with `../finetuned_qwen.txt` and `../../../intent_eval/README.md`.

| Measure | Before | After | Change |
|---|---|---|---|
| Unit tests | 383 pass, 1 skipped | 383 pass, 1 skipped | none |
| Router, leak-free baseline (thr 0.82) | 61.8% correct, 16.0% false accepts | 61.8%, 16.0% | identical |
| Fine-tuned Qwen 0.5B "A3" (conf >= 0.7) | 67.2% / 9.5% | 67.2% / 9.5% | identical |
| Fine-tuned Qwen 0.5B "B2" (conf >= 0.7) | 81.4% / 5.0% | 81.4% / 5.0% | identical |
| Production router under the live default (ONNX), thr 0.82 | 90.5% any-match, 6.5% false accepts (800-phrase set, leaky) | 90.5%, 6.5% | identical |
| Embedding through Ollama (single query / batch) | 16 ms / 69 per s (0.35.0) | 10 ms / 110 per s | faster (no longer on the live path; ONNX is the default) |
| qwen2.5:1.5b generation | 61 tok/s | 61 tok/s | same |
| Command gate (`/v1/systemone`, tev1:0.8b) | 15 of 16 identical choices vs 0.35 | works; 4 spot checks correct, median 130 ms | compatible |
| qwen3-vl:8b generation (no earlier figure) | n/a | 16.3 tok/s, 5.2 GB resident | baseline for later comparison |

Notes: the "peak memory growth" numbers printed in `finetuned_qwen.txt` differ from the earlier run (0.24 / 0.00 GB vs 0.44 / 0.39 GB).
That is an artefact of how the peak is measured (it depends on what the process had already allocated), not a real change.
These runs happened with the voice service running, so latencies are slightly pessimistic.
