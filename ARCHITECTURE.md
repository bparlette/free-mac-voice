# ARCHITECTURE — how free-mac-voice actually works

This is the detailed implementation companion to the [README](README.md).
Every latency number below is either measured (cited) or labeled an estimate.
Nothing here costs money.

## 1. The pipeline

```
mic (16 kHz PCM)
  ─► faster-whisper tiny.en, Metal, int8          ~100–300 ms   (local)
  ─► Tier 0: regex router + completion gating    <1 ms         (local)
  ─► Tier 1: Ollama qwen3-vl:8b, JSON mode      ~1–2 s warm   (local, Tier 0 miss only)
  ─► Tier 2: Gemini API free tier, search-grounded 2–5+ s       (Tier 1 miss/unsure only)
  ─► execution: AppleScript / shell / xa11y (+ vision fallback) ~50–100 ms (local)
  ─► macOS `say` confirmation                                  (local)
```

Design principle: **a cascade, not a committee.** Each tier only wakes when
every faster tier failed. The common path (a standard command) never touches
anything slower than a millisecond.

### Why one 8B vision-language model instead of a tiny router?

The local model is `qwen3-vl:8b` — one model for routing, Q&A, *and* screen
understanding, instead of a small text-only router plus a separate vision
model. Two reasons:

1. **One resident model, no swap penalty.** Two models fight over unified
   memory; one 8B (~5 GB) stays warm via `keep_alive: 60m` and serves every
   local need. 8 GB minis use `qwen3-vl:4b` via `OLLAMA_MODEL`.
2. **`think: false`.** Qwen3-family models reason by default — a reasoning
   trace before every answer is death for voice latency. Non-thinking mode
   returns the answer directly. Routing JSON is short (64 tokens), so even at
   ~30–60 tok/s on Apple Silicon it lands in ~1–2 s — acceptable for a Tier 1
   fallback that only fires on Tier 0 misses, never per speech chunk.

Measure on your own machine with:

```bash
time curl -s http://localhost:11434/api/chat -d '{
  "model": "qwen3-vl:8b", "format": "json", "stream": false, "think": false,
  "options": {"temperature": 0, "num_predict": 64},
  "messages": [{"role": "user", "content": "open my notes app please"}]}' | head -c 200
```

## 2. Tier 0 — regex + completion gating

`route(text, partial=False)` in `free_voice.py`. ~58 patterns, ordered
specific-before-general, each flagged `partial_ok=True/False`.

**Partial-safe** (may fire mid-sentence): closed commands whose payload can't
grow into something else — `volume up`, `mute`, `play`, `lock`,
`open <exact app>`, `open <known> settings`, `press enter`, `copy`, …

**Final-only** (wait for end-of-speech): anything with a free-text payload —
`type …`, `search …`, `calculate …`, `click the … button`, `set volume to 30`
(the number can grow: "3" → "30"), destructive actions (they need confirmation).

**The completion gate** (`_partial_complete`) is what makes mid-sentence
firing safe instead of chaotic. A partial must:

1. match a partial-safe pattern,
2. be **fully consumed** by the match (`m.end() == len(text)`), and
3. pass the payload check:
   - `open`/`quit <app>` → the app phrase must resolve **exactly** (alias or
     full installed name — no substring matching). This is the rule that stops
     "open no" from firing as "open Notes".
   - `open <topic> settings` → topic must be a known Settings pane.
   - closed enums → the full match is sufficient.

**Dedup** (`PartialSession`): each distinct command fires at most once per
utterance. Feed it growing partials; it fires the moment a command becomes
complete. Demo it without a mic:

```bash
python3 free_voice.py --partial "open notes" --dry-run
```

### 2b. Chained Compound Commands & Macros

- **Chaining without LLM overhead**: When an utterance contains conjunctions (`"and"`, `"and then"`, `", then"`), Tier 0 verifies whether all sub-clauses form valid actions. If so, they execute sequentially with a 300 ms inter-command delay (`open notes and snap left`, `set volume to 30 and play`). If any clause fails or the phrase is a natural sentence, Tier 1 handles it.
- **Browser & Contextual Navigation**: Hotkey-backed actions for fast active-window navigation without heavy accessibility tree walks: `new tab`, `close tab`, `reopen tab`, `refresh` / `reload`, `go back`, `go forward`, `scroll down` / `page down`, `scroll up` / `page up`, `find on page`, and `clear terminal`.
- **Spaces & Multi-Monitor Display Tiling**: Instant virtual desktop switching (`next space`, `prev space`) and multi-monitor window tossing (`move to next display` / `move to other screen`) via NSScreen frame calculations and AppleScript window repositioning.
- **Phonetic & Soundex Resolution**: Standard American Soundex indexing maps spoken misspellings from Whisper to installed app bundles (e.g. `es de` / `s d` → `ES-DE`, `sephari` → `Safari`).
- **Tactile Earcons**: Push-to-talk plays native `Tink.aiff` on Right-Option press and `Pop.aiff` on release, giving zero-latency eyes-free auditory feedback.
- **Voice Macros**: Quick actions for `read clipboard` (`pbpaste`), `type today's date`, `type the time`, and `type my email` (`VOICE_USER_EMAIL` in `.env`).

## 3. Tier 1 — local vision-language model (JSON mode)

Fires **only** on a Tier 0 miss. `ollama_route()` POSTs to
`http://localhost:11434/api/chat` with:

- `model`: `qwen3-vl:8b` (override with `OLLAMA_MODEL`; `qwen3-vl:4b` on 8 GB minis)
- `format: "json"` — Ollama's structured output
- `think: false` — no reasoning trace; voice needs the answer now
- `keep_alive: "60m"` — the model stays resident in unified memory, dodging
  the cold-start penalty on first use
- `options: {temperature: 0, num_predict: 64}` — deterministic, short output

The system prompt constrains the model to a fixed action enum
(`open_app`, `quit_app`, `type_text`, `web_search`, `set_volume`, `media`,
`timer`, `click_button`, …) plus `params` plus a `confidence` 0–1.

**Two honest caveats, built into the code:**

1. **JSON mode guarantees shape, not sense.** The output will be valid JSON
   matching the schema — but the model can still confidently pick the *wrong*
   action or wrong app. `dispatch_tier1()` therefore validates every enum and
   param (unknown media op → safe default, bad timer unit → minutes) instead
   of trusting the model.
2. **The confidence is self-reported, not calibrated.** A purpose-built
   decision model (e.g. Jev) returns calibrated per-choice probabilities you
   can threshold reliably; here the 0–1 is the model grading its own homework.
   `TIER1_MIN_CONFIDENCE` (default 0.5) is a rough heuristic. Below it, the
   request falls through to Tier 2 (or a polite "I didn't understand").

Disable with `OLLAMA_TIER1=0`. If Ollama isn't reachable, Tier 1 logs once and
gets out of the way — Tier 0 and Tier 2 are unaffected.

## 4. Tier 2 — Gemini free tier (optional)

Fires only when Tier 1 misses or abstains. A short, spoken-style answer via
`generativelanguage.googleapis.com` using the free API tier — **not** the $20
consumer subscription; those are separate products. Key lives in
`~/.free-voice/.env` (`GEMINI_API_KEY`), never in the repo. No key → Tier 2
is skipped silently.

**Search grounding** (`tools: [{"google_search": {}}]`) is enabled so fresh
questions ("who won last night") get live answers — still on the free tier,
still $0. The model only searches when it judges the question needs it.

**Quota fallback:** on HTTP 429 (free-tier limit hit), `gemini_answer()`
falls back to `ollama_answer()` — the local model answers instead, and says
so out loud ("Google's free limit is hit, answering from the on-device
model"). The local answer is lower quality and knowledge-cutoff-bound, but
the assistant never goes silent.

## 4b. Screen description & vision tiers

Screen queries and UI control locate follow a fast-path additive cascade:

1. **Quartz Window Summary (`quartz_window_summary()`) — Instant Orientation**:
   "What's on my screen" / "describe my screen" routes through Quartz first using `CGWindowListCopyWindowInfo`. It filters for layer-0 active windows $\ge 100\text{px}$ in front-to-back z-order and speaks a concise summary naming the frontmost app + window title and what's behind it (e.g. *"In front is Notes (Grocery List). Behind it, you have Safari and 2 other apps."*).
   - **~1.3 ms latency**: ~8,900× faster than VLM inference.
   - **Zero permissions barrier**: Does not require Screen Recording permissions, working instantly even before that grant.
   - **No screenshot overhead**: Completely skips `screencapture` and Ollama.
   - Orientation queries use Quartz; visual-content questions ("what's in this window", "what color is the button", "read my screen") seamlessly fall through to the VLM.

2. **Apple Vision OCR Fast-Path (`ocr_locate()`) — High-Speed Text Clicking**:
   When the accessibility tree (`xa11y`) has no node matching a clicked label (canvas UI, Flutter/web apps, or unmapped buttons), the click chain queries Apple's native `VNRecognizeTextRequest` before falling back to the VLM.
   - **Fast vs. Accurate Benchmark (M4 Mac mini)**:
     - `VNRequestTextRecognitionLevelFast`: Mean **61.4 ms**, $p50$ **60.1 ms**, min **59.3 ms** (80 bounding boxes).
     - `VNRequestTextRecognitionLevelAccurate`: Mean **224.3 ms**, $p50$ **224.7 ms**, min **217.7 ms** (83 bounding boxes).
     - UI text matching was 100% identical across all standard menu, button, and navigation targets. `Fast` was adopted as the active default, cutting OCR latency by ~3.6×.
   - **Screenshot Cache Reuse**:
     - `ocr_locate()` reuses the 8-second cached screenshot when fresh. With a warm cache, OCR text locate completes in **~61 ms** total (saving the ~189ms `screencapture` overhead).
   - **Tier 0 Direct Click Routing**:
     - Phrasings like *"click on the save button"*, *"press save"*, *"tap continue"*, *"hit allow"* route directly in Tier 0 (0.2 ms).
     - `_extract_target_name()` automatically strips conversational noise, prepositions, and modal prefixes (`on the `, `pop up dialog `, `button`, `link`), achieving **~0.18s–0.28s end-to-end click execution**.
   - **System Modal / Dialog Discovery (`_get_target_apps()`)**:
     - macOS permission alerts (Microphone, Accessibility, Screen Recording) are owned by system daemons (`SecurityAgent`, `CoreServicesUIAgent`, `Notification Center`), not `frontmost_app()`. `_get_target_apps()` queries `NSWorkspace.runningApplications()` in ~2ms to dynamically include active system alert hosts in the accessibility and locate search, resolving "Allow" buttons in 10ms.
   - **Ambiguity protection**: When multiple identical or near-identical controls are found across disparate coordinates, it safely clarifies ("I found multiple items matching X. Which one did you want?") rather than misclicking.
   - **Iconographic fallback**: Purely iconographic targets ("the red circle") score 0 in OCR and gracefully fall through to `vision_locate()`.

3. **Local VLM Fallback (`qwen3-vl:8b`)**:
   - **Describe Screen (800px)**: Empirical benchmarks measured 11.58s $p50$ at 640px vs 11.36s at 800px. Prompt evaluation and fixed inference overhead dominate over token count; the codebase uses a single unified 800px downscaled path (`.opt.jpg`), eliminating dimension-keyed cache fragmentation with zero fidelity loss.
   - **Locate Element (800px)**: Retains the measured 800px fidelity sweet spot for precise coordinate extraction, skipping the second crop pass when the target is large ($\ge 480\text{px}$).
   - **Vision ThreadPool for `--always` Mode**: In continuous listening mode, VLM inferences are shunted to a background single-worker `ThreadPoolExecutor`. It speaks an immediate "Looking..." acknowledgment and delivers the response when ready, ensuring 11–21s inferences never deafen the microphone loop.

## 4c. Local-First Model Architecture & Gemini Options

### Why 100% Local by Default
`free-voice` runs completely offline on your Apple Silicon Mac by default:
- **Zero API Keys & $0 Cost**: Out-of-the-box operation with Homebrew + Ollama.
- **Zero Rate Limits**: Cloud free tiers enforce aggressive RPM/TPM caps and daily limits (frequently throwing HTTP 429s). Local inference provides unlimited continuous bandwidth.
- **100% Private**: No voice audio, screenshots, window names, or clipboard text ever leave your computer.
- **Zero Content Censorship**: Local open weights do not phone home to remote safety classifiers.

### The Tier 1 Model Tradeoff: Single 8B vs. Smaller 1.5B/3B
- **Single Model (Default: `qwen3-vl:8b`)**: Uses one unified model for both Tier 1 conversational routing and VLM vision fallback. Ollama keeps it warm in memory (`keep_alive=60m`), using ~5.5 GB unified RAM with **zero model-swapping latency**.
- **Smaller Local Model for Tier 1 (`qwen2.5:1.5b` or `qwen2.5:3b`)**: A 1.5B model routes in ~250–320ms on M4 (vs 1.28s for 8B). However, keeping two distinct models loaded requires ~7 GB RAM, and if memory pressure forces an unload, swapping between models incurs a 2–4s reload penalty. Because Tier 0 regex + Quartz + Apple Vision OCR now handle >95% of commands without touching the LLM, the single 8B architecture is the cleanest default.
- Users desiring dedicated sub-300ms routing can set `OLLAMA_ROUTER_MODEL="qwen2.5:1.5b"` in `~/.config/free-voice/.env`.

### Optional Gemini Tier 2 Fallback
If you set `GEMINI_API_KEY`, Tier 2 is enabled for:
- Fast creative code/SVG generation (`draw a cat` in ~2s via cloud TPU vs ~8s locally).
- Broad encyclopedic world trivia via Google Search grounding.
If the API key is omitted, `free-voice` runs 100% locally.

## 5. Execution

**System actions** go through AppleScript (`osascript`) and shell — volume,
media keys (F7/F8/F9 key codes), dark mode, screenshots, timers, app
launch/quit, keystrokes. Nothing here needs third-party tools.

**UI clicks** go through [xa11y](https://github.com/xa11y/xa11y) (MIT), Python
bindings over the native macOS Accessibility tree (`AXUIElement`):

```python
app.locator("button[name*='Reply']").press()
```

CSS-like selectors address the *semantic* UI node, so clicks survive window
moves, resolution changes, and dark mode — the failure mode of pixel
coordinate clickers. If xa11y isn't installed, click commands degrade to a
one-line install hint instead of crashing.

**Permissions** (all for the terminal app you launch from):

| Permission | Needed for |
|---|---|
| Microphone | hearing you |
| Accessibility | keystrokes, xa11y UI tree |
| Input Monitoring | the Right-Option push-to-talk hotkey |
| Screen Recording (macOS 26+) | xa11y seeing window contents (else: menu bars only) |

**Safety:** shutdown, restart, logout, and empty-trash always ask for a spoken
"yes" first (bypassable with `--yes` in `--text` mode only). Apps that don't
expose accessibility data are invisible to xa11y — the vision fallback
(§4b) can still locate their controls on a screenshot, but coordinate clicks
are approximate and never used for destructive or sensitive actions.

## 6. Audio path (current) and the streaming upgrade

**Today:** two input modes share the same cascade.

- **Push-to-talk** (default). `pynput` watches for Right-Option hold →
  `sounddevice` records 16 kHz mono → on release, `faster-whisper`
  (`tiny.en`, Metal, int8, beam 1, VAD filter) transcribes → the cascade
  runs. Perceived latency is release + ~200 ms.
- **Always-listening** (`--always`, or the `Voice Control (Always On).command`
  launcher). A built-in energy VAD watches the mic continuously:
  adaptive RMS noise floor (slow EMA during silence, never trusted below
  a floor of 60 int16-units), speech onset at 3× the floor sustained for
  250 ms, speech end after 900 ms below 0.6× the start threshold
  (hysteresis), a 0.4 s minimum utterance, and a 15 s safety cap.
  Each captured utterance goes through the same three tiers — but a total
  miss is logged, never spoken (`quiet_miss=True`), so background chatter
  costs a transcription and nothing else. Tune with `--sensitivity`
  (higher = easier to trigger).

**Why no wake word:** the standard free engine, openWakeWord, does not work
natively on Apple Silicon — its ONNX models score near zero on ARM64
([issue #309](https://github.com/dscripka/openwakeword/issues/309),
[#336](https://github.com/dscripka/openwakeword/issues/336)); the only
workaround is a ~500 MB TensorFlow install plus a runtime shim, which fails
the "one command, no fiddling" bar. The energy VAD needs no new
dependencies and works out of the box; the honest tradeoff is that any
speech — TV included — gets transcribed. In a noisy room, push-to-talk
wins. A real wake word remains a documented future upgrade, not a shipped
claim.

**Start at login:** `install.sh` can install `com.free-mac-voice.plist` as a
LaunchAgent (`RunAtLoad` + `KeepAlive`), so `--always` survives reboots and
restarts on crash. Logs go to `/tmp/free-mac-voice.log`. Note: macOS grants
Microphone per-binary, so the first autostart prompts once for Python —
allow it and it sticks. Remove with
`launchctl unload -w ~/Library/LaunchAgents/com.free-mac-voice.plist`.

**Next:** true mid-sentence execution. The router side is already built for
it — `PartialSession.feed(partial)` with gating + dedup is exactly what a
streaming STT loop should drive. The remaining work is Mac-side: run
`whisper.cpp --stream` (installed by `install.sh`) and feed its partial
hypotheses into `feed()`, firing Tier 0 the moment a command completes while
the user is still talking. That is the Andy Gao effect; everything it needs
except the audio plumbing is in this repo.

## 7. Files

| File | What it is |
|---|---|
| `free_voice.py` | the whole system: audio, 3 tiers, actions, CLI |
| `install.sh` | one-command macOS setup (re-runnable) |
| `upgrade.sh` | one-command update for existing installs (git or zip) |
| `welcome.html` | 60-second visual start guide, opened post-install |
| `Voice Control.command` | double-click launcher: push-to-talk |
| `Voice Control (Always On).command` | double-click launcher: always-listening |
| `com.free-mac-voice.plist` | LaunchAgent template for start-at-login (filled in by install.sh) |
| `prime_permissions.sh` | pops macOS's native Allow dialogs (mic, accessibility, screen recording) + opens Settings for the one manual toggle (Input Monitoring) |
| `requirements.txt` | Python deps |
| `ARCHITECTURE.md` | this file |

## 7c. Permissions — why the user still clicks

macOS TCC (Transparency, Consent, and Control) requires a human to grant
Microphone, Accessibility, Input Monitoring, and Screen Recording. No
installer can grant them silently — that would be a security hole, and
every voice app, free or paid, hits the same wall. What an installer
*can* do is provoke the OS's own dialogs instead of making the user hunt
through Settings: `prime_permissions.sh` attempts a 1-second mic recording
(dialog #1), talks to System Events via AppleScript (dialog #2), and takes
a screenshot (dialog #3), then verifies Accessibility via
`AXIsProcessTrusted` and opens the Privacy & Security pane for Input
Monitoring — the one permission Apple offers no dialog API for. Previously
denied permissions never re-prompt; those must be flipped by hand.

`prime_permissions.sh` also handles the microphone: it installs the
`switchaudio-osx` CLI (Homebrew) if missing, looks for the iPhone among
audio inputs, and selects it as the system input when present. Either way
it writes `VOICE_MIC=iPhone` to `~/.free-voice/.env`, which `free_voice.py`
honors via substring matching (`resolve_input_device()`) in all three
recording paths — so the iPhone mic is used whenever it's in range, with
fallback to the system default otherwise. `--mic NAME` overrides per run.
The one thing no script can do: make the iPhone *appear* — that needs
Continuity (same Apple ID, Wi-Fi + Bluetooth, nearby/unlocked).

## 7b. Updating

`upgrade.sh` exists because most users will install from the zip, not a
clone. It detects the install style: `.git` present → `git pull --ff-only`;
otherwise it downloads the latest `main` tarball from GitHub and overlays
the files, explicitly skipping `.venv` (and `.git` if ever present), then
re-runs `install.sh --update` — the non-interactive mode that refreshes
Homebrew/Python dependencies and migrates stale `.env` defaults without
re-popping permission dialogs or the login prompt. User config
(`~/.free-voice/.env`) keeps every user customization; only known-retired
default values (e.g. an old default model) are bumped, with a `.bak` backup.
Finally `upgrade.sh` restarts the LaunchAgent service (if installed) so the
new code actually takes effect — previously the old process kept running
until the next reboot.

## 7c. Reliability features

- **Destructive confirmation** (both tiers): quit/close-all-windows/shutdown/
  restart/logout/empty-trash ask for a spoken "yes" first (Tier 0 via
  `confirm_spoken`, Tier 1 via `_tier1_confirm`); `--yes` skips it in
  `--text` mode. Chained commands confirm each destructive part separately.
- **Model pre-warm**: `prewarm_ollama()` fires a background thread at startup
  so the 8B model is resident before the first command, not loaded by it.
- **Screenshot cache**: `capture_screenshot()` caches for 8 s — a
  describe-then-click sequence reuses the shot instead of capturing twice.
- **Two-pass vision clicks**: after the first coordinate guess, a 480-px crop
  around the guess is re-asked for finer coordinates (Pillow; skipped
  gracefully when absent).
- **Status command**: "are you working" speaks mic, model readiness, Gemini
  state, and the last backend error (tracked via `note_error()`).
- **Gemini fallback**: ANY Gemini failure — not just 429 — falls back to the
  local model and says so.

Environment variables (`~/.free-voice/.env`): `GEMINI_API_KEY`,
`GEMINI_MODEL` (default `gemini-2.5-flash`), `WHISPER_MODEL` (default
`tiny.en`), `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_TIER1`, `TIER1_MIN_CONFIDENCE`.

## 8. Testing & Measured Benchmarks

Everything testable without a Mac was tested on Linux:

```bash
python3 -m py_compile free_voice.py
python3 free_voice.py --list
python3 free_voice.py --text "open notes" --dry-run
python3 free_voice.py --partial "open no" --dry-run        # must NOT fire
python3 free_voice.py --partial "open notes" --dry-run     # fires exactly once
python3 free_voice.py --text "click the Reply button" --dry-run
```

#### Measured Real-World Latency (Apple M4 Mac mini, 16 GB unified RAM)

Measured end-to-end execution times from command dispatch to action execution and `say` voice synthesis completion:

| Command | Routing Path | Decision / Inference Latency | Total End-to-End Time |
|---|---|---|---|
| `close notes` | Tier 0: Regex router | < 1 ms | **1.94s - 2.06s** |
| `open notes` | Tier 0: Regex router | < 1 ms | **2.18s - 3.30s** |
| `what time is it` | Tier 0: Regex router | < 1 ms | **2.59s - 2.68s** |
| `could you please open notes` | Tier 1: `qwen2.5:1.5b` fallback | **0.32s** | **2.53s** |
| `could you please open notes` | Tier 1: `qwen3-vl:8b` + `num_ctx=1024` | **1.26s** | **3.66s** |

Key takeaway:
- **Fast Tier 0 reflexes**: Standard everyday commands execute in < 1 ms router time and complete within ~2 seconds total roundtrip including speech response.
- **Micro-model fallback (`qwen2.5:1.5b`)**: Evaluates natural language variants in 0.32s with 986 MB RAM footprint.
- **Vision-Language fallback (`qwen3-vl:8b`)**: With `num_ctx=1024` and assistant prefill, routing latency is a consistent **1.26s p50** across all prompts. Without `num_ctx`, the default KV context causes 1.3–6.2s variance (p50=5.75s) depending on prompt position. `num_ctx=512` was actively harmful (+28% slower due to context truncation pressure). The routing system prompt is ~297 tokens, giving 727 tokens of headroom at 1024.

### Component Benchmark Breakdown (`benchmarks/bench.py`)

| Component / Benchmark | Samples (\(n\)) | Mean | Median (\(p50\)) | 95th %tile (\(p95\)) | Status / Notes |
|---|---|---|---|---|---|
| **Tier 0 Routing** | 200 | 0.3 ms | **0.3 ms** | 0.3 ms | Corpus of 23 commands incl. chained commands |
| **Tier 0 Partial Gating** | 200 | 0.2 ms | **0.2 ms** | 0.2 ms | 10 growing prefixes of `"open notes"` |
| **Chain Dispatch Overhead** | 50 | 0.4 ms | **0.3 ms** | 0.5 ms | `"open notes and snap left"` sequential dispatch |
| **Quartz Window Summary** | 20 | 2.2 ms | **1.3 ms** | 15.8 ms | Instant orientation: no screenshot, no VLM (~8,900× faster) |
| **Apple Vision OCR Locate** | 5 | 0.49s | **0.46s** | 0.60s | Native OCR text locate: ~45× faster than VLM (0.46s vs 21.16s) |
| **Tier 1 Cold (Model Reload)** | 1 | 9.37s | **9.37s** | 9.37s | First call reloading model into memory |
| **Tier 1 Warm (`num_ctx=1024`)** | 5 | 1.28s | **1.27s** | 1.32s | Consistent 1.2–1.3s; `num_ctx=1024` + assistant prefill |
| **Screenshot Capture** | 5 | 0.19s | **0.18s** | 0.24s | Native macOS `screencapture` to temp file |
| **Vision: Describe Screen (640px)** | 3 | 12.77s | **11.58s** | 15.30s | Screenshot + `sips` 640px downsample + `qwen3-vl:8b` |
| **Vision: Locate Element (800px)** | 3 | 21.16s | **21.12s** | 21.25s | Native `sips` 800px downsample + coordinate query; adaptive skip for $\ge 480\text{px}$ targets |
| **Whisper STT (`tiny.en`)** | 3 | 2.69s | **1.20s** | 5.70s | On-device STT encode/decode pipeline |
| **Earcon Audio Feedback** | 5 | 3.2 ms | **2.6 ms** | 4.9 ms | Non-blocking `afplay` sound trigger |

