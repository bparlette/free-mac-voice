# Voice-command router bake-off: qwen2.5 vs TypeSafe decision models on an M4 Mac mini

*October 3, 2026. All test phrases are synthetic. No real user audio or transcripts were used.*

## What this tests

free-mac-voice turns speech into Mac actions. After speech-to-text and a fast embedding match, a small local model acts as the **router**. It decides which action a phrase means, or that it isn't a command at all. That second job matters most: when the TV or a podcast is playing nearby, a router that "hears" commands in background speech will open apps and change the volume on its own.

The current router is **qwen2.5:1.5b** running in Ollama's JSON mode. On October 2, 2026, llama.cpp added the TypeSafe-compatible `/v1/systemone` endpoint (PR #29818). It serves *decision models* that score a fixed list of options in one pass instead of generating text. This bench checks whether any of them would be a better router.

## Contenders

| Model | Served by | Size on disk | Quantization | How it answers |
|---|---|---|---|---|
| qwen2.5:1.5b (current) | Ollama 0.35.0 | 986 MB | Q4_K_M | Generates JSON `{action, params, confidence}` |
| tev1:0.8b | Ollama 0.35.0 `/v1/systemone` | 811 MB | Q8_0 | Decision model, probabilities over options |
| Julia-1 | llama.cpp b11377 `llama-server` | 168 MB | Q8_0 | Decision model (laya type) |
| Laya | llama.cpp b11377 `llama-server` | 449 MB | Q8_0 | Decision model |
| Kev-4B | llama.cpp b11377 `llama-server` | 4.48 GB | Q8_0 | Decision model |
| lev | llama.cpp b11377 `llama-server` | 4.48 GB | Q8_0 | Decision model |

GGUF files are from the `ggml-org` Hugging Face repos. SHA-256 prefixes: Julia-1 `1ea6a7e87156eeed`, Laya `c06528c5746d3bb8`, Kev-4B `7c2ebed90560522c`, lev `c6b70833a9ec59c6`. Ollama model IDs: qwen2.5:1.5b `65ec06548149`, tev1:0.8b `d45e875d63fe`.

## Hardware

Mac mini, Apple M4, 16 GB unified memory, macOS 26.3, on AC power. No thermal or performance warnings were recorded during any run. The voice app itself was stopped during testing.

## Test sets

Both sets are hand-written synthetic phrases in four styles:

- **clean:** plain commands ("open spotify", "set a timer for 5 minutes")
- **messy:** fillers and speech-to-text errors ("um can you open up uh safari", "pawse", "goggle best headphones")
- **none:** things that aren't commands, such as small talk, general questions, TV-style background speech, "yeah", and gibberish
- **hard:** indirect wording ("it's way too loud", "i don't like this song", "wake me up in 15 minutes")

**Test A: 8 choices (main result, 369 phrases).** Seven actions (open_app, quit_app, switch_app, set_volume, media, timer, web_search) plus `none`. This is the option list free-mac-voice already uses for its decision-model path. Commands for any other action (window snapping, tabs, dark mode, and so on) count as `none`, because the embedding match handles those earlier. Of the 369 phrases, 152 are commands and 217 should come back `none`. **A model that always says `none` scores 58.8%**, so plain accuracy flatters cautious models. Watch the false-trigger and missed-command columns.

**Test B: 40 choices (side result, 264 phrases).** The full list of 39 actions plus `none`, which is the list the current qwen prompt carries. tev1:0.8b refuses more than 26 options, so it can't run this test.

Every model gets the same phrases, labels and option list, with short descriptions for each option. qwen gets an equivalent JSON-mode system prompt at temperature 0.

## Method and fairness controls

- **One model at a time.** Before each model, every Ollama model is unloaded (`keep_alive: 0`) and any llama.cpp server is stopped, followed by a 30-second cooldown.
- **Same server settings.** llama.cpp runs with 1 slot and a 2,048-token context (`-np 1 -c 2048`). The default of 4 slots × 8K context made Kev-4B reserve about 10 GB and pushed the Mac into heavy swap on an early run, so that run was thrown out. Ollama runs at its defaults, and qwen gets `num_ctx 1024`.
- **Cold vs warm, measured separately.** For each model the harness records the load time (llama.cpp: process start until `/health` is OK), then the **cold first request**, then 5 untimed warm-up requests, then warm timings.
- **Repeats and order.** Timing ran 3 full passes over a fixed 123-phrase subset (every 3rd phrase of Test A), with model order shuffled each pass (seed 42), so no model gains from going first or last. Accuracy comes from the full 369-phrase run.
- **Machine state logged.** Before each model the harness logs swap use, free memory, thermal state, power source and load average. Free memory was about 86% before every model in the timing passes, and nothing else heavy was running.
- **Latency** is the wall-clock time for one HTTP round trip on localhost, single request, no batching.

## Results: Test A (8 choices, 369 phrases)

| Model | Accuracy | False triggers ↓ (non-command → action) | Missed commands ↓ (command → none) | Warm median | Warm p95 | Cold first request | Load time |
|---|---|---|---|---|---|---|---|
| **Kev-4B** | **81.6%** | **9.2%** | 25.7% | 659 ms | 676 ms | 655 ms | 1.5–3.5 s |
| lev | 79.4% | 26.3% | **3.9%** | 1,505 ms | 1,644 ms | 1,416 ms | 1.4–2.7 s |
| tev1:0.8b | 70.2% | 35.9% | 11.2% | 177 ms | 179 ms | 1.0–2.6 s* | in cold* |
| qwen2.5:1.5b (current) | 67.8% | 43.3% | 4.6% | 336 ms | 393 ms | 1.6–1.8 s* | in cold* |
| Julia-1 | 64.5% | 2.8% | 79.6% | 13 ms | 13 ms | 28 ms | 0.6–0.8 s |
| Laya | 58.8% | 0.0% | 100% | 75 ms | 76 ms | 82 ms | 0.2–0.6 s |

\* Ollama loads a model on its first request, so for the Ollama models the cold number includes loading.

Timing numbers are the median across the 3 passes. The passes agreed within about 1%.

Accuracy by phrase style:

| Model | clean | messy | none | hard |
|---|---|---|---|---|
| Kev-4B | 86.0% | 76.2% | 92.9% | 66.7% |
| lev | 81.4% | 70.5% | 87.1% | 85.4% |
| tev1:0.8b | 65.1% | 66.4% | 90.0% | 64.6% |
| qwen2.5:1.5b | 60.5% | 65.6% | 87.1% | 64.6% |
| Julia-1 | 58.1% | 59.8% | 100% | 41.7% |
| Laya | 51.9% | 50.8% | 100% | 37.5% |

## Results: Test B (40 choices, 264 phrases)

| Model | Accuracy | False triggers ↓ | Missed commands ↓ | Warm median |
|---|---|---|---|---|
| Kev-4B | 83.0% | 23.5% | 10.1% | 2,700 ms |
| qwen2.5:1.5b (current) | 68.6% | 41.2% | 6.1% | 297 ms |
| Laya | 53.4% | 30.6% | 21.2% | 76 ms |
| Julia-1 | 15.9% | 82.4% | 16.8% | 19 ms |
| tev1:0.8b | can't run (max 26 options) | | | |
| lev | not run (it was already 1.5 s per phrase with 8 options) | | | |

Test B was run before the fairness controls above were added, so treat its latency numbers as rough. Decision models get slower as the option list grows: Kev-4B went from about 0.66 s with 8 options to 2.7 s with 40.

## Prompt sensitivity check

With the 8-choice list, Laya and Julia-1 answered `none` for nearly everything. Shortening the `none` description to "None of the above" barely changed that: Laya still missed 85% of commands and Julia-1 missed 45%. lev's accuracy dropped slightly with the short description (76.2%). Results above use the full description for every model.

## Takeaways

1. **The current router's weak spot is false triggers.** qwen2.5:1.5b catches almost every real command (only 4.6% missed), but it also turns 41–43% of non-commands into actions. In a living room with a TV on, that's the failure that matters.
2. **Kev-4B is the most accurate and the most reliable at saying "no".** With 8 choices it cut false triggers from 43% to 9% and raised accuracy from 68% to 82%. The costs: about 0.66 s per decision instead of 0.34 s, about 4.7 GB of memory instead of about 1.1 GB, and more missed commands (26%).
3. **lev rarely misses a command (3.9%)**, but it's slow (1.5 s) and still false-triggers on 26% of non-commands.
4. **tev1:0.8b is a cheap upgrade.** It's about 2× faster than qwen, a little more accurate, and has fewer false triggers (36%). Raising its confidence threshold to 0.7 brings false triggers down to about 16%.
5. **Julia-1 and Laya are very fast but too cautious** for this many choices. They'd work better as a yes/no "is this a command?" gate than as the router.
6. **Keep the option list short.** Decision models get slower and the small ones get much worse as options grow. 8 options suits them far better than 40.

## Recommendation

There's no free win. For a living-room setup where false triggers are the main complaint, the best candidate is **Kev-4B with the 8-option list**, accepting about 0.3 s of extra delay. Its misses can fall through to the existing qwen path. If speed matters more, **tev1:0.8b with a 0.7 confidence threshold** is the low-risk middle option. Nothing has been changed in free-mac-voice yet. Any switch would be tested against live, real-world audio first.

## Caveats

- The phrases are synthetic and written in English by the tester. Real speech-to-text output, accents and room noise will differ.
- qwen2.5:1.5b ran at Q4_K_M (Ollama's default) and the decision models ran at Q8_0. A Q4 build of Kev-4B would likely be faster, but it wasn't tested.
- Decision-model probabilities come with temperatures stored in each model file, and llama.cpp notes they aren't guaranteed to be calibrated for your data.
- Vendor speed claims for these models were measured on datacenter GPUs. Everything here ran on a base M4 Mac mini.
- Single machine and single-user latency. No batching or concurrency was tested.
