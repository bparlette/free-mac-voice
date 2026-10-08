# Tier 1 / vision model: Qwen3-VL 8B (current) vs 4B

Question: could the assistant's main local model (`qwen3-vl:8b`, 5.2 GB resident) be replaced by a 4B model to save memory
and time? Tested on the three jobs the assistant gives it, **using the assistant's own functions** (`ollama_route`,
`vision_ask`, and the exact request `ollama_answer` sends) with only `OLLAMA_MODEL` changed. Apple M4 16 GB, Ollama 0.40.0,
the voice service running during the tests (so absolute speeds are a bit pessimistic).

## Results

| | **8B (current, "thinking" build)** | 4B, "thinking" build (`qwen3-vl:4b`) | **4B instruct** (`qwen3-vl:4b-instruct`) |
|---|---|---|---|
| Resident memory | 5.2 GB | 3.0 GB | **3.0 GB** |
| Generation speed | 16.3 tok/s | 29.6 tok/s | 27.4 tok/s |
| Tier 1 routing, **current code** (60 held-out commands / 60 non-commands) | 52 correct, 5 wrong, 3 none; 9/60 false accepts; 1.4 s | 9 correct (**48 none**); 1/60 FA; 0.17 s | 9 correct (**50 none**); 0/60 FA; 0.17 s |
| Tier 1 routing, **no pre-filled answer start** | not applicable (thinking models need it) | 0 correct (nothing usable) | **51 correct, 6 wrong, 3 none; 15/60 false accepts; 0.84 s** |
| Screen questions (12, assistant's `vision_ask`) | 12/12, median 9.3 s | 11/12, 5.6 s | **12/12, 4.1 s** |
| General questions (10, assistant's answer prompt) | 8/10, **1 empty**, median **12.1 s** (194 tokens) | 7/10, **3 empty**, 8.0 s | **9/10, 0 empty, 0.3 s** (6 tokens) |

## Added later: `qwen3-vl:8b-instruct` (same size as the current model, non-thinking)

| | 8B thinking (current) | **8B instruct** | 4B instruct |
|---|---|---|---|
| Resident memory | 5.2 GB | 5.2 GB | 3.0 GB |
| Generation speed | 16.3 tok/s | 15.8 tok/s | 27.4 tok/s |
| Tier 1 routing, current code (of 60 commands) | 52 right, 5 wrong, 3 none | 47 right, 4 wrong, 9 none | 9 right (50 none) |
| Routing false accepts (of 60 non-commands) | 9 | **7** | 0 (but see no-pre-fill row above: 15) |
| Screen questions (of 12) | 12/12, 9.3 s | **12/12, 4.9 s** | 12/12, 4.1 s |
| General questions (of 10) | 8/10, 1 empty, **12.1 s** | **9/10, 0 empty, 0.5 s** | 9/10, 0 empty, 0.3 s |

- Same memory and speed as today, but general answers are about **24x faster** (0.5 s vs 12 s), screen answers about 2x faster, and no silent empty replies.
- Routing is a bit lower under the *current* code (47 vs 52 of 60), but that code pre-fills the answer start for any `qwen3` model, which is
  meant for thinking models. The same pre-fill collapsed the 4B instruct from 51 to 9 correct, so the 8B instruct's figure is probably also
  understated. **The no-pre-fill run for 8B instruct was not done** (the model was removed to free disk); it is the missing test.
- Several of its "wrong action" answers are arguably right and only look wrong against my labels: "close that tab mate" -> `close_tab`
  (labelled `close_window`), "go to sleep" -> `sleep` (labelled `lock_screen`). Treat the routing counts as approximate.
- Its one general-question miss was spelling "necessary" backwards.
- Raw numbers: `results/qwen3-vl_8b-instruct.json`.

## What it says

1. **The plain 4B does not work as a drop-in.** With the assistant's current Tier 1 code, 4B returns an empty `{}` for most commands
   (9/60 right). The code pre-fills the start of the model's answer (`{"action": "`) for any model whose name contains `qwen3`;
   that trick is meant for "thinking" models and makes the 4B emit nothing. 
2. **The Ollama tags `qwen3-vl:4b` and `qwen3-vl:8b` are thinking models**, which reason silently before answering even with
   `think: false`. That costs tokens: general questions on the current 8B take **about 12 seconds** (194 tokens at 16 tok/s), and
   in 1 of 10 the reasoning used the whole 256-token budget so **the assistant would have said nothing** (`ollama_answer` only
   reads the final answer). The 4B thinking build is worse (3 of 10 empty).
3. **The non-thinking `4b-instruct` build fixes this.** General answers: 0.3 s and 9/10 correct. Screen questions: 12/12 at
   4.1 s (vs 9.3 s). Routing, once the pre-fill is skipped for instruct models: 51/60 correct versus 52/60 for the 8B,
   in 0.84 s versus 1.4 s. It uses 2.2 GB less memory.
4. **The cost:** routing false accepts are higher (15/60 = 25% vs 9/60 = 15% on adversarial non-commands, where "false accept"
   means Tier 1 proposed an action for something that was not a command). In the live assistant the wake word, the regex
   tier, the embedding tier and the command gate sit in front of Tier 1, so this is an upper bound, but it was not measured
   end to end.

## Not tested / caveats

- No code change was made. Using `4b-instruct` needs `ollama_route` to skip the pre-fill for non-thinking models (e.g. only
  pre-fill when the model tag does not contain `instruct`), plus `OLLAMA_MODEL=qwen3-vl:4b-instruct`.
- Small samples: 60+60 routing phrases from the synthetic held-out set, 12 screen questions (on screenshots of the Off The Rip
  site, keyword-matched), 10 general questions. Differences of a few points are noise.
- The 8B comparison row is the thinking build because that is what is installed today.
- Memory is Ollama's reported size of the loaded model (`/api/ps`).

## Reproduce

```bash
ollama pull qwen3-vl:4b-instruct
./.venv/bin/python benchmarks/model_size_eval/bench_qwen.py qwen3-vl:8b           # unload other models between runs: ollama stop <model>
./.venv/bin/python benchmarks/model_size_eval/bench_qwen.py qwen3-vl:4b-instruct
OLLAMA_MODEL=qwen3-vl:4b-instruct ./.venv/bin/python benchmarks/model_size_eval/route_prefill_ab.py qwen3-vl:4b-instruct
```
`bench_qwen.py` expects page screenshots in `/tmp/shots` (see the question list in the script; swap in your own images and answers).
Raw results: `results/*.json`.
