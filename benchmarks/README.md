# Benchmark ledger: what we measured, what we tried, what we changed

**Last updated: 2026-10-08.** This is the one file to read first. The headline tables below are regenerated from the saved result files by
`python benchmarks/update_ledger.py` (check with `--check`); everything else is hand-written history. **When you run something new:** save the raw
output in that folder's `results/`, add a dated row to the [timeline](#timeline), run the updater, commit.

## Read this first

- **Numbers are only comparable inside the same test set.** Folders use different sets (listed in the [folder index](#folder-index)). All phrases are
  synthetic and mostly AI-written; no real audio or transcripts. Treat gaps under about 5 points as noise (about 200 phrases per side).
- **"Correct" means the right action was chosen**, not just "something matched" (the first intent harness counted any match and overstated recall; see the timeline).
- Latencies were measured with the voice service and Ollama running, on an Apple M4 with 16 GB, so they are somewhat pessimistic.

## What is live in the assistant because of these benchmarks

| Change | Date | Why |
|---|---|---|
| Tier 0.5a embeddings run **in-process with ONNX** by default (`TIER05_EMBED_BACKEND`, falls back to Ollama if the model is missing) | 2026-10-07 | Same accuracy as the Ollama copy at about 6 ms and 0.1 GB instead of 10-16 ms and about 650 MB |
| Tier 0.5a threshold 0.82 | 2026-10-06 | Better recall and fewer false accepts than 0.78 on the then-current test |
| Speaker verification removed | 2026-10-08 | Wake word makes it unnecessary for this setup (the model was only a weak second filter) |
| App-name lookup handles polite phrasing ("can you launch chrome please") | 2026-10-08 | Tier 0.5a matched the intent but dropped it for lack of an app name |
| Ollama upgraded 0.35 -> 0.40 | 2026-10-07 | Identical accuracy; embeddings through Ollama faster; command-gate endpoint compatible |

**Supported by the data but not applied yet** (needs an owner decision): disable Tier 0.5b (`OLLAMA_DECISION_MODEL=` empty), tighten the Tier 0 regex, and
skip the pre-filled answer start for non-thinking models so a 4B/8B *instruct* model can be used. See [recommendations](#recommendations).

## Current standings

### A. Tier 0.5a embedding tier and small routers (held-out half of the intent set: 204 commands / 200 non-commands)

| Approach | Correct command | Non-commands acted on | Size / speed |
|---|---|---|---|
| Router as it was deployed (examples included 75 test phrases: inflated) | 73.5% | 17.0% | n/a |
| **Router, leak-free** (Ollama EmbeddingGemma v1, threshold 0.82, regex tier first) | **61.8%** | **16.0%** | about 650 MB in Ollama; 10 ms |
| **ONNX q4 EmbeddingGemma v1, in-process (now the default)** | 62.3% (63.2% with Google's task prefix) | at the same 16% | **+0.1 GB; 6 ms** |
| EmbeddingGemma 2 (Ollama 270m / ONNX q4) | 54.9% / 57.8% | at the same 16% | 330 MB / +0.25 GB: worse, not adopted |
| Fine-tuned Qwen2.5-0.5B, trained on the router's own examples + hand-written negatives | 65.7% (conf >= 0.8) | 9.5% | +0.4 GB; 45 ms |
| Same, trained on about 2.4x as much in-distribution data | **81.4%** (conf >= 0.7) | **5.0%** | same: data is the lever |
| Laya decision model, zero-shot | 52.0% | 63.5% | 2.7 GB; 325 ms: dropped |

### B. The local LLM (Tier 1 routing, screen questions, answers): Qwen3-VL and Qwen3.5 sizes

Same 60 commands / 60 non-commands (held-out), 12 screen questions, 10 general questions, using the assistant's own functions. "Routing, current code" uses
the assistant's JSON-output request, which Ollama 0.40 refuses for Qwen3.5 (so those rows show 0). Routing without the code's pre-filled answer start, and the
other routers, are in the notes below the table.

<!-- AUTO:models:START -->
| Model | Resident | Speed | Routing, assistant's current code (of commands) | Non-commands acted on | Screen questions | General questions |
|---|---|---|---|---|---|---|
| `qwen3-vl:8b` | 5.17 GB | 16.3 tok/s | 52/60 right, 3 no answer (1.39 s) | 9/60 | 12/12 (9.3 s) | 8/10 (12.1 s, 1 silent) |
| `qwen3-vl:8b-instruct` | 5.17 GB | 15.8 tok/s | 47/60 right, 9 no answer (1.41 s) | 7/60 | 12/12 (4.9 s) | 9/10 (0.5 s) |
| `qwen3-vl:4b-instruct` | 3.02 GB | 27.4 tok/s | 9/60 right, 50 no answer (0.17 s) | 0/60 | 12/12 (4.1 s) | 9/10 (0.3 s) |
| `qwen3-vl:4b` | 3.02 GB | 29.6 tok/s | 9/60 right, 48 no answer (0.17 s) | 1/60 | 11/12 (5.6 s) | 7/10 (8.0 s, 3 silent) |
| `qwen3-vl:2b-instruct` | 1.64 GB | 53.7 tok/s | 12/60 right, 47 no answer (0.1 s) | 1/60 | 12/12 (1.5 s) | 9/10 (0.1 s) |
| `qwen3.5:2b` | 3.0 GB | 32.8 tok/s | 0/60 right, 60 no answer (0.0 s) | 0/60 | 12/12 (2.8 s) | 8/10 (0.5 s) |
| `qwen3.5:0.8b` | 1.29 GB | 68.7 tok/s | 0/60 right, 60 no answer (0.0 s) | 0/60 | 10/12 (1.4 s) | 7/10 (0.1 s) |
<!-- AUTO:models:END -->

Notes (from `model_size_eval/README.md`):
- The default tags `qwen3-vl:8b` / `:4b` are **thinking** builds: general answers take about 12 s on the 8B and the answer can come back **empty** (the model spends its
  token budget reasoning). **Instruct** builds answer in 0.3-0.5 s.
- With the pre-fill skipped, **4B instruct routes 51/60 (15 false accepts)**; the same pre-fill that helps thinking models collapses it to 9/60.
- The 8B instruct's routing without the pre-fill, and a harder screen/question set, were **not run**.
- Cold start (model unloaded): 8B 6.6 s, 4B instruct 9.6 s.
- Qwen3.5 0.8B / 2B without `format=json`: routing 35 / 45 of 60 (with 20 / 11-15 false accepts).

### C. The whole pipeline, end to end (dry-run on real code; 80 commands + 80 adversarial non-commands)

In the real pipeline the command gate only guards the **wake window** (follow-up speech) and only for phrases the regex tier does not match.

<!-- AUTO:pipeline:START -->
| | Current pipeline | Tier 0.5b disabled |
|---|---|---|
| After wake word: commands routed to an acceptable action | 53/80 | 55/80 |
| Wake window: commands correct | 37/80 | 39/80 |
| Wake window: wrong action | 20 | 17 |
| **Wake window: non-commands wrongly acted on** | **33/80** | **22/80** |
| ...by tier | {"Tier 0.5b": 12, "Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 3} | {"Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 4} |
<!-- AUTO:pipeline:END -->

### D. Voice output (TTS): Kokoro vs Paradee (`tts_eval/`)

| | Speed | Short phrase | Process memory | Word error (Whisper round trip) |
|---|---|---|---|---|
| Kokoro (the assistant's voice) | 4.3x real time | 450 ms | 0.66 GB | 4.7% |
| Paradee int8 (one female voice) | 17.7x | 131 ms | 0.54 GB (its phonemizer takes 0.37 GB) | 3.5% |

Not adopted: memory barely improves, the voice changes, and it needs Python 3.12 (spaCy has no build for 3.14). Subjective quality was not judged.

### E. Other measurements (not in a results folder)

- Display refresh 120 Hz -> 60 Hz on the TV: macOS compositor CPU 43.8% -> 41.3%, so refresh rate was not the main cost (the display runs a 4K-backed mode; idle load stayed near 38%).
- Ollama 0.35 -> 0.40: embeddings 16 -> 10 ms, generation speed unchanged (61 tok/s on qwen2.5:1.5b); the 0.40 server re-saved every model in a new format and **kept the old copies** (store grew 8.9 -> 35 GB until test downloads were removed).

## Recommendations

| # | Recommendation | Evidence | Status |
|---|---|---|---|
| 1 | Disable Tier 0.5b (the `tev1:0.8b` action router) | End to end: false triggers in the wake window 33 -> 22 of 80, correct commands up. It got 2 commands right and caused 12 false triggers and `quit_app` misroutes. Also weak in `docs/router-benchmark.md` (36% false triggers) and as a re-ranker | **Open: needs owner OK** |
| 2 | Fix the Tier 0 regex false triggers (12 of 80) and the `close this one` -> `quit_app` mapping | Pipeline check | Open |
| 3 | If switching the LLM: prefer an **instruct** build and skip the answer pre-fill for non-thinking models | Model-size results | Open |
| 4 | Keep `tev1:0.8b` as the yes/no command gate only | Gate works on Ollama 0.40; poor as an action router | Done (current behaviour) |

## Timeline

| Date | What | Result | Where |
|---|---|---|---|
| 2026-10-03 | Router bake-off: qwen2.5:1.5b vs decision models (Kev-4B, lev, tev1:0.8b, Julia-1, Laya); 8-option and 40-option tests | Kev-4B best: 81.6% accuracy, 9.2% false triggers, 0.66 s, 4.7 GB. tev1:0.8b 70.2% / 35.9% (max 26 options). qwen2.5:1.5b 67.8% / 43.3%. Laya, Julia-1 too cautious as routers; fine as yes/no gates | `docs/router-benchmark.md` |
| 2026-10-06 | Embedding bake-off: n-gram hash vs nomic-embed-text vs EmbeddingGemma v1 vs v2 (small hand-written set) | EmbeddingGemma v1 chosen | `embed_bakeoff/` |
| 2026-10-06 | Intent-eval harness (800 phrases); examples expanded; threshold 0.78 -> 0.82 | Reported 77.5% recall / 6.5% false accepts. **Later corrected**: it counted any match, and 75 test phrases were in the examples | `intent_eval/` |
| 2026-10-06 | Speaker ID: legacy model (same-speaker 0.26 vs other 0.0) -> WeSpeaker CAM++ (0.48 vs 0.03) | Better margin; feature **removed 2026-10-08** | (history) |
| 2026-10-07 | Fine-tune bake-off: router vs fine-tuned Qwen 0.5B (MLX LoRA) vs Laya zero-shot vs EmbeddingGemma 2 | Fair router baseline 61.8% / 16%; fine-tune beats it (65.7% / 9.5%, or 81.4% / 5.0% with more data); Laya dropped. Three failed first attempts documented (eval bug, divergence, collapse) | `finetune_eval/` |
| 2026-10-07 | ONNX vs Ollama embeddings; Ollama 0.35 vs 0.40; ONNX variants and CoreML | ONNX q4 matches accuracy at 6 ms / 0.1 GB: **made the default**. CoreML provider broken. Int8 slow | `finetune_eval/results/` |
| 2026-10-07 | TTS: Kokoro vs Paradee | Faster, equally intelligible, not worth switching | `tts_eval/` |
| 2026-10-08 | Qwen3-VL 8B (thinking) vs 4B (thinking) vs 4B / 8B instruct | Thinking builds are slow and sometimes silent; instruct builds fix it; 4B instruct routes like the 8B once the pre-fill is skipped | `model_size_eval/` |
| 2026-10-08 | End-to-end pipeline dry-run, with and without Tier 0.5b | 33 -> 22 of 80 false triggers without 0.5b | `pipeline_eval/` |
| 2026-10-08 | `tev1:0.8b` as action router (embedding shortlist + decision model) | Worse than embeddings alone; the API takes 2-26 options | `model_size_eval/` |
| 2026-10-08 | Qwen3.5 0.8B / 2B and qwen3-vl 2B instruct | 2B instruct: 1.6 GB, 12/12 screen questions in 1.5 s, weak router. Qwen3.5 refused by Ollama's JSON mode | `model_size_eval/` |

## Tried and dropped, or ruled out without running

| Item | Why |
|---|---|
| Laya (zero-shot), `laya-mlx` | Weak as a router (52% / 63.5% false accepts) and 2.7 GB; the MLX build is a one-person conversion |
| EmbeddingGemma 2 (Ollama, ONNX), ONNX int8, ONNX on the CoreML provider | Less accurate than v1, slow, or broken |
| Paradee TTS | See section D |
| Unsloth Studio install, Unsloth decision-model fine-tuning | System-level install with no clear Apple-silicon training path; MLX LoRA used instead |
| OpenAI Decisions API (cloud) | Would send speech text off the device |
| Qwen3.8-27B variants (DFlash2, ANEMLL Neural Engine package, abliterated build), Underdog Saluki 27B | Too large for 16 GB next to the rest, mostly NVIDIA-only, or unsafe |
| Cloudflare Clef-flash 9B, Nimble 9B | Need roughly 41 GB of memory or more |
| Gemma 4 E2B | 7 GB download despite the name |

## Backlog (priority order)

1. Apply and re-verify recommendation 1 (disable Tier 0.5b) in the live assistant; then tighten the regex tier.
2. Full pipeline with 4B instruct and 8B instruct as Tier 1 (needs the pre-fill skipped for instruct models).
3. Harder screens and reasoning questions for the 2B / 4B (the current ones are easy).
4. 8B instruct without the pre-fill (6 GB download).
5. New decision models: Liquid d1-omni-600M and d1-3B (released 2026-10-07; needs a runtime that serves `/v1/systemone`, which the installed llama.cpp lacks), Amazon Strands Decider 2B (2026-10-01), `tev1:4b`, and a re-test of Kev-4B end to end.
6. Real-audio false-wake test (hours of TV and podcasts) and the wake-word false-wake rate.
7. TTS listening test and time to first spoken word on long replies.
8. Compositor load at true idle: plain 1080p vs 4K-backed mode, wallpaper, Safari content.
9. Live ONNX-vs-Ollama comparison on real speech (`TIER05_EMBED_BACKEND=compare`, then grep the log).
10. Fine-tune a decision head on the assistant's own labeled utterances (data was the biggest lever).

## Folder index

| Folder | What it holds | Test set |
|---|---|---|
| `embed_bakeoff/` | n-gram hash vs nomic vs EmbeddingGemma | 77 positives / 30 negatives (hand-written) |
| `intent_eval/` | Router recall and false accepts (`run.py`); `testset.jsonl` | 400 commands + 400 adversarial non-commands, 17 intents |
| `finetune_eval/` | Fine-tuned small models, Laya, ONNX/Ollama embedding and engine benchmarks, "not in the bake-off" record | held-out half of `intent_eval/testset.jsonl` |
| `model_size_eval/` | Qwen3-VL / Qwen3.5 sizes, decision-model router | 60 + 60 held-out phrases, 12 screen questions, 10 general questions |
| `pipeline_eval/` | End-to-end dry-run of the real pipeline | 80 + 80 held-out phrases |
| `tts_eval/` | Kokoro vs Paradee | 10 typical assistant replies |
| `bench.py`, `results.jsonl` | Earlier component timings (speech recognition etc.) | n/a |
| `../docs/router-benchmark.md` | 2026-10-03 router vs decision-model bake-off | 369 + 264 hand-written phrases |
