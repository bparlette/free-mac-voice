# ARCHITECTURE — how free-mac-voice actually works

This is the detailed implementation companion to the [README](README.md).
Every latency number below is either measured (cited) or labeled an estimate.
Nothing here costs money.

## 1. The pipeline

```
mic (16 kHz PCM)
  ─► faster-whisper tiny.en, Metal, int8          ~100–300 ms   (local)
  ─► Tier 0: regex router + completion gating    <1 ms         (local)
  ─► Tier 1: Ollama qwen2.5:1.5b, JSON mode      ~0.7–1 s warm (local, Tier 0 miss only)
  ─► Tier 2: Gemini API free tier                2–5+ s        (Tier 1 miss/unsure only)
  ─► execution: AppleScript / shell / xa11y      ~50–100 ms   (local)
  ─► macOS `say` confirmation                                  (local)
```

Design principle: **a cascade, not a committee.** Each tier only wakes when
every faster tier failed. The common path (a standard command) never touches
anything slower than a millisecond.

### Why not a 7B model as the router?

A 7B LLM on a base M4 Mac mini decodes at roughly **~24 tokens/second**
(reported across community benchmarks) — a routing decision over a few-hundred-token
prompt plus JSON output lands at **~2–4 seconds**. Running that on every
streaming speech chunk permanently lags behind the speaker and destroys the
mid-sentence execution effect. The 1.5B model runs ~3× faster (≈60–70 tok/s);
a community benchmark of `qwen2.5:1.5b` via Ollama on Apple Silicon measured
**median 703 ms, mean ~1 s end-to-end** per request
([source](https://github.com/chrismckee1/scribe/blob/HEAD/macos/CLEANUP-MODEL-BENCHMARK.md)).
That is still too slow to run per-chunk — which is exactly why it is Tier 1
(fallback), never the primary router. Measure on your own machine with:

```bash
time curl -s http://localhost:11434/api/chat -d '{
  "model": "qwen2.5:1.5b", "format": "json", "stream": false,
  "options": {"temperature": 0, "num_predict": 60},
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

## 3. Tier 1 — local 1.5B fallback (JSON mode)

Fires **only** on a Tier 0 miss. `ollama_route()` POSTs to
`http://localhost:11434/api/chat` with:

- `model`: `qwen2.5:1.5b` (override with `OLLAMA_MODEL`)
- `format: "json"` — Ollama's structured output
- `keep_alive: "30m"` — the model stays resident in unified memory, dodging
  the **20–30 s cold-start** penalty on first use
- `options: {temperature: 0, num_predict: 80}` — deterministic, short output

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
expose accessibility data are invisible to xa11y — documented, not worked
around.

## 6. Audio path (current) and the streaming upgrade

**Today:** push-to-talk. `pynput` watches for Right-Option hold → `sounddevice`
records 16 kHz mono → on release, `faster-whisper` (`tiny.en`, Metal, int8,
beam 1, VAD filter) transcribes → the cascade runs. Perceived latency is
release + ~200 ms.

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
| `welcome.html` | 60-second visual start guide, opened post-install |
| `Voice Control.command` | double-click launcher (no terminal needed) |
| `requirements.txt` | Python deps |
| `ARCHITECTURE.md` | this file |

Environment variables (`~/.free-voice/.env`): `GEMINI_API_KEY`,
`GEMINI_MODEL` (default `gemini-2.5-flash`), `WHISPER_MODEL` (default
`tiny.en`), `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_TIER1`, `TIER1_MIN_CONFIDENCE`.

## 8. Testing

Everything testable without a Mac was tested on Linux:

```bash
python3 -m py_compile free_voice.py
python3 free_voice.py --list
python3 free_voice.py --text "open notes" --dry-run
python3 free_voice.py --partial "open no" --dry-run        # must NOT fire
python3 free_voice.py --partial "open notes" --dry-run     # fires exactly once
python3 free_voice.py --text "click the Reply button" --dry-run
```

Untested until a Mac runs it: actual audio capture, AppleScript actions,
permissions behavior, xa11y against real apps, and Tier 1 latency on the
target machine (measure with the `curl` command in §1).
