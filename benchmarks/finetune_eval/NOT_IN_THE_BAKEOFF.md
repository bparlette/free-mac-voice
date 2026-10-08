# Things that were considered, run and dropped, or never run

Honest record of every contender and dead end, with the reason. "Run" means it was measured in this
harness. "Not run" means it was ruled out on paper and **its claims are unverified for this task**.
Memory budget used throughout: the voice router may add at most about 1.5 GB on top of what is
already resident (a large Qwen model already takes about 8 GB of a 16 GB Apple M4).

## Run, then eliminated

| Contender | Result | Why it was dropped |
|---|---|---|
| **Laya zero-shot** (official `laya` 0.4.0, English checkpoint, CPU, 50 router actions + "none" as one `choice` question, plus a yes/no "is this a command?" question) | Best setting: 52% correct command with 63.5% of non-commands wrongly accepted. Tightening the confidence cutoff: 22% correct at 26% false accepts; 9% correct at 12.5%. | Far below the router (73.5% correct, 17% false accepts, same held-out half) **and** over the memory budget: about 2.7 GB peak process memory on CPU with one checkpoint (about 2.3 GB when `preload=True` loads all three checkpoints). Median latency about 370 ms per request on CPU, measured while a training job shared the machine, so it is probably pessimistic. |
| **First fine-tune (run "A", 300 iterations, lr 2e-4)** | 0% correct | Two causes. (1) Evaluation bug: `mlx_lm` trains prompt/completion data in chat format, and my decoder did not apply the chat template. After fixing that, (2) the model was underfit and answered "none" for obvious commands. Kept in the record because the bug would have wrongly condemned the whole approach. |
| **Fine-tune "A2"** (24 LoRA layers, lr 3e-4, 900 iterations) | Training diverged (validation loss NaN) | Learning rate too high for the quantized 0.5B model. Retrained as "A3" at lr 1e-4, 16 layers (this is the A3 in README.md). |
| **Fine-tune "B"** (same too-high settings, 800 iterations, trained on examples + half the test set) | 0% correct; answered only "none" or "tv_right" regardless of input | Collapsed to the label prior because of the same learning-rate problem (its training loss of about 0.65 was just the cost of guessing the prior). Retrained as "B2" at lr 1e-4 (the B2 in README.md), which reached 81% correct. A healthy run showed training loss of about 0.16 early on. |
| **EmbeddingGemma 2 270M-class (q4 ONNX)** as a drop-in embedding model for Tier 0.5a | Run; no better than the current model. With Tier 0 first it needed thresholds near 0.88-0.90 and gave 52-56% correct at 15-16.5% false accepts, versus 61.8% / 16.0% for the current model. About 0.5 GB and 16-23 ms per query. | Not an improvement on accuracy. Note: the 270M-class label is approximate: the downloaded ONNX text graph is Google's single q4 text model from the 740M multimodal repo, not the separately packaged Ollama "270m" tag, which could not be tested (next row). |
| **EmbeddingGemma 2 via Ollama** (`embeddinggemma-2:270m`, 378 MB) | Could not run | Ollama 0.35 refused to pull it ("requires a newer version"). The Homebrew `llama.cpp` (build 11146) failed with "unknown model architecture: gemma-embedding2" and `transformers` 5.18 does not know `embedding_gemma2`. Upgrading Homebrew Ollama to 0.40 was attempted, but the Ollama desktop app relaunches its own (older, 0.35.0) server on the same port, and quitting the app needs a manual click, so the update was abandoned and the ONNX route above was used instead. The Homebrew Ollama background service was left **stopped** at that point; the desktop app's server is what answers on the port. |

### What Laya was *not* given (so this is not a final verdict on Laya)

- No fine-tuning (the package ships a training CLI; the router-style task was only tried zero-shot).
- Choices were bare action names ("snap left"), with no descriptions or examples. A 512-token context
  limit makes 50 described options hard to fit.
- Only the English checkpoint was timed; the multilingual one and the "typed decisions" one were not scored.
- CPU only (kept off the GPU on purpose). No half-precision load, no Apple GPU run, no ONNX or Core ML build.
- One of its heads logged a "temperatures out of range, confidence uncalibrated" warning when loaded, so its
  confidence scores may be less trustworthy than advertised.

## Not run (ruled out before testing)

| Item | Why not run |
|---|---|
| **Unsloth Studio / Desktop install** (the tool in the post that started this) | The installer is a system-level install (own PyTorch environment, an `.app` bundle, a PATH change, optional autostart). It is also not evident from the installer that it can train on Apple silicon. Replaced by `mlx_lm` LoRA, Apple's own fine-tuning library already installed in the project environment, which tests the same idea ("fine-tune a small LM into a classifier"). The 74-78% accuracy figures in the Unsloth post/guide are the vendor's own, measured on general text-classification sets (BANKING77, CLINC150, typed-decisions), not on spoken commands. |
| **Unsloth `FastDecisionModel` / "Clef head" recipe** | Not run. The guide's code loads 4-bit models through Unsloth's own stack; I did not confirm it works on Apple silicon. |
| **`laya-mlx`** (third-party MLX conversion of Laya) | Single-author, unvetted package. Used the official `laya` package instead, installed into a throwaway folder (`pip install --target`, not the project environment). |
| **OpenAI Decisions API / other cloud decision APIs** | Would send transcribed speech off the device for an always-listening assistant; also adds network latency. Excluded on privacy grounds, not on accuracy. |
| **Qwen3.8-27B + DFlash2 speculative decoding** | Needs an NVIDIA GPU stack; no NVIDIA GPU here. Its speed-up only applies when the answer is largely copied from a long prompt, which voice commands are not. About 8.65-11 GB of weights also would not fit next to the existing model. |
| **Abliterated ("uncensored") Mirai S Qwen3.8-27B** | Same hardware reasons, plus a model with its refusals removed does not belong in an assistant that can control the computer. |
| **EmbeddingGemma 2 + TurboQuant vector compression** | The router compares speech against about 250 example phrases (a few hundred KB), so there is nothing to compress. EmbeddingGemma 2 itself (a newer embedding model) is a candidate for a later, separate test if a small build is available for Ollama; its availability and size are unconfirmed. |

## A correction to an earlier number

`benchmarks/intent_eval/README.md` reports "77.5% command recall at threshold 0.82". That harness counts a
command as recalled if **anything** matched, not if the **correct** action matched. Scored on the right action,
the router gets about 73.5% on the held-out half used here (and its false accepts include Tier 0 regex
mistakes, so they read 17% instead of the 6.5% the older harness reports for Tier 0.5a alone). Both harnesses
are valid for what they measure; do not compare numbers across them.

## Caveats that apply to every result in this folder

- The test set is synthetic and mostly written by AI; real speech-to-text errors are only roughly imitated.
- Fine-tuning data is small (177 production examples left after removing 75 that also appear in the test set,
  plus 112 hand-written non-commands). Style overlap between my negatives and the test negatives is possible.
- One random 50/50 split (seed 0). With about 200 commands per side, differences under about 5 points are noise.
