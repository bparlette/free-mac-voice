# 🎙️ Free Mac Voice Control

Talk to your Mac. **$0 per command, forever.** No subscriptions, no API keys, no cloud — every word is transcribed and understood on your own machine.

Hold **Right Option ⌥**, speak, release. Your Mac does it.

## Install (one command)

```bash
git clone https://github.com/bparlette/free-mac-voice.git
cd free-mac-voice
bash install.sh        # interactive setup
# Or for zero-touch unattended setup (accepts defaults, enables login service):
bash install.sh --yes
```

The installer sets up everything: Homebrew packages, whisper.cpp with Metal acceleration, a Python environment, the local AI fallback model, and opens a 60-second visual start guide (`welcome.html`).

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
| “read my screen to me” | Detailed top-to-bottom window readout |
| “start dictating” | Continuous dictation until “stop dictating” |
| “macro standup runs open notes and open slack” | Saves custom voice macro (chained actions) |
| “remind me in 10 minutes to check oven” | Spoken reminder with audio chime |
| “turn my screen into ascii art” | Converts screenshot to dark-mode ASCII HTML page |
| “draw ascii flower” | Instant ASCII art generated & opened in browser |
| “draw a cat” | On-device LLM generates SVG & opens in browser |
| “play some jazz” | Plays matching playlist in Apple Music |
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
| “next app” / “previous app” | Switches applications (⌘Tab / ⇧⌘Tab) |
| “next tab” / “previous tab” | Switches browser/terminal tabs (^Tab / ^⇧Tab) |
| “type URL” / “address bar” | Focuses browser address bar (⌘L) |
| “search Mac” | Opens Spotlight search (⌘Space) |
| “show all windows” | Mission Control overview (F3) |
| “show desktop” | Reveals desktop (F11) |
| “next window” | Cycles windows of current app (⌘`) |
| “hide everything else” | Hides all other apps (⌥⌘H) |
| “kill safari” | Force quits an app (asks first) |
| “private window” | Opens private / incognito window (⇧⌘N) |
| “bookmark this” | Bookmarks active page (⌘D) |
| “paste plain text” | Pastes without formatting (⌥⇧⌘V) |
| “find next” | Jumps to next match in document/page (⌘G) |
| “save as” | Opens Save As dialog (⇧⌘S) |
| “print this” | Opens system print dialog (⌘P) |
| “record screen” | Opens macOS screenshot & recording toolbar (⇧⌘5) |
| “go to desktop / documents / downloads / apps” | Finder quick navigation folders |
| “new folder” | Creates new folder in active window (⇧⌘N) |
| “rename to project notes” | Renames selected file/item |
| “duplicate this” | Duplicates selected file (⌘D) |
| “get file info” | Opens Get Info inspector (⌘I) |
| “preview this” | Quick Look preview (Space) |
| “trash this” | Moves selected file to Trash (⌘⌫) |
| “list / icon / column / gallery view” | Switches Finder view modes (⌘1–⌘4) |
| “close” / “close this” / “close all windows” | Closes active tab/window or all windows |
| “lock” / “lock it down” | Locks the screen |
| “dim screen” / “brightness up” | Controls screen brightness |
| “switch to computer” / “switch to tv” | Switches Samsung TV input (needs one-time SmartThings setup, see below) |
| “switch input to HDMI 2” | Sets Samsung TV to a named input source |
| “turn on the tv” / “turn off the tv” | Powers the Samsung TV on/off |
| “set tv volume to 25” / “tv volume up” / “tv volume down by 5” | Sets or steps Samsung TV volume (0–100) |
| “mute tv” / “unmute tv” | Mutes or unmutes Samsung TV audio |
| “pause tv” / “play tv” / “stop tv” | Controls Samsung TV media playback |

Say **“help”** anytime to hear available commands. Or double-click **Voice Control.command** — no terminal needed. Includes instant audio earcons (subtle audio chime on keypress, release click on completion).

## Always listening

Rather than holding a key, just talk:

- **Try it once:** double-click **Voice Control (Always On).command**. Speak naturally; non-commands are ignored silently. Ctrl-C or close the window to stop.
- **Every login, even after reboots:** re-run `bash install.sh` and answer **y** to the start-at-login prompt. It restarts itself if it ever crashes (logs at `/tmp/free-mac-voice.log`).

Always-listening uses the wake word **"Mac"** by default:
- **Single breath:** `"Mac, open notes"` · `"Hey Mac, switch to TV"` · `"Mac turn on the TV"`
- **Two-stage:** Say `"Mac"` (or `"Hey Mac"`), listen for the chime, then speak your command within 8 seconds.
- Normal room conversation is ignored so ambient chatter never triggers accidental actions.
- In push-to-talk mode (Right Option ⌥), the wake word is optional — the keypress itself signals intent.
- Change the wake word in `~/.free-voice/.env` (`VOICE_WAKE_WORD=mac`), pass `--wake-word <name>`, or disable it for open-mic mode with `--no-wake-word`.
- **Wake feedback:** customize audio response when "Mac" is heard alone via `VOICE_WAKE_FEEDBACK="both"` (`both`, `chime`, `voice`, or `silent`) and `VOICE_WAKE_CHIME="Tink.aiff"` (any sound in `/System/Library/Sounds/`).

### Real-Time Streaming Mode (sub-500ms mid-speech firing)

Run with `--stream` for instant mid-sentence recognition powered by `whisper-stream` (Metal GPU accelerated):

```bash
python3 free_voice.py --stream
```

Whisper streams incoming audio chunks continuously. The moment you finish speaking a valid command (e.g. *"Mac, open notes"*), Tier 0 reflex triggers immediately without waiting for silence or utterance boundary timeouts.

### Menu Bar Status Indicator

Keep visual track of listening state with the lightweight native macOS menu bar app:

```bash
python3 menu_bar.py
```

Reflects real-time state:
- 🎙️ **Listening** — mic open, waiting for wake word or command
- 👂 **Heard Wake Word** — "Mac" detected, listening window active (8s)
- ⚙️ **Working** — executing action / running vision OCR or model
- 💤 **Idle** — standby / muted

Click the menu bar item to view current status or quit.

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
the first-pass center can't miss. Continuous dictation ("start dictating",
"take notes" — types until you say "stop dictating"), custom voice macros
("macro standup runs open slack and open zoom", saved to
~/.config/free-voice/macros.json), and fun stuff: "turn my screen into
ASCII art", "draw a cat" (LLM-generated SVG opened in the browser),
"roll a die", "flip a coin", "ask the magic 8 ball", "tell me a joke",
"remind me in 10 minutes to …", "read my screen to me", and
"play some jazz" (first matching Music playlist).

## How it stays free (30-second version)

Four tiers, fastest first. The system only uses a slower tier when the faster one can't help:

1. **Tier 0 — the reflex (<1 ms).** Pattern matching on your words with fuzzy app matching and conversational noise stripping. Handles standard commands instantly (0.2–0.3 ms) — even mid-sentence, but only when the command is complete (“open notes” fires; “open no” never misfires). Real UI clicks run through Accessibility (`xa11y`) with a native Apple Vision OCR fast path (**~0.25s end-to-end**).
2. **Tier 0.5 — local decision model (~50 ms).** Using Ollama 0.35's Jev-style System One API (`tev1:0.8b`), conversational commands that miss regex (*"turn the sound down a little bit"*, *"could you open my browser"*) are classified in a single forward pass with calibrated probabilities (~50–120ms), bypassing token-by-token generation.
3. **Tier 1 — local vision-language AI (~1–2 s).** A full vision-language model (`qwen3-vl:8b`) running on your Mac extracts complex open entities and understands your screen (“what's on my screen”). 100% offline, zero API keys, zero rate limits, zero data leaving your Mac.
4. **Tier 2 — the answerer (optional).** Google's free Gemini API answers general trivia or open-ended web questions aloud (with fallback to local model).

### Local by Default vs. Optional Gemini
By default, **Free Mac Voice is 100% on-device and local**. No audio, screenshots, or metadata leave your Mac.

**Why you might want Gemini:**
- **Faster creative SVG generation:** Generating complex SVGs for "draw a cat" takes ~2 seconds via cloud TPU compared to ~8-10 seconds on local 8B model.
- **Broad world trivia:** Provides live web-grounded answers for open-ended knowledge questions ("who won the game last night").

**How to enable Gemini:**
Add your free API key to `~/.config/free-voice/.env` or export it in your shell:
```bash
export GEMINI_API_KEY="AIzaSy..."
```
If unset (the default), `free-voice` runs completely local and private on your hardware.

### Samsung TV control (optional)
Control input switching, power, volume, mute, and media playback on your Samsung Smart TV over Wi-Fi via the SmartThings cloud API.

**One-time setup (about 5 minutes):**
1. **SmartThings app:** On iPhone/Android, open the SmartThings app, sign in with your Samsung account, and add your Samsung TV (usually auto-detected on the same Wi-Fi).
2. **Personal Access Token:** Go to [account.smartthings.com](https://account.smartthings.com) → *Personal Access Tokens* → *Generate new token*. Name it `mac-voice` and grant scopes `r:devices:*` (read) and `x:devices:*` (run commands). Copy the token.
3. **Environment variables:** Add the token and device ID to `~/.config/free-voice/.env` (or export in shell):
   ```bash
   SAMSUNG_ST_TOKEN=your-token-here
   SAMSUNG_TV_DEVICE_ID=your-device-id-here
   # Optional overrides (defaults shown):
   SAMSUNG_INPUT_COMPUTER=HDMI1
   SAMSUNG_INPUT_TV=digitalTv
   ```
4. **Discover:** Run `python3 samsung_tv.py discover` to list your devices (copy the TV's device ID into step 3) and check supported input sources. If your Mac is connected to a different port than HDMI1, configure `SAMSUNG_INPUT_COMPUTER` accordingly.

---

## Measured Performance (Apple M4 Mac mini, 16 GB)

### End-to-End Command Latency

Measured times on an Apple M4 Mac mini (including process spawn, command execution, and macOS voice confirmation):

| Command | Routing Path | Decision / Inference Latency | Total End-to-End Time |
|---|---|---|---|
| `close notes` | Tier 0 (Instant Regex) | < 1 ms | **1.94s - 2.06s** |
| `open notes` | Tier 0 (Instant Regex) | < 1 ms | **2.18s - 3.30s** |
| `click on the File button` | Tier 0 + Apple Vision OCR | ~61 ms | **0.18s - 0.28s** |
| `what time is it` | Tier 0 (Instant Regex) | < 1 ms | **2.59s - 2.68s** |
| `could you please open notes` | Tier 1 (`qwen2.5:1.5b` fallback) | **0.32s** | **2.53s** |
| `could you please open notes` | Tier 1 (`qwen3-vl:8b` + `num_ctx=1024`) | **1.26s** | **3.66s** |

*Note: `num_ctx=1024` caps the KV context window to give consistent 1.0–1.3s Tier 1 latency. Without it, qwen3-vl:8b's large default context produces 1.3–6.2s variance (p50=5.75s). Whisper STT (`tiny.en`, int8) runs in **109–139ms** on M4 for 1–3.5s of real speech after a 360ms first-call model load — entirely local, no cloud dependency.*

### Component Benchmark Breakdown (`benchmarks/bench.py`)

Fresh benchmark run on Apple M4 Mac mini (16 GB unified RAM, macOS Darwin arm64, `qwen3-vl:8b` resident):

| Component / Benchmark | Samples (\(n\)) | Mean | Median (\(p50\)) | 95th %tile (\(p95\)) | Status / Notes |
|---|---|---|---|---|---|
| **Tier 0 Routing** | 200 | 0.3 ms | **0.3 ms** | 0.3 ms | Regex matcher across 23 commands |
| **Tier 0 Partial Gating** | 200 | 0.2 ms | **0.2 ms** | 0.2 ms | 10 prefixes of “open notes” |
| **Chain Dispatch Overhead** | 50 | 0.4 ms | **0.3 ms** | 0.5 ms | Multi-intent sequential dispatch |
| **Tier 0 Click End-to-End** | 10 | 0.28s | **0.24s** | 0.65s | Tier 0 regex $\to$ xa11y $\to$ cached OCR $\to$ click |
| **Quartz Window Summary** | 20 | 2.2 ms | **1.3 ms** | 15.8 ms | Window list orientation: no screenshot, no VLM |
| **Apple Vision OCR (Cached)** | 5 | 61.4 ms | **60.1 ms** | 64.7 ms | 8s screenshot cache reuse + Fast OCR level |
| **Apple Vision OCR (Fresh)** | 5 | 0.25s | **0.24s** | 0.28s | Fresh `screencapture` + Fast OCR level |
| **Tier 1 Cold (Model Reload)** | 1 | 9.65s | **9.65s** | 9.65s | First call reloading model into memory |
| **Tier 1 Warm (`num_ctx=1024`)** | 5 | 1.30s | **1.29s** | 1.35s | Resident Ollama route with prefill |
| **Screenshot Capture** | 5 | 0.23s | **0.23s** | 0.24s | Native macOS `screencapture` to temp file |
| **Vision: Describe Screen (800px)** | 3 | 11.82s | **11.36s** | 12.76s | Screenshot + `sips` 800px + `qwen3-vl:8b` fallback |
| **Vision: Locate Element (800px)** | 3 | 20.87s | **20.88s** | 20.92s | Coordinate query; adaptive skip for $\ge 480\text{px}$ targets |
| **Whisper STT (`tiny.en`)** | 3 | 2.69s | **1.20s** | 5.70s | On-device STT encode/decode pipeline |
| **Earcon Audio Feedback** | 5 | 3.2 ms | **2.6 ms** | 4.9 ms | Non-blocking `afplay` sound trigger |

## Tests & benchmarks

No mic, model, or Mac required — the suite stubs all hardware:

```bash
python3 -m unittest discover -s tests   # 131 unit tests, stdlib only
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
