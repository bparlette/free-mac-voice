# 🎙️ Free Mac Voice Control

Talk to your Mac. **$0 per command, forever.** No subscriptions, no API keys, no cloud — every word is transcribed and understood on your own machine.

Hold **Right Option ⌥**, speak, release. Your Mac does it.

## Install (one command)

```bash
git clone https://github.com/bparlette/free-mac-voice.git
cd free-mac-voice
bash install.sh
```

The installer sets up everything: Homebrew packages, a Python environment, the local AI fallback model, and then pops up a 60-second visual start guide (`welcome.html`).

## Use

| You say | Your Mac |
|---|---|
| “open notes” | Opens Notes |
| “open notes and snap left” | Chained: opens app and tiles it to half screen |
| “snap left” / “maximize” | Window layout and tiling |
| “next space” / “prev space” | Switches macOS Spaces / virtual desktops |
| “move to next display” | Moves active window to adjacent monitor |
| “new tab” / “close tab” | Browser / terminal tab management |
| “reopen tab” / “refresh” | Reopens closed tab or reloads current page |
| “scroll down” / “scroll up” | Smooth scrolling or page navigation |
| “switch to safari” | Focuses background application |
| “read clipboard” | Reads your copied text aloud |
| “type today's date” | Types formatted date macro |
| “what's on my screen” | AI screen vision & summary (Qwen3-VL) |
| “are you working” | Spoken health check: mic, models, last error |
| “quit spotify” | Quits the app (asks first) |
| “set volume to 30” | Sets volume |
| “play” / “next” | Media keys |
| “set a timer for 5 minutes” | Background timer, speaks when done |
| “click the Reply button” | Clicks the real button in the front app |
| “click” | Clicks wherever the mouse already is |
| “move the mouse to the toggle” | Moves the cursor onto a control (no click) |
| “move mouse up / left 200” | Nudges the cursor (default 100 px) |
| “scroll up / scroll down 3” | Scrolls the window under the cursor |
| “search best pizza near me” | Google search |
| “what time is it” | Speaks the time |
| “close” / “close all windows” | Closes active tab/window or all windows |
| “lock” | Locks the screen |

Say **“help”** anytime to hear available commands. Or double-click **Voice Control.command** — no terminal needed. Includes instant audio earcons (subtle audio chime on keypress, release click on completion).

## Always listening

Rather than holding a key, just talk:

- **Try it once:** double-click **Voice Control (Always On).command**. Speak naturally; non-commands are ignored silently. Ctrl-C or close the window to stop.
- **Every login, even after reboots:** re-run `bash install.sh` and answer **y** to the start-at-login prompt. It restarts itself if it ever crashes (logs at `/tmp/free-mac-voice.log`).

There's no wake word — any speech is the trigger. In a noisy room, push-to-talk is calmer. If it triggers too easily, raise the bar: `python3 free_voice.py --always --sensitivity 4.5` (default 3.0).

## Use your iPhone as the mic

A Mac mini has no built-in mic. Two free options:

1. **Continuity (built-in, no app):** same Apple ID on iPhone + Mac, Wi-Fi and Bluetooth on, iPhone nearby → Mac **System Settings → Sound → Input → your iPhone**.
2. **WO Mic (free app):** WO Mic from the iOS App Store + the free Mac client from wolicheng.com, connected over Wi-Fi. Ad-supported and reviews are mixed — try Continuity first.

## Updating

```bash
bash upgrade.sh
```

One command: pulls the latest version (git or zip install), migrates stale
settings in `~/.free-voice/.env` (e.g. an old default model gets bumped to the
current one — your customizations are never touched), pulls the configured
model, refreshes Python dependencies, and restarts the always-listening
service if you have it installed. No permission dialogs, no questions.

New since the last release: "are you working" (spoken health check), model
pre-warm at startup so the first command doesn't stall, screenshot caching for
describe-then-click, two-pass vision click refinement, confirmation now covers
"quit …" and "close all windows" (plus shut down / restart / log out / empty
trash), and Gemini failures of any kind fall back to the on-device model.
Browser and desktop control: "new tab" / "close tab" / "reopen tab" / "refresh" /
"go back" / "go forward" / "page down" / "page up" / "find on page", "next space"
/ "previous space" for virtual desktops, and "move to next display". Speech
confirmations no longer block: acknowledgments play fire-and-forget so actions
feel instant, and vision queries send an 800px sips-downscaled copy (~60%
faster inference) while click refinement still crops the full-res shot.
Vision clicks now ask the model for the target's size too, and skip the
second refinement inference (~5-8s) when the target is large enough that
the first-pass center can't miss.

## How it stays free (30-second version)

Three tiers, fastest first. The system only uses a slower tier when the faster one can't help:

1. **Tier 0 — the reflex (<1 ms).** Pattern matching on your words. Handles every standard command instantly — even mid-sentence, but only when the command is complete (“open notes” fires; “open no” never misfires).
2. **Tier 1 — the local fallback (~1–2 s).** A vision-language AI (Qwen3-VL 8B) running on your Mac translates unusual phrasing (“could you be a dear and open my browser thing”) into the same commands — and understands your screen (“what's on my screen”). Still $0, still on your Mac.
3. **Tier 2 — the answerer (optional).** Google's free API tier answers open-ended questions aloud, with live web search for fresh answers. Needs a free API key; skip it and Tier 2 just stays quiet. If the free quota runs out mid-day, it falls back to the on-device model automatically.

Full implementation details, latency math, and honest limits: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Measured Performance (Apple M4 Mac mini, 16 GB)

Measured end-to-end execution times on an Apple M4 Mac mini (including process spawn, command execution, and macOS voice confirmation):

| Command | Routing Path | Decision / Inference Latency | Total End-to-End Time |
|---|---|---|---|
| `close notes` | Tier 0 (Instant Regex) | < 1 ms | **1.94s - 2.06s** |
| `open notes` | Tier 0 (Instant Regex) | < 1 ms | **2.18s - 3.30s** |
| `what time is it` | Tier 0 (Instant Regex) | < 1 ms | **2.59s - 2.68s** |
| `could you please open notes` | Tier 1 (`qwen2.5:1.5b` fallback) | **0.32s** | **2.53s** |
| `could you please open notes` | Tier 1 (`qwen3-vl:8b` vision fallback) | **1.30s** | **3.70s** |

*Note: For thinking models such as `qwen3-vl:8b`, an assistant prefill bypasses reasoning tokens, keeping 8.8B-parameter decision latency down from 8.2s to **1.30s** on Apple Silicon without losing structured routing accuracy.*

## Tests & benchmarks

No mic, model, or Mac required — the suite stubs all hardware:

```bash
python3 -m unittest discover -s tests   # 91 unit tests, stdlib only
```

Component benchmarks (Tier 0 routing, Tier 1 cold vs warm, screenshot,
vision describe/locate, whisper transcription, earcon latency) — anything the
machine can't do is reported SKIPPED with a reason instead of failing:

```bash
python3 benchmarks/bench.py            # full run, appends to benchmarks/results.jsonl
python3 benchmarks/bench.py --quick    # smoke run, fewer iterations
```

## Requirements

- Apple Silicon Mac (M1/M2/M3/M4)
- A microphone — note: a Mac mini has **no built-in mic**. An iPhone on the same Wi-Fi works (Continuity), or any USB mic.
- 3 macOS permissions for your terminal: **Microphone, Accessibility, Input Monitoring** (+ **Screen Recording** on macOS 26+ for “click the … button”). The installer walks you through them.

## Honest limits

- It runs **commands**, not open-ended screen errands. “Click the Reply button” ✓. “Find the cheapest flight and book it” ✗ — that's a different kind of agent.
- The live loop acts on key release; true mid-sentence streaming is structured in code but needs `whisper.cpp --stream` wired up (documented in ARCHITECTURE.md).
- Brightness keys do nothing on most external displays. Some toggles open Settings instead of flipping switches.

## License

MIT — do whatever you want with it.
