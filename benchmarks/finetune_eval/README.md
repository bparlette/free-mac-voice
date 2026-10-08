# Bake-off: can a small fine-tuned model replace Tier 0.5a?

Question: the router's Tier 0.5a matches what you said to about 250 example phrases using an embedding
model (`embeddinggemma` via Ollama). A social-media post claimed small "decision models" fine-tuned
from tiny LLMs jump from near-chance to about 75-80% on intent tasks. Does a small local model beat the
current router on **our** voice-command task, inside our memory budget?

Memory budget: at most about 1.5 GB extra on a 16 GB Apple M4 that already keeps a large Qwen model (about 8 GB) loaded.

**Short answer:** promising, not ready to ship. A small fine-tuned model beat the router on both accuracy
and false triggers, fits the budget, and is fast. But the gain is from one synthetic test set, and the
biggest lever is more real training data, not the model. See "What to do next".

Everything dropped, never run, or that failed is in [NOT_IN_THE_BAKEOFF.md](NOT_IN_THE_BAKEOFF.md).
Raw logs for every number below are in [results/](results/).

## How the comparison works

- **Test set:** `benchmarks/intent_eval/testset.jsonl` (400 spoken-style commands across 17 intents with
  casual phrasing and speech-to-text mishearings, plus 400 adversarial non-commands such as TV, podcasts and
  narration). It is split once, 50/50 and stratified (seed 0). **All numbers are on the held-out half:
  204 commands + 200 non-commands.** With about 200 per side, differences under roughly 4-5 points are noise.
- **Metric:** a command counts as **correct** only if the model picked the right action (test intent names are
  mapped to the router's action names in `common.py`). **False accept** = a non-command that was accepted as
  some action. Older `intent_eval` numbers counted *any* match as recall and are not comparable.
- **Leak-free:** 75 of the router's 252 example phrases also appear verbatim in the test set. The "as deployed"
  baseline therefore flatters the router. All fair comparisons drop those 75 (177 examples remain).
- **Pipeline stages:** "+ Tier0" means the existing Tier 0 regex runs first and the contender only handles what it
  misses, as in production. "alone" means no regex.

## Results (held-out half, 204 commands / 200 non-commands)

Pick the row at similar false-accept rates when comparing.

| Contender | Correct command | False accepts | Memory | Speed | Notes |
|---|---|---|---|---|---|
| Router **as deployed** (Tier 0 + 0.5a, thr 0.82) | 73.5% | 17.0% | already resident | n/a | Inflated by test-set leakage |
| Router **leak-free** (thr 0.82) | **61.8%** | **16.0%** | already resident (embedding model 621 MB in Ollama) | n/a | The honest baseline |
| Router leak-free, thr 0.86 | 58.3% | 13.5% | same | | |
| **Fine-tuned Qwen2.5-0.5B, "A3"** (production examples + 112 hand-written non-commands; alone, conf >= 0.8) | **65.7%** | **9.5%** | about 0.4 GB growth | 45 ms median | Same data the router has |
| A3, conf >= 0.7 | 67.2% | 9.5% | | | |
| **Fine-tuned Qwen2.5-0.5B, "B2"** (A3 data + the *other* half of the test set; alone, conf >= 0.7) | **81.4%** | **5.0%** | about 0.4 GB growth | 45 ms median | More in-distribution data; optimistic, see below |
| B2, conf >= 0.8 | 78.9% | 4.5% | | | |
| A3 with Tier 0 regex first | 65.7% | 20.0% | | | Regex tier adds false accepts |
| B2 with Tier 0 regex first | 74.5% | 16.0% | | | Same |
| EmbeddingGemma 2 (q4 ONNX, task prefix) + Tier 0, thr 0.90 | 52.0% | 16.5% | about 0.5 GB growth | 23 ms median | Not better than the current embedding model |
| EmbeddingGemma 2 (q4 ONNX, no prefix) + Tier 0, thr 0.88 | 55.9% | 15.0% | about 0.5 GB growth | 16 ms median | Same |
| **Laya** zero-shot (English checkpoint, CPU) | 52.0% | 63.5% | **about 2.7 GB peak** | 325 ms median | Eliminated: accuracy and memory |

Memory is peak process memory growth measured in a separate process per contender (`ru_maxrss`); MLX also
holds GPU buffers that this number may undercount. Speeds are per request on this M4 with nothing else running.

### What the table says

1. **The router is weaker than its old numbers suggested.** Counting only correct actions and removing leaked
   examples, it is about 62% correct at 16% false accepts, not the 77.5% "recall" in `intent_eval/README.md`.
2. **A small fine-tuned model beats it on the same data.** A3 gets 65.7% correct with 9.5% false accepts, versus
   61.8% / 16.0%. The accuracy gain (4 points) is within noise on 204 commands; the false-accept drop
   (32 -> 19 of 200) is the more convincing part.
3. **More data is the real lever.** B2 saw about 2.4x as many examples (685 vs 289 rows) (including phrasing from the same generator as
   the test set) and reached 81% correct at 5% false accepts. Treat that as an upper bound: its training and test
   data come from the same source. The takeaway is that logging real utterances and labelling them would help more
   than any model swap.
4. **The Tier 0 regex is the biggest source of false accepts on this adversarial set.** Putting it first raises the
   fine-tuned models' false accepts from 5-9.5% to 15-20%, e.g. a narration line that happens to contain "open". In
   production a command gate and wake word sit in front of these tiers and were *not* part of this bake-off, so
   re-measure with the gate before drawing a conclusion about the regex.
5. **It fits the budget.** About 0.4 GB and 45 ms per decision, comparable to the 621 MB embedding model it
   would replace rather than add to.
6. **Laya and EmbeddingGemma 2 did not earn a place** (see rows and NOT_IN_THE_BAKEOFF.md).
7. **Separately from the model question, the ONNX build of the current embedding model is a cheap win** (same accuracy, a fraction of the memory); see the next section.

## Engine and ONNX benchmarks (embedding step only)

Same held-out half and leak-free examples as above, Tier 0 first. The metric here is **correct command at <= 16% false
accepts** (the router's own level), found by scanning the confidence threshold, because different models have different
cosine scales. Raw logs: `results/ollama_old_vs_new.txt`, `results/onnx_variants.txt`, `results/onnx_memory_isolated.txt`.
Harness: `bench_engines.py`.

### Old vs new Ollama (0.35.0 vs 0.40.0, run side by side on different ports)

| Model | Engine | Correct @ FA<=16% | Single query | Batch | Loaded size |
|---|---|---|---|---|---|
| embeddinggemma (current) | old 0.35.0 | 61.8% | 16 ms | 69 texts/s | 648 MB |
| embeddinggemma (current) | **new 0.40.0** | 61.8% | **10 ms** | **107 texts/s** | 648 MB |
| embeddinggemma-2:270m | new 0.40.0 (old cannot load it) | 54.9% (52.0% with task prefix) | 15 ms | 91 texts/s | **330 MB** |
| qwen2.5:1.5b generation | old / new | 61 / 61 tokens/s | | | |

- The newer Ollama gives **identical accuracy** for the current model and embeds about 1.5x faster; text generation
  speed is unchanged. Upgrading the engine is not an accuracy risk.
- EmbeddingGemma 2 (270m) uses half the memory but is about 7 points less accurate. Not worth it for this task.
- `llama.cpp` from Homebrew (build 11146) has no newer release available and cannot load EmbeddingGemma 2, so it is
  represented by the Ollama versions, which bundle their own newer engine.

### ONNX (onnxruntime 1.30, in-process, no Ollama)

| Model (weights) | Provider | Correct @ FA<=16% (no prefix / task prefix) | Single query | Memory growth (fresh process) |
|---|---|---|---|---|
| **embeddinggemma v1 q4 (188 MB)** | CPU | **62.3% / 63.2%** | **6 ms** | **+0.10 GB** |
| embeddinggemma v1 q4f16 (168 MB) | CPU | 61.8% / 63.7% | 8 ms | +0.15 GB |
| embeddinggemma v1 int8 "quantized" (295 MB) | CPU | 61.8% / 61.8% | 43 ms | +1.15 GB |
| embeddinggemma 2 q4 (166 MB) | CPU | 57.8% / 51.0% | 14 ms | +0.25 GB |
| embeddinggemma 2 q4f16 (150 MB) | CPU | 57.8% / 50.5% | 15 ms | +0.25 GB |
| embeddinggemma 2 fp16 (517 MB) | CPU | 54.9% / 54.4% | 12 ms | +0.40 GB |
| v1 q4f16 | CoreML | 61.8% / 63.7% | 13 ms (slower than CPU) | |
| v1 q4 | CoreML | **0.0%: output unusable** | 131 ms | |
| v1 int8 | CoreML | **22%: output unusable** | 270 ms | |
| embeddinggemma 2 (all four) | CoreML | **fails to run** (zero-size input unsupported) | | |

- **The standout is v1 q4 on the CPU:** the same accuracy as Ollama's copy of the same model (62-64% vs 61.8%), about 6 ms
  per query versus 10-16 ms, and roughly 0.1 GB of process memory growth versus the 648 MB Ollama reports as
  loaded. (Different measures, but the order of magnitude is the point.) `onnxruntime` is already a project dependency, so
  Tier 0.5a could embed in-process and stop depending on Ollama for this step, freeing about 0.5 GB next to the large Qwen.
- Google's task prefix (`task: classification | query: `) helps v1 slightly and hurts v2. Keep v1 with the prefix if tried.
- **Do not use the int8 variants or the CoreML provider**: the int8 graphs are slow and large, and CoreML either fails or
  produces unusable embeddings for most variants.
- Not done: wiring this into `free_voice.py`. The speed and memory figures were measured in a benchmark process, not under
  the live daemon.

## Process (to reproduce)

All commands run from the repository root with the project venv. Artifacts go to `/tmp/ft` (not committed).

1. **Build training data** (excludes every test phrase from the production examples, adds `negatives.txt`):
   ```bash
   ./.venv/bin/python benchmarks/finetune_eval/make_data.py /tmp/ft
   ```
   Writes `/tmp/ft/A` (examples + negatives: 289 rows) and `/tmp/ft/B` (A plus a stratified half of the test set: 685 rows).
   Labels are the router's own action names plus `none`; the prompt is `Voice command: <text>\nAction:`.
2. **Fine-tune with Apple's MLX** (`mlx_lm` 0.32, already in the project venv; base model
   `mlx-community/Qwen2.5-0.5B-Instruct-4bit`). The settings that worked (A3 and B2):
   ```bash
   ./.venv/bin/python -m mlx_lm lora --model mlx-community/Qwen2.5-0.5B-Instruct-4bit --train \
     --data /tmp/ft/A --adapter-path /tmp/ft/adA3 --iters 900 --batch-size 8 \
     --learning-rate 1e-4 --num-layers 16 --mask-prompt --steps-per-eval 300 --val-batches 4 --save-every 900
   # B2: --data /tmp/ft/B --adapter-path /tmp/ft/adB2 --iters 800 (rest the same)
   ```
   About 9 minutes each. **Learning rate matters:** 2e-4 underfit, and 3e-4 with 24 layers diverged or collapsed to
   a constant answer (details in NOT_IN_THE_BAKEOFF.md). `mlx_lm` trains prompt/completion data in chat format, so
   inference must apply the chat template (the eval script does).
3. **Score** (greedy decode; confidence = probability of the generated label):
   ```bash
   ./.venv/bin/python benchmarks/finetune_eval/eval.py --baseline --adapter /tmp/ft/adA3 --adapter /tmp/ft/adB2   # needs Ollama + embeddinggemma
   ./.venv/bin/python benchmarks/finetune_eval/eval.py --laya      # zero-shot Laya; install: pip install --no-deps --target /tmp/laya_pkgs laya
   ./.venv/bin/python benchmarks/finetune_eval/eval.py --baseline --eg2   # EmbeddingGemma 2 q4 ONNX copied into /tmp/eg2 (see below)
   ```
   Every contender is run in its own process so memory numbers do not mix.
4. **ONNX and engine benchmarks:** download the `onnx-community/embeddinggemma-300m-ONNX` and `.../embeddinggemma-2-ONNX` variants with `huggingface_hub` and copy them (dereferencing cache symlinks) to `/tmp/onnx_models/embeddinggemma1/` and `.../embeddinggemma2/` (each with `onnx/<variant>.onnx` + `.onnx_data` and `tokenizer.json`), then `bench_engines.py onnx`. For `bench_engines.py ollama`, run two Ollama versions on different ports.
5. **EmbeddingGemma 2 files for `eval.py --eg2`:** Ollama 0.35, the Homebrew `llama.cpp` build and `transformers` 5.18 on this machine all
   failed to load the model (unknown architecture), so the `onnx-community/embeddinggemma-2-ONNX` q4 text graph was run
   with `onnxruntime`. Copy `onnx/model_q4.onnx`, `onnx/model_q4.onnx_data`, `tokenizer.json`, `tokenizer_config.json`
   and `config.json` (dereference the Hugging Face cache symlinks) into `/tmp/eg2`.

## Caveats

- Synthetic, mostly AI-written test set; real speech-to-text errors are only imitated.
- One random split, about 200 examples per class. Small differences are noise.
- The fine-tuned model's training loss reached 0.000, so it memorised its small training set; held-out numbers are
  what count.
- The command gate model and the wake-word step in front of Tier 0 were not part of this test.
- GPU contention was **not** measured: the fine-tuned model runs on the GPU through MLX while a large Qwen may be
  loaded. Measure that before deploying.

## What to do next (in order of value)

1. **Log real utterances and their outcomes** (opt-in, local) and label a few hundred. B2 shows data is the lever.
2. Re-run with the production command gate in front, and examine which Tier 0 regex rules produce the false accepts.
3. Try a fine-tuned model as the *primary* classifier with Tier 0 kept only for exact, safe patterns.
4. Test under real load (Qwen resident, daemon running) for memory pressure and latency.
5. Try the ONNX q4 embedding backend for Tier 0.5a (benchmarked above): same accuracy, much less memory.
6. Only then consider wiring a fine-tuned classifier behind a feature flag; nothing in `free_voice.py` was changed by this experiment.
