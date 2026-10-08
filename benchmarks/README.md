# Benchmark ledger: what we measured, what we tried, what we changed

**Last updated: 2026-10-08.** This is the one file to read first. The headline tables below are regenerated from the saved result files by
`python benchmarks/update_ledger.py` (check with `--check`); everything else is hand-written history. **When you run something new:** save the raw
output in that folder's `results/`, add a dated row to the [timeline](#timeline), run the updater, commit. (The updater also copies the main sections into the
**Benchmark Ledger** section of the repository's top-level `README.md`; do not edit that copy by hand.)

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
| **Tier 0.5b (decision-model router) switched off** on the live setup (`OLLAMA_DECISION_MODEL=` empty in the local config; the code default is unchanged) | 2026-10-08 | End to end: false triggers in the wake window 33 -> 21 of 80, correct commands up |
| **Tier 1 / vision / answers model changed to `qwen3-vl:8b-instruct`** on the live setup (`OLLAMA_MODEL`); the code now pre-fills the answer start **only for thinking builds** | 2026-10-08 | Routing 51/60 vs 52/60 for the thinking build, with fewer false accepts (7 vs 9); general answers 12 s -> 0.5 s; screen questions 9.3 s -> 4.9 s; no silent replies |
| **Tier 0 regex tightened** (app-name slot must resolve; clause-after-comma guard; stricter generic `search` / `play` / `hit` rules) | 2026-10-08 | Tier 0 false triggers 43 -> 3 of 400 non-commands; wake-window false triggers 21 -> 13 of 80 with correct commands unchanged |
| Command-gate model kept resident (`keep_alive`) and warmed at startup | 2026-10-08 | A cold gate call took about 2.7 s against the 3 s timeout, and a timeout fails closed (drops the phrase) |

**Still open:** the code's *default* model is still the thinking `qwen3-vl:8b` (the installer pulls it), so making an instruct build the default for everyone is a separate decision. See [recommendations](#recommendations).

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
- **8B instruct without the pre-fill routes 51/60 with 7 false accepts** (1.35 s), matching the 8B thinking build's 52/60 with 9; with the pre-fill it scores 47/60. A harder screen/question set was **not run**.
- Cold start (model unloaded): 8B 6.6 s, 4B instruct 9.6 s.
- Qwen3.5 0.8B / 2B without `format=json`: routing 35 / 45 of 60 (with 20 / 11-15 false accepts).

### C. The whole pipeline, end to end (dry-run on real code; 80 commands + 80 adversarial non-commands)

In the real pipeline the command gate only guards the **wake window** (follow-up speech) and only for phrases the regex tier does not match.

<!-- AUTO:pipeline:START -->
| | Current pipeline | Tier 0.5b disabled | + 8B instruct | + Tier 0 regex tightened (live setup) |
|---|---|---|---|---|
| After wake word: commands routed to an acceptable action | 53/80 | 55/80 | 55/80 | 55/80 |
| Wake window: commands correct | 37/80 | 39/80 | 39/80 | 39/80 |
| Wake window: wrong action | 20 | 17 | 17 | 16 |
| **Wake window: non-commands wrongly acted on** | **33/80** | **22/80** | **21/80** | **13/80** |
| ...by tier | {"Tier 0.5b": 12, "Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 3} | {"Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 4} | {"Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 3} | {"Tier 0.5a": 7, "Tier 1": 6} |
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

### F. Earlier router bake-off, 2026-10-03: decision models vs qwen2.5:1.5b (`docs/router-benchmark.md`; 8 choices, 369 hand-written phrases)

A different test set from the tables above (numbers are not comparable across sets). "False triggers" = non-commands turned into actions; "missed" = commands answered `none`.

| Model | Accuracy | False triggers | Missed commands | Warm median | Size |
|---|---|---|---|---|---|
| **Kev-4B** (decision model) | **81.6%** | **9.2%** | 25.7% | 659 ms | 4.5 GB |
| lev | 79.4% | 26.3% | 3.9% | 1,505 ms | 4.5 GB |
| tev1:0.8b | 70.2% | 35.9% | 11.2% | 177 ms | 0.8 GB |
| qwen2.5:1.5b (then the router) | 67.8% | 43.3% | 4.6% | 336 ms | 1.0 GB |
| Julia-1 | 64.5% | 2.8% | 79.6% | 13 ms | 0.2 GB |
| Laya | 58.8% | 0.0% | 100% | 75 ms | 0.4 GB |

With 40 options: Kev-4B 83.0% (but 2.7 s per decision), qwen2.5:1.5b 68.6%, `tev1:0.8b` refuses more than 26 options. Takeaway then and now: decision models are good at saying "no"
(fewer false triggers) but slow down and get worse as the option list grows; Julia-1 and Laya are better as yes/no gates than as routers.

### G. Embedding backends and engines (`finetune_eval/results/`)

Correct command at about 16% false accepts (the router's own level), leak-free, Tier 0 first; held-out half of the intent set.

| Backend | No prefix / with task prefix | Single query | Memory | Notes |
|---|---|---|---|---|
| Ollama 0.35.0, EmbeddingGemma v1 | 61.8% | 16 ms (69 texts/s) | 648 MB loaded | the old live path |
| Ollama 0.40.0, EmbeddingGemma v1 | 61.8% | 10 ms (107 texts/s) | 648 MB loaded | generation speed unchanged (61 tok/s) |
| Ollama 0.40.0, EmbeddingGemma 2 270m | 54.9% / 52.0% | 15 ms | 330 MB | worse |
| **ONNX v1 q4, CPU (live default)** | **62.3% / 63.2%** | **6 ms** | **+0.10 GB** | 188 MB of weights |
| ONNX v1 q4f16, CPU | 61.8% / 63.7% | 8 ms | +0.15 GB | |
| ONNX v1 int8, CPU | 61.8% / 61.8% | 43 ms | +1.15 GB | slow and large |
| ONNX v2 q4 / q4f16 / fp16, CPU | 57.8% / 57.8% / 54.9% | 14 / 15 / 12 ms | +0.25 / +0.25 / +0.40 GB | worse than v1 |
| ONNX on CoreML (v1 q4f16 / v1 q4 / v1 int8 / all v2) | 61.8% / **0%** / 22% / fails | 13 ms / 131 ms / 270 ms | | CoreML provider unusable except q4f16 (slower than CPU) |

### H. Speech recognition: Phonon-2 vs Whisper

Covered by the benchmark section above in this README (`benchmarks/bench.py`, `results.jsonl`): Phonon-2 (Apple MLX) is about 7.2x faster than Whisper `base.en` on Apple silicon.

### I. Harder questions, 4B pipeline and wake word (2026-10-08)

**Hard questions** (`model_size_eval/bench_hard.py`; 10 screen questions on a dense task page, 10 reasoning questions):

| Model | Screen questions right | Reasoning right | Silent empty replies | Median reply (screen / reasoning) |
|---|---|---|---|---|
| qwen3-vl:2b-instruct | 9/10 | 6/10 | 0 | 1.3 s / 0.2 s |
| qwen3-vl:4b-instruct | 9/10 | **8/10** | 0 | 1.3 s / 0.4 s |
| qwen3-vl:8b-instruct (live) | 9/10 | 7/10 | 0 | 4.4 s / 0.6 s |
| qwen3-vl:8b (thinking) | 9/10 | 3/10 | 6 | 10.1 s / 16.3 s |

The 4B instruct build is at least as good as the 8B instruct build on these questions. The thinking 8B fails reasoning mostly by returning nothing. Samples are small (10 each): differences of one or two questions are noise.

**Full pipeline with 4B instruct as Tier 1** (`pipeline_eval/results/pipeline_end_to_end_tier0_tight_4b_instruct.json`): after the wake word 55/80 commands got an acceptable action (22 wrong, 3 not handled); in the wake window 40/80.

**Wake word** (`wake_eval/bench_wake.py`; Kokoro speech in 6 voices through the real recognizer, so indicative only):

| Parser | "Mac, <command>" recognised | TV-style lines that start with a name or verb (60 clips) woken | Non-command speech woken (360 clips) |
|---|---|---|---|
| Before 2026-10-08 (10 spellings, any sentence) | 100% | 56 | 3 |
| Now (command-aware: mac/mack/macs always; max/matt/mark/match/make/mike/mock only when a real command follows) | 98.6% | **16** | 3 |
| Strict (mac/mack/macs only) | 97.8% | 0 | 3 |

`VOICE_WAKE_LOOSE=always|command|off` selects the behaviour (default `command`).

### J. Backlog benches run without a microphone (2026-10-08)

**Command gate on its own** (`model_size_eval/bench_gate.py`; held-out half: 204 commands / 200 non-commands; "accepted" = P(command) >= 0.5):

| Gate model | Commands accepted | Non-commands accepted (false accepts) | Median latency | Size |
|---|---|---|---|---|
| `tev1:0.8b` (live) | 42.6% | 24.5% | 129 ms | 0.8 GB |
| **`tev1:4b`** | **83.8%** | **10.5%** | 419 ms | 4.4 GB |
| Kev-4B (Q4_K_M, llama-server 0.6.0) | 79.9% | 14.5% | 336 ms | 3.0 GB |

**Pipeline with the gate swapped** (4B instruct as Tier 1, Tier 0.5b off, 80 commands + 80 non-commands; the wake-window rows are the ones the gate affects):

| Setup | After wake word: correct | Wake window: correct | Wake window: false triggers |
|---|---|---|---|
| gate `tev1:0.8b` (live) | 55/80 | 40/80 | 13/80 |
| **gate `tev1:4b`** | 55/80 | **52/80** | **9/80** |
| gate `tev1:4b` + Tier 0.5b `tev1:4b` | 54/80 | 51/80 | 12/80 |

Non-commands that the gate rejects cost about 0.55 s median. `tev1:4b` as the Tier 0.5b decision model adds nothing, so that tier stays off. As a re-ranker behind the embedding tier (60 commands / 60 non-commands, shortlist of 8, `bench_decision_router.py`), `tev1:4b` gets 47/60 correct with 9/60 false triggers at P >= 0.5 (727 ms), versus 29/60 and 25/60 for `tev1:0.8b` and 25/60 and 6/60 for Kev-4B.

**Not runnable here:** Liquid `d1-omni-600M` and `d1-3B` (GGUF files pull into Ollama 0.40 but it rejects their decision type; the installed llama.cpp 0.6.0 says "unsupported decision model type: lfm2-d1"; llama.cpp's development branch has it, a local build was made but not run) and Amazon Strands Decider 2B (published as a LoRA adapter plus a head on a Qwen3.5 base, with no GGUF or Ollama build). Both remain untested.

**TTS: wait before the first word** (`tts_eval/bench_ttfa.py`, Kokoro af_heart, no playback; macOS `say` could not be timed while the audio daemon is hung). The assistant synthesizes the whole reply before playing it:

| Reply length | Synthesize whole reply | Synthesize first sentence only |
|---|---|---|
| 6 words | 0.44 s | 0.43 s |
| 20 words | 1.35 s | 1.20 s |
| 50 words | 3.62 s | 1.18 s |
| 100 words | 6.91 s | 1.15 s |
| 200 words | 13.4 s | 1.14 s |

Speaking sentence by sentence keeps the wait near 1.2 s for any length. **Implemented 2026-10-08** (`VOICE_TTS_STREAM`, replies of 160+ characters with more than one sentence).

**Replay of logged utterances** (`finetune_eval/replay_logged_utterances.py`): only 59 unique real utterances were in the log (most rows are ambient). At the 0.82 threshold both embedding backends accepted 15 (14 of them the same ones; 13 of those with the same action) and agreed on the top action for 51 of 59. Mean cosine 0.754 (ONNX) vs 0.751 (Ollama). Small sample, but no sign the ONNX default behaves differently on real speech-to-text output.

## Recommendations

| # | Recommendation | Evidence | Status |
|---|---|---|---|
| 1 | Disable Tier 0.5b (the `tev1:0.8b` action router) | End to end: false triggers in the wake window 33 -> 22 of 80, correct commands up. It got 2 commands right and caused 12 false triggers and `quit_app` misroutes. Also weak in `docs/router-benchmark.md` (36% false triggers) and as a re-ranker | **Done on the live setup (config); not the code default** |
| 2 | Fix the Tier 0 regex false triggers (12 of 80) and the `close this one` -> `quit_app` mapping | Pipeline check | **Done** (43 -> 3 false triggers at Tier 0; 25 real commands that Tier 0 mishandled now fall to the embedding / LLM tiers). `close this one` is still routed to `quit_app` by a later tier |
| 3 | If switching the LLM: prefer an **instruct** build and skip the answer pre-fill for non-thinking models | Model-size results; 8B instruct 51/60 without the pre-fill | **Done** (code skips the pre-fill for instruct builds; live setup uses 8B instruct) |
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
| 2026-10-08 | Tier 0 regex tightening (simulated on the data first, then applied) | 43 -> 3 false triggers on 400 non-commands; 144 -> 119 real commands matched at Tier 0 (the other 25 were being mishandled there). End to end: wake-window false triggers 21 -> 13 of 80, correct commands unchanged | `pipeline_eval/`, `intent_eval/` |
| 2026-10-08 | 8B instruct with / without the pre-filled answer start; pipeline with the planned live setup | 51/60 without (47/60 with); pipeline false triggers 21/80. **Applied to the live setup** (Tier 0.5b off, 8B instruct) | `model_size_eval/`, `pipeline_eval/` |
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

1. Move the command gate to `tev1:4b` (section J: wake-window correct 40 -> 52 of 80, false triggers 13 -> 9; +3.6 GB memory).
3. Liquid d1 models and Amazon Strands Decider 2B: need a decision-capable runtime (llama.cpp development build) or the Strands Python stack.
4. Real-audio false-wake test (hours of TV and podcasts) and the live ONNX-vs-Ollama comparison on real speech (`TIER05_EMBED_BACKEND=compare`); both need a working microphone.
5. Compositor load at true idle: plain 1080p vs 4K-backed mode, wallpaper, Safari content (needs display changes and your OK).
6. Fine-tune a decision head on the assistant's own labeled utterances (data was the biggest lever).

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
