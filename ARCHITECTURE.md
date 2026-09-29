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

## 4b. Screen vision (local, same model)

"What's on my screen" captures a screenshot (`screencapture -x`) and sends it
to `qwen3-vl:8b` with the question — ~1–3 s once the model is resident.
`vision_ask()` is also the fallback behind UI clicks: when the accessibility
tree has no node matching ("click the blue Submit button" in a canvas-drawn
UI), the model returns the element's center as 0–1000 fractions and pynput
clicks there. Tree-first ordering keeps coordinates approximate-only; vision
clicks are never used for destructive or sensitive actions.

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
re-runs `install.sh` to refresh Homebrew/Python dependencies. User config
(`~/.free-voice/.env`) is never touched. The LaunchAgent, if installed,
keeps working because it points at the repo's venv by absolute path.

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

### Measured Real-World Latency (Apple M4 Mac mini, 16 GB unified RAM)

Measured end-to-end execution times from command dispatch to action execution and `say` voice synthesis completion:

| Command | Routing Path | Decision / Inference Latency | Total End-to-End Time |
|---|---|---|---|
| `close notes` | Tier 0: Regex router | < 1 ms | **1.94s** |
| `open notes` | Tier 0: Regex router | < 1 ms | **2.18s** |
| `what time is it` | Tier 0: Regex router | < 1 ms | **2.59s** |
| `could you please open notes` | Tier 1: Local Ollama LLM | **0.32s** | **2.53s** |

Key takeaway: With `"think": false` enabled on local Ollama models, Tier 1 routing decision latency dropped from 0.40s down to **0.32s** (~20% speedup), eliminating unnecessary reasoning traces during real-time voice handling.
