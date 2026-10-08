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

## Added later: `tev1:0.8b` (a decision model) as the action router

Decision models answer typed questions with calibrated probabilities through `/v1/systemone`. Findings (`bench_decision_router.py`):

- **The API accepts only 2-26 options per `choice` question** (HTTP 400 otherwise), so it cannot pick among all ~46 actions in one go.
  A shortlist design was tested instead: the embedding tier proposes the top 8 candidate actions, the decision model picks one or "none".
  The right action was in the top-8 shortlist for 58 of 60 commands.
- **It did worse than the embeddings alone** (60 held-out commands / 60 non-commands):

  | | correct | wrong | non-commands acted on |
  |---|---|---|---|
  | Embeddings alone, cosine >= 0.82 | 29 | 4 | 4 |
  | Embeddings + tev1:0.8b, probability >= 0.70 | 23 | 10 | 17 |
  | Embeddings + tev1:0.8b, probability >= 0.50 | 29 | 13 | 25 |

- Median call time 317 ms (with other load running). Verdict: **`tev1:0.8b` is a fine yes/no command gate but a poor action router**,
  consistent with the end-to-end result in `../pipeline_eval/README.md` (disabling the tier that uses it improves results).

## Added later: smaller models (Qwen3.5 0.8B / 2B, qwen3-vl 2B instruct)

Same tests, run one model at a time (`results/small_models_run.txt` has the raw log; each model was removed afterwards).
Routing here is the better of "with / without the pre-filled answer start", run with `route_prefill_ab.py` (it retries without
`format=json`, see the first finding).

| | resident | speed | Tier 1 routing (60 cmds / 60 non-cmds) | screen questions (12) | general (10) |
|---|---|---|---|---|---|
| 8B thinking (current) | 5.2 GB | 16 tok/s | 52 right, 9 false accepts, 1.4 s | 12/12, 9.3 s | 8/10, 12 s, 1 silent |
| 8B instruct | 5.2 GB | 16 tok/s | 47 right (current code; no-pre-fill not run), 7 FA | 12/12, 4.9 s | 9/10, 0.5 s |
| 4B instruct | 3.0 GB | 27 tok/s | 51 right, 15 FA, 0.8 s (no pre-fill) | 12/12, 4.1 s | 9/10, 0.3 s |
| Qwen3.5 2B | 3.0 GB | 33 tok/s | 45 right, 15 FA, 0.9 s | 12/12, 2.8 s | 8/10, 0.5 s |
| **qwen3-vl 2B instruct** | **1.6 GB** | 54 tok/s | 13 right, 8 FA (weak) | **12/12, 1.5 s** | **9/10, 0.1 s** |
| Qwen3.5 0.8B | 1.3 GB | 69 tok/s | 35 right, 20 FA (weak) | 10/12, 1.4 s | 7/10, 0.1 s |

Findings:
1. **Ollama 0.40 refuses `format: "json"` for the Qwen3.5 models** (`HTTP 501 structured output is unavailable`). The assistant's Tier 1 routing
   requests JSON that way, so with the current code the Qwen3.5 models return nothing (0/60). They would need a no-`format` path.
2. **Routing accuracy falls with size**: 0.8B 35, 2B 45, 4B 51, 8B about 47-52 of 60; false accepts stay high (11-20 of 60) below 8B.
3. **Screen questions and short answers hold up much better than routing.** `qwen3-vl:2b-instruct` answered all 12 screen questions in 1.5 s with
   1.6 GB resident, and 9/10 general questions in 0.1 s, but routed only 13/60. The test screens had large, clear text; harder screens are untested.
4. This supports a **split design**: embeddings/regex for routing, with a small model (the 2B instruct) for screen and short-answer jobs, and a
   bigger model only when needed. Cold-start for the 8B / 4B instruct measured 6.6 s / 9.6 s (warm 0.2-0.3 s).

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
