# 🎙️ Free Mac Voice Control

**Fast, private, 100% on-device voice control for macOS.**  
**$0 per command, forever.** No cloud subscriptions, no API keys required, no telemetry — every syllable is transcribed and executed locally on Apple Silicon.

[![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-blue.svg)](https://apple.com)
[![ASR Engine](https://img.shields.io/badge/ASR-Phonon--2%20(164MB%20MLX)-orange.svg)](https://huggingface.co/FermionResearch/Phonon-2)
[![TTS Engine](https://img.shields.io/badge/TTS-Kokoro--82M%20(Neural)-purple.svg)](docs/index.html)
[![Hardware](https://img.shields.io/badge/accelerated-Metal%20GPU%20%2F%20MLX-green.svg)](https://developer.apple.com/metal/)
[![Tests](https://img.shields.io/badge/tests-420%20passing-brightgreen.svg)](tests/)
[![Privacy](https://img.shields.io/badge/privacy-100%25%20On--Device-success.svg)](#privacy--local-by-default)
[![Benchmarks](https://img.shields.io/badge/benchmarks-full%20ledger-informational.svg)](benchmarks/README.md)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> 📒 **Benchmarks:** every test we have run over weeks, what we changed because of it, and what we ruled out, is in the **[Benchmark Ledger](benchmarks/README.md)** ([summary on this page](#-benchmark-ledger-weeks-of-testing-in-one-place)).

---

## ⚡ The Experience

Hold **Right Option ⌥**, speak, release. Or simply talk hands-free using the wake word **"Mac"**:

> *"Mac, open notes and snap left"*  
> *"Mac, turn the TV volume down by 5"*  
> *"Mac, click the Reply button"*  
> *"Mac, what's on my screen?"*

---

## 🚀 One-Command Install

```bash
git clone https://github.com/bparlette/free-mac-voice.git
cd free-mac-voice

# Standard interactive setup:
bash install.sh

# Or 100% unattended / zero-touch setup (accepts defaults & enables login service):
bash install.sh --yes
```

The installer configures everything automatically: Homebrew packages, `whisper.cpp` with Metal acceleration, Python virtual environment, local AI decision and vision models, and opens a visual 60-second start guide (`welcome.html`).

---

## 🧠 The 4-Tier Architecture

Three faster tiers run before any generative model is called. Slower tiers only engage when faster tiers cannot resolve your request:

```mermaid
flowchart TD
    Audio([Spoken Audio]) --> STT[Ultra-Fast Local ASR<br/>Phonon-2 via MLX: ~30-40 ms<br/>or Whisper base.en fallback]
    STT --> W[Wake Word: 'Mac' or Right Option ⌥]
    W --> T0{Tier 0: Regex Reflex<br/>Latency: &lt; 0.3 ms}
    T0 -- Match --> E0[Instant Action / Accessibility Click]
    T0 -- Miss --> T05{Tier 0.5: Semantic Router & Slot Classifier<br/>Cosine Embedding + qwen2.5:1.5b<br/>Latency: ~40-70 ms}
    T05 -- High Confidence --> E05[Calibrated Intent + Parameter Execution]
    T05 -- Complex / Low Confidence --> T1{Tier 1: On-Device VLM<br/>qwen3-vl:8b<br/>Latency: ~1.2 s}
    T1 -- Structured Action / Vision --> E1[VLM Screen Description / Macro]
    T1 -- Open Q&A / Trivia --> T2[Tier 2: Gemini Search Grounding or Local Answer]
    E0 & E05 & E1 & T2 --> TTS[Spoken Response<br/>Kokoro-82M Neural Speech: ~150 ms<br/>or native say fallback]
```

0. **ASR Front-End — Phonon-2 on Apple MLX (~30–40 ms):** Powered by Fermion Research's quantized 164 MB Parakeet-TDT model running natively on Apple Silicon MLX GPU/Neural Engine. Achieves a 5.2% WER matching Whisper Large at 7.2x faster speed with zero silence hallucinations.
1. **Tier 0 — The Reflex (<0.3 ms):** Exact pattern matching on compiled regex tables with fuzzy app resolution and conversational filler stripping. Native macOS Accessibility (`AXCloseButton`) directly closes application windows reliably without dropped keystrokes or confirmation prompts. Real UI clicks route through `xa11y` with an Apple Vision OCR fast-path (**~60 ms**).
2. **Tier 0.5 — Semantic Router & Intent Classifier (~40–70 ms):** Dual-stage semantic bridge. Stage (a) computes in-process cosine similarity against canonical intent embeddings (~2 ms, threshold 0.75). Stage (b) calls local `qwen2.5:1.5b` in JSON mode to classify intents and extract slots. Whisper/Phonon acoustic mishearings (*"john askey picture of a heart"*, *"call an ascii..."*) automatically route to actions without new regexes.
3. **Tier 1 — On-Device Vision-Language Model (~1.2 s):** Local `qwen3-vl:8b` handles visual screen understanding (*"what's on my screen"*, *"read my screen to me"*) and complex multi-entity phrasing. 100% private, offline, and resident in unified memory.
4. **Tier 2 — The Answerer (Optional):** Google's free Gemini API answers general trivia or open-ended web questions with live Google Search grounding. If offline or quota-limited, it falls back to the local model.
5. **Spoken Feedback — Kokoro-82M Neural TTS (~150 ms):** Natural, style-guided human speech with 8 curated personas (Fenrir, Heart, Adam, Sarah, etc.) running locally via ONNX Runtime with zero API calls. Seamlessly falls back to native macOS `say` if needed.

---

## 🗣️ Spoken Commands Reference

### 🚀 Apps, Windows & Spaces
| Voice Command | Action |
|---|---|
| `“open notes”` / `“open my code”` | Opens or focuses app (fuzzy matched to installed apps) |
| `“switch to safari”` / `“focus chrome”` | Prioritizes already-running applications |
| `“open notes and snap left”` | Chained command: opens app and tiles it left |
| `“snap left”` / `“snap right”` / `“maximize”` | Instant window layout and screen tiling |
| `“center window”` / `“minimize”` / `“close”` | Active window management |
| `“next space”` / `“prev space”` | Switches macOS virtual desktops (Spaces) |
| `“move to next display”` | Moves focused window to adjacent monitor |
| `“new tab”` / `“close tab”` / `“reopen tab”` | Browser / terminal tab management |
| `“show all windows”` / `“show desktop”` | Mission Control (F3) and Reveal Desktop (F11) |
| `“quit spotify”` / `“close all windows”` | Clean application shutdown (confirms aloud first) |

### 🍿 Apple TV & Couch Media Experience
| Voice Command | Action |
|---|---|
| `“watch youtube”` / `“open netflix”` | Launches streaming services directly in the browser |
| `“watch disney plus”` / `“open hulu”` / `“open max”` / `“watch prime video”` | Direct-to-app streaming launcher |
| `“watch apple tv”` | Opens native macOS Apple TV app (`/System/Applications/TV.app`) |
| `“search youtube for lofi hip hop”` | Direct search query on YouTube |
| `“skip 10 seconds”` / `“fast forward 30 seconds”` | Jumps forward across YouTube, Netflix, Disney+, VLC |
| `“rewind”` / `“go back 15 seconds”` | Jumps backward across video players |
| `“what did they say?”` | Rewinds 15s and toggles subtitles (signature Apple TV feature) |
| `“subtitles on”` / `“toggle subtitles”` | Toggles closed captions (`c` key standard across web players) |
| `“next episode”` | Plays next video or episode (`Shift+N`) |
| `“fullscreen”` / `“theater mode”` | Toggles macOS full screen (`Ctrl+Cmd+F`) |
| `“airplay”` / `“screen mirroring”` | Opens AirPlay Receiver settings to cast from iPhone or iPad |

### 🗣️ Voice Auditions & High-Definition Speech
*Powered by Kokoro-82M (default neural TTS engine) with instant offline `say` fallback.*
| Voice Command | Action |
|---|---|
| `“pick a voice”` / `“sample voices”` | Rotates through curated voices speaking: `"[Name]. This is what I sound like on your Mac."` |
| `“use voice Adam”` / `“set voice to Heart”` | Selects active voice (saved permanently to `~/.config/free-voice/config.json`) |
| `“switch voice to George”` / `“use voice Sarah”` | Switches persona and confirms in that exact voice |
| `“what is my voice?”` | Reports the currently active voice and TTS engine |

### 🔊 Sound, System & Media
| Voice Command | Action |
|---|---|
| `“set volume to 30”` / `“volume up”` / `“mute”` | System volume control |
| `“play”` / `“pause”` / `“next”` / `“previous”` | macOS media playback keys (Apple Music, Spotify, YouTube) |
| `“play some jazz”` | Launches matching playlist in Apple Music |
| `“dim screen”` / `“brightness up”` | Display brightness controls |
| `“turn wifi off”` / `“turn wifi on”` | Network controls |
| `“dark mode”` / `“light mode”` | Toggles macOS appearance |
| `“lock”` / `“lock it down”` | Instant screen lock |
| `“set a timer for 10 minutes”` | Background countdown, announces aloud on completion |
| `“are you working”` / `“status”` | Spoken health check: active mic, models, and errors |

### 📺 Samsung Smart TV Control (Over Wi-Fi)
*Requires one-time SmartThings token setup (see below).*
| Voice Command | Action |
|---|---|
| `“switch to computer”` / `“switch to tv”` | Switches TV input directly to your Mac or TV mode |
| `“switch input to HDMI 2”` | Switches TV to any named source |
| `“turn on the tv”` / `“turn off the tv”` | Powers the Samsung TV on or off |
| `“set tv volume to 25”` | Sets TV volume to exact level (0–100) |
| `“tv volume up”` / `“tv volume down by 5”` | Steps TV volume |
| `“mute tv”` / `“unmute tv”` | Mutes or unmutes Samsung TV audio |
| `“pause tv”` / `“play tv”` / `“stop tv”` | Controls Samsung TV media streaming playback |

### 👁️ Screen Vision & Smart Clicks
| Voice Command | Action |
|---|---|
| `“click the Reply button”` | Semantic click via Accessibility tree (`xa11y`) |
| `“click the blue Download link”` | High-speed Apple Vision OCR fast-path (**~60 ms**) |
| `“click”` | Clicks at current mouse coordinates |
| `“move mouse up 200”` / `“scroll down 3”` | Cursor positioning and smooth scrolling |
| `“what's on my screen”` | Quartz summary (~1.3 ms) or on-device VLM scene analysis |
| `“read my screen to me”` | Top-to-bottom window text readout |
| `“screenshot”` / `“record screen”` | Native macOS capture shortcuts |

### 🛠️ Dictation, Tools & Custom Shortcuts
| Voice Command | Action |
|---|---|
| `“when I say party mode, set volume to 80 and play some jazz”` | Teaches a new multi-command shortcut |
| `“when I say bedtime, turn off the tv and sleep”` | Teaches custom bedtime sequence |
| `“alias surf to open safari”` | Teaches new custom trigger words |
| `“when I say backup, run bash ~/backup.sh”` | Runs custom local shell scripts via voice |
| `“what are my shortcuts”` / `“list shortcuts”` | Reads your active custom shortcuts aloud |
| `“forget shortcut party mode”` | Deletes a custom shortcut |
| `“start dictating”` | Continuous dictation until you say `“stop dictating”` |
| `“read clipboard”` | Reads your copied clipboard text aloud |
| `“type today's date”` / `“type the time”` | Types formatted date/time stamp |
| `“turn my screen into ascii art”` | Converts screenshot to dark-mode HTML page in browser |
| `“draw a cat”` | Local LLM generates an SVG vector art file and opens it |
| `“tell me a joke”` / `“roll a die”` / `“flip a coin”` | Built-in conversational utilities |

### 🎮 3D Voice-Reactive Vector Runner (Zero-Interference Game Mode)
Free Mac Voice features a built-in 3D endless vector runner powered by WebGL (Three.js), sub-second local reflex routing, and generative world shifts:

| Voice Command | Action |
|---|---|
| `“Mac, start runner game”` / `“play runner game”` | Fires up Ollama, starts backend orchestrator, and launches 60 FPS WebGL game in Safari |
| `“left”` / `“right”` | Steers ship lane without saying "Mac" |
| `“jump”` | Hops over obstacles without saying "Mac" |
| `“faster”` / `“slower”` | Dynamic 2x turbo boost / 0.45x braking slow-motion |
| `“shoot”` | Fires twin laser cannons to shatter obstacles |
| `“change world to [theme]”` | Asynchronously hallucinates 3D palettes via local Qwen (e.g. *"change world to neon matrix"*) |
| `“Mac, close game”` / `“Hey Mac, quit game”` | Terminates runner orchestrator, closes Safari window, restores standard desktop voice control |
| `“Mac, <any macOS command>”` | Wake-word override: controls your Mac mid-game without interference (e.g. *"Mac, open notes"*) |

### 🤖 Rogue Mech Protocol — the Shooter Game (16-Bit Arcade)
A single-file 320×240 pixel-art cockpit game (`integrations/shooter_game/`) with three simultaneous loops: kill tanks and jets through the windshield, keep three reactor cores from staying red for 3 seconds, and beat the Stroop-effect weapons lock. Pick one of the 8 Sticker Kart pilots; your pilot turns to look back at the screen on every kill. Bundled sticker art in `assets/` (falls back to the Sticker Kart CDN, then to procedural pixel heads).

| Voice Command | Action |
|---|---|
| `“Mac, start shooter game”` / `“play shooter”` / `“play rogue mech protocol”` | Starts the voice bridge and opens the game in Safari (stops the runner if running; both share `ws://localhost:8765`) |
| `“Cap”`, `“Blaze”`, `“Hook”`, `“Sarge”`, `“Patch”`, `“Sumo”`, `“Zen”`, `“Marshal”` | Selects your pilot by name on the character select screen (or say `“pilot 1”` – `“pilot 8”`) |
| `“deploy”` / `“start”` / `“retry”` | Deploys your chosen pilot into mission or retries after Game Over |
| `“kick”` | Destroys the closest ground tank (big explosion + screen shake) |
| `“shoot”` / `“fire”` | Destroys the closest jet (needs unlocked weapons, else red error flash) |
| `“red”` / `“blue”` | Answer the **painted** color of the word, not the word itself. Right unlocks weapons for 3 s; wrong overheats a random core |
| `“vent one”` / `“vent two”` / `“vent three”` | Instantly cools reactor core 1 / 2 / 3 |
| `“Mac, close game”` | Shuts down the bridge and closes the window |

Keyboard fallback: `A`/`D` = red/blue, `K` kick, `S` shoot, `1`/`2`/`3` vent. Same Zero-Interference Game Mode as the runner: in-game words never trigger macOS actions; saying **"Mac"** hands control back instantly.

> [!NOTE]
> **Zero-Interference Game Mode**: While the runner is active, in-game speech (`left`, `jump`, `faster`, `shoot`, `change world...`) is captured exclusively by the game engine in `<1ms`. `free-mac-voice` will **never** trigger macOS accessibility clicks, window movements, or system actions on game words. Saying **"Mac"** or **"Hey Mac"** instantly handshakes back to macOS.

> [!TIP]
> **Update-Proof Customization**: All spoken shortcuts and custom extensions are saved directly to `~/.config/free-voice/` (outside the git repository). You can pull updates, run `bash upgrade.sh`, or reinstall anytime—your custom phrases, aliases, and shell macros are preserved forever. Developers can also drop custom Python handlers into `~/.config/free-voice/extensions.py` to add arbitrary code.

---

## 🎧 Listening Modes

### 1. Push-to-Talk (Default)
Hold **Right Option ⌥**, speak naturally, and release. Earcon audio confirms keypress and completion. Fast, zero-battery-drain, and perfect for noisy rooms.

### 2. Hands-Free Always-Listening (`--always`)
Run continuously with voice activity detection:
- **Wake Word:** Prepend commands with **"Mac"** (e.g. *"Mac, open Safari"*).
- **Two-Stage Wake Window:** Say *"Mac"*, hear the chime acknowledgment, and you have **15 seconds** (configurable with `VOICE_WAKE_WINDOW`) to speak your command without repeating the wake word.
- **Ambient Noise Rejection:** Room chatter, podcasts, and TV audio that do not trigger the wake word are ignored silently.
- **Double-Click Launcher:** Double-click `Voice Control (Always On).command` anytime.

### 3. Real-Time Streaming Whisper (`--stream`)
Powered by Metal-accelerated `whisper.cpp`:
```bash
python3 free_voice.py --stream
```
Streams audio chunks continuously. The instant a command completes (e.g. *"Mac, open notes"*), Tier 0 executes immediately mid-sentence without waiting for silence or speech-boundary timeouts.

### 4. Native Menu Bar Companion (`menu_bar.py`)
A lightweight status icon in your macOS menu bar reflects live state:
- 🎙️ **Listening** — mic open, waiting for wake word or hotkey
- 👂 **Wake Word Heard** — wake window active (15-second countdown)
- ⚙️ **Working** — executing action, OCR text locate, or model inference
- 💤 **Idle** — paused / standby

Start manually (`python3 menu_bar.py`) or let `install.sh` / `Voice Control (Always On).command` run it automatically.

### 5. Persistent Background Daemon (`service.sh`)
Run completely hands-free as a native macOS `LaunchAgent` without keeping a terminal open:
```bash
# Install and auto-start on macOS login:
./service.sh install

# Check status, PID, and health:
./service.sh status

# Manage lifecycle anytime:
./service.sh {start|stop|restart|logs|uninstall}
```
Supervises `free_voice.py --always` and the `menu_bar.py` status indicator with automated restart on failure.

---

## 🪄 Custom Shortcuts & Extensions (Update-Proof)

Teach your assistant new words, aliases, multi-step macros, or custom Python code that **never get erased or overwritten** when you update the software.

### 1. Teach New Words & Shortcuts by Voice (Zero Coding)
You can teach new phrases naturally without touching a terminal:

- **Create Multi-Action Sequences:**
  - *"Mac, when I say party mode, set volume to 80 and play some jazz"*
  - *"Mac, when I say bedtime, turn off the tv and sleep"*
- **Create Colloquial Aliases:**
  - *"Mac, alias surf to open safari"*
  - *"Mac, when I say chill out, turn down the volume"*
- **Run Local Shell Scripts / Terminal Commands:**
  - *"Mac, when I say backup, run bash ~/backup.sh"*
  - *"Mac, add shortcut clean temp runs rm -rf /tmp/scratch"*
- **Manage Shortcuts by Voice:**
  - *"Mac, what are my shortcuts?"* — Reads all active custom shortcuts aloud
  - *"Mac, forget shortcut party mode"* — Deletes a shortcut

*Saved directly to `~/.config/free-voice/macros.json` — completely outside the git repository.*

### 2. Custom Python Extensions (`extensions.py`)
For power users and developers who want custom Python code, API integrations, or hardware hooks:

1. Drop a script into `~/.config/free-voice/extensions.py` (a starter template is auto-generated at `~/.config/free-voice/extensions.py.example`).
2. Define a `register(add_command)` hook:

```python
# ~/.config/free-voice/extensions.py
# Survives all updates, git pulls, and reinstalls.

def register(add_command):
    def handle_brew_update(match):
        import subprocess
        subprocess.Popen(["brew", "update"])
        print("Homebrew update running!")

    # add_command(regex_pattern, handler_function, partial_ok=False)
    add_command(r"^update homebrew$", handle_brew_update)
```

Whenever the voice engine starts or reloads, it automatically discovers and binds your custom extensions without modifying core code.

---

## 🗣️ High-Definition Neural Voice (Kokoro-82M Default)

`free-mac-voice` defaults to **Kokoro-82M** for spoken feedback, giving you **ElevenLabs-tier natural human speech** running 100% locally on your Mac with zero cloud fees, zero subscriptions, and zero API keys:

- **Audition Voices by Voice:** Say *"Mac, pick a voice"* (or *"sample voices"* / *"choose a voice"*). The assistant rotates through curated personas speaking:
  > *"[Name]. This is what I sound like on your Mac."*
- **Choose Your Voice:** Say *"Mac, use voice Heart"* or *"Mac, set voice to Adam"*. The assistant confirms your choice in that exact voice (default: **Fenrir**, second recommended: **Heart**).
- **Update-Proof Persistence:** Your selected voice is saved directly to `~/.config/free-voice/config.json`, surviving software updates and reinstalls.
- **Instant Fallback:** If offline, running in a minimal container, or if model files are uninitialized, it falls back seamlessly to macOS native `say` without interruption.

#### 🎧 Voice Audition Showcase

> [!NOTE]
> **Why does GitHub say "Preview unavailable"?**  
> GitHub's repository code viewer does not embed an audio player for raw `.wav` or `.mp3` files (showing a *"Preview unavailable"* placeholder).  
> - **🌐 Web Audio Player:** Visit the **[Interactive Web Audio Showcase](https://bparlette.github.io/free-mac-voice/)** to play, pause, and compare all voices with waveforms in your browser.  
> - **▶️ Direct Stream:** Click any of the **[▶️ Stream MP3]** or **[▶️ Stream WAV]** links in the table below (bypasses GitHub's code viewer directly to the audio stream).  
> - **🎙️ Audition on Your Mac:** Say *"Mac, pick a voice"* hands-free, or test locally in terminal: `afplay docs/audio_samples/fenrir_sample.wav`

| Voice | Style & Accent | Sample Quote | Direct Audio Stream |
|:---|:---|:---|:---:|
| **Fenrir** *(Default)* | Cinematic American Male | *"Fenrir. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/fenrir_sample.mp3?raw=true) · [WAV](docs/audio_samples/fenrir_sample.wav?raw=true) |
| **Heart** *(Recommended)* | Warm & Expressive American Female | *"Heart. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/heart_sample.mp3?raw=true) · [WAV](docs/audio_samples/heart_sample.wav?raw=true) |
| **Adam** | Deep & Resonant American Male | *"Adam. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/adam_sample.mp3?raw=true) · [WAV](docs/audio_samples/adam_sample.wav?raw=true) |
| **Sarah** | Clear & Sharp American Female | *"Sarah. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/sarah_sample.mp3?raw=true) · [WAV](docs/audio_samples/sarah_sample.wav?raw=true) |
| **Nicole** | Upbeat & Friendly American Female | *"Nicole. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/nicole_sample.mp3?raw=true) · [WAV](docs/audio_samples/nicole_sample.wav?raw=true) |
| **George** | Refined British Male | *"George. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/george_sample.mp3?raw=true) · [WAV](docs/audio_samples/george_sample.wav?raw=true) |
| **Emma** | Crisp & Articulate British Female | *"Emma. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/emma_sample.mp3?raw=true) · [WAV](docs/audio_samples/emma_sample.wav?raw=true) |
| **Michael** | Dynamic American Male | *"Michael. This is what I sound like on your Mac."* | [▶️ Stream MP3](docs/audio_samples/michael_sample.mp3?raw=true) · [WAV](docs/audio_samples/michael_sample.wav?raw=true) |

---

## 🏎️ The Benchmark: Phonon-2 (Apple MLX) vs. OpenAI Whisper

<p align="center">
  <a href="https://bparlette.github.io/free-mac-voice/benchmark.html">
    <img src="docs/assets/benchmark_card.png" alt="Phonon-2 vs OpenAI Whisper Benchmark - 7.2x Speedup on Apple Silicon" width="100%">
  </a>
</p>

> *"How we made local Mac voice commands 7.2x faster with zero cloud lag, zero silence hallucinations, and a tiny 164 MB footprint."*  
> 🔗 **Shareable Benchmark Link:** [Interactive Benchmark Card](https://bparlette.github.io/free-mac-voice/benchmark.html) · [Direct High-Res Image](https://raw.githubusercontent.com/bparlette/free-mac-voice/main/docs/assets/benchmark_card.png)
>
> 📒 **Every other benchmark we have run, what we changed because of it, and what we ruled out:** see the [Benchmark Ledger](#-benchmark-ledger-weeks-of-testing-in-one-place) below and [`benchmarks/README.md`](benchmarks/README.md).

We benchmarked **OpenAI Whisper `base.en`** against **Phonon-2 (Parakeet-TDT)** head-to-head on Apple Silicon unified memory using real macOS speech across representative desktop voice commands:

### ⚡ Side-by-Side Command Latency

| Voice Command | Spoken Audio Duration | OpenAI Whisper `base.en` (CPU/Torch) | Phonon-2 (Apple MLX Metal) | Real-World Speedup | Accuracy & Behavior |
|:---|:---:|:---:|:---:|:---:|:---|
| **`"Mac close window"`** | 1.11 s | 367.8 ms | **77.5 ms** | **4.7x faster** 🚀 | 100% exact transcription |
| **`"draw an ascii picture of a heart"`** | 1.76 s | 311.8 ms | **34.7 ms** | **9.0x faster** ⚡ | Fast phonetic transcription, matches Tier 0.5 |
| **`"switch to TV"`** | 1.02 s | 264.1 ms | **25.8 ms** | **10.2x faster** ⚡ | 100% exact transcription |
| **`"could you please open notes for me"`** | 1.86 s | 307.8 ms | **32.6 ms** | **9.4x faster** ⚡ | 100% exact transcription |
| **`"type password123"`** | 1.96 s | 264.1 ms | **40.9 ms** | **6.5x faster** 🚀 | Clean number/word recognition |
| **Average End-to-End Latency** | — | **303.1 ms** | **42.3 ms** | **7.2x faster** | **Zero hallucinations on silence** |

---

### 🔍 Why Phonon-2 Crushes Traditional Whisper on macOS

1. **Non-Autoregressive Transducer (TDT vs. Encoder-Decoder):**  
   Whisper is an autoregressive sequence-to-sequence model that generates text token-by-token. In contrast, Phonon-2 is based on NVIDIA's **Parakeet Token-and-Duration Transducer (TDT)**, predicting tokens and frame durations simultaneously in parallel.
2. **Zero Silence Hallucination:**  
   Whisper often hallucinates phantom words or infinite repetition loops when listening to microphone background noise or room silence. Phonon-2 emits tokens only when speech phonemes exist—transcribing 1.0s of silence takes **0.0 ms** and outputs an exact empty string `""`.
3. **164 MB Footprint with Large-v3 Quality:**  
   Through advanced ternary/2.1-bit quantization, Phonon-2 requires only a **164 MB download**, yet achieves an average **5.2% Word Error Rate (WER)** across standard English benchmarks—matching OpenAI's 1.5 GB Whisper Large-v3 (a model 10x its size).
4. **Native Apple Silicon MLX GPU/Neural Acceleration:**  
   Executes directly on unified memory via Apple's MLX engine (`mlx` + Metal shaders), completely eliminating PyTorch/CPU bus bottlenecks.
5. **Pluggable & Graceful Fallback:**  
   Configurable via `VOICE_STT_ENGINE=phonon` (default on Apple Silicon) with transparent fallback to Whisper if running on non-Apple-Silicon systems or minimal containers.

---

## 📒 Benchmark Ledger: weeks of testing, in one place

We benchmark everything before changing the assistant, and write down what we tried, what we changed, and what we ruled out, including the
dead ends. This section is **generated from [`benchmarks/README.md`](benchmarks/README.md)** (the full ledger, with the saved raw results next to each
benchmark in `benchmarks/*/results/`); run `python benchmarks/update_ledger.py` after adding results and it refreshes both. Test sets are synthetic and
numbers are only comparable inside one test set; see the ledger for the caveats.

### What changed in the assistant because of the benchmarks

<!-- AUTO:ledger-live:START -->
| Change | Date | Why |
|---|---|---|
| Tier 0.5a embeddings run **in-process with ONNX** by default (`TIER05_EMBED_BACKEND`, falls back to Ollama if the model is missing) | 2026-10-07 | Same accuracy as the Ollama copy at about 6 ms and 0.1 GB instead of 10-16 ms and about 650 MB |
| Tier 0.5a threshold 0.82 | 2026-10-06 | Better recall and fewer false accepts than 0.78 on the then-current test |
| Speaker verification removed | 2026-10-08 | Wake word makes it unnecessary for this setup (the model was only a weak second filter) |
| App-name lookup handles polite phrasing ("can you launch chrome please") | 2026-10-08 | Tier 0.5a matched the intent but dropped it for lack of an app name |
| Ollama upgraded 0.35 -> 0.40 | 2026-10-07 | Identical accuracy; embeddings through Ollama faster; command-gate endpoint compatible |
| **Tier 0.5b (decision-model router) switched off** on the live setup (`OLLAMA_DECISION_MODEL=` empty in the local config; the code default is unchanged) | 2026-10-08 | End to end: false triggers in the wake window 33 -> 21 of 80, correct commands up |
| **Tier 1 / vision / answers model changed to `qwen3-vl:8b-instruct`** on the live setup (`OLLAMA_MODEL`); the code now pre-fills the answer start **only for thinking builds** | 2026-10-08 | Routing 51/60 vs 52/60 for the thinking build, with fewer false accepts (7 vs 9); general answers 12 s -> 0.5 s; screen questions 9.3 s -> 4.9 s; no silent replies |
| **Tier 0 regex tightened** (app-name slot must resolve; clause-after-comma guard; stricter generic `search` / `play` / `hit` rules) | 2026-10-08 | Tier 0 false triggers 43 -> 3 of 400 non-commands; wake-window false triggers 21 -> 13 of 80 with correct commands unchanged |
| Command-gate model kept resident (`keep_alive`) and warmed at startup | 2026-10-08 | A cold gate call took about 2.7 s against the 3 s timeout, and a timeout fails closed (drops the phrase) |

**Still open:** the code's *default* model is still the thinking `qwen3-vl:8b` (the installer pulls it), so making an instruct build the default for everyone is a separate decision. See [recommendations](benchmarks/README.md#recommendations).
<!-- AUTO:ledger-live:END -->

### Where things stand

<!-- AUTO:ledger-standings:START -->
### A. Tier 0.5a embedding tier and small routers (held-out half of the intent set: 204 commands / 200 non-commands)

| Approach | Correct command | Non-commands acted on | Size / speed |
|---|---|---|---|
| Router as it was deployed (examples included 75 test phrases: inflated) | 73.5% | 17.0% | n/a |
| **Router, leak-free** (Ollama EmbeddingGemma v1, threshold 0.82, regex tier first) | **61.8%** | **16.0%** | about 650 MB in Ollama; 10 ms |
| **ONNX q4 EmbeddingGemma v1, in-process (now the default)** | 62.3% (63.2% with Google's task prefix) | at the same 16% | **+0.1 GB; 6 ms** |
| EmbeddingGemma 2 (Ollama 270m / ONNX q4) | 54.9% / 57.8% | at the same 16% | 330 MB / +0.25 GB: worse, not adopted |
| Fine-tuned Qwen2.5-0.5B, trained on the router's own examples + hand-written negatives | 65.7% (conf >= 0.8) | 9.5% | +0.4 GB; 45 ms |
| Same, trained on about 2.4x as much in-distribution data | **81.4%** (conf >= 0.7) | **5.0%** | same: data is the lever |
| Laya decision model, zero-shot | 52.0% | 63.5% | 2.7 GB; 325 ms: dropped |

### B. The local LLM (Tier 1 routing, screen questions, answers): Qwen3-VL and Qwen3.5 sizes

Same 60 commands / 60 non-commands (held-out), 12 screen questions, 10 general questions, using the assistant's own functions. "Routing, current code" uses
the assistant's JSON-output request, which Ollama 0.40 refuses for Qwen3.5 (so those rows show 0). Routing without the code's pre-filled answer start, and the
other routers, are in the notes below the table.

| Model | Resident | Speed | Routing, assistant's current code (of commands) | Non-commands acted on | Screen questions | General questions |
|---|---|---|---|---|---|---|
| `qwen3-vl:8b` | 5.17 GB | 16.3 tok/s | 52/60 right, 3 no answer (1.39 s) | 9/60 | 12/12 (9.3 s) | 8/10 (12.1 s, 1 silent) |
| `qwen3-vl:8b-instruct` | 5.17 GB | 15.8 tok/s | 47/60 right, 9 no answer (1.41 s) | 7/60 | 12/12 (4.9 s) | 9/10 (0.5 s) |
| `qwen3-vl:4b-instruct` | 3.02 GB | 27.4 tok/s | 9/60 right, 50 no answer (0.17 s) | 0/60 | 12/12 (4.1 s) | 9/10 (0.3 s) |
| `qwen3-vl:4b` | 3.02 GB | 29.6 tok/s | 9/60 right, 48 no answer (0.17 s) | 1/60 | 11/12 (5.6 s) | 7/10 (8.0 s, 3 silent) |
| `qwen3-vl:2b-instruct` | 1.64 GB | 53.7 tok/s | 12/60 right, 47 no answer (0.1 s) | 1/60 | 12/12 (1.5 s) | 9/10 (0.1 s) |
| `qwen3.5:2b` | 3.0 GB | 32.8 tok/s | 0/60 right, 60 no answer (0.0 s) | 0/60 | 12/12 (2.8 s) | 8/10 (0.5 s) |
| `qwen3.5:0.8b` | 1.29 GB | 68.7 tok/s | 0/60 right, 60 no answer (0.0 s) | 0/60 | 10/12 (1.4 s) | 7/10 (0.1 s) |

Notes (from `model_size_eval/README.md`):
- The default tags `qwen3-vl:8b` / `:4b` are **thinking** builds: general answers take about 12 s on the 8B and the answer can come back **empty** (the model spends its
  token budget reasoning). **Instruct** builds answer in 0.3-0.5 s.
- With the pre-fill skipped, **4B instruct routes 51/60 (15 false accepts)**; the same pre-fill that helps thinking models collapses it to 9/60.
- **8B instruct without the pre-fill routes 51/60 with 7 false accepts** (1.35 s), matching the 8B thinking build's 52/60 with 9; with the pre-fill it scores 47/60. A harder screen/question set was **not run**.
- Cold start (model unloaded): 8B 6.6 s, 4B instruct 9.6 s.
- Qwen3.5 0.8B / 2B without `format=json`: routing 35 / 45 of 60 (with 20 / 11-15 false accepts).

### C. The whole pipeline, end to end (dry-run on real code; 80 commands + 80 adversarial non-commands)

In the real pipeline the command gate only guards the **wake window** (follow-up speech) and only for phrases the regex tier does not match.

| | Current pipeline | Tier 0.5b disabled | + 8B instruct | + Tier 0 regex tightened (live setup) |
|---|---|---|---|---|
| After wake word: commands routed to an acceptable action | 53/80 | 55/80 | 55/80 | 55/80 |
| Wake window: commands correct | 37/80 | 39/80 | 39/80 | 39/80 |
| Wake window: wrong action | 20 | 17 | 17 | 16 |
| **Wake window: non-commands wrongly acted on** | **33/80** | **22/80** | **21/80** | **13/80** |
| ...by tier | {"Tier 0.5b": 12, "Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 3} | {"Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 4} | {"Tier 0.5a": 6, "Tier 0": 12, "Tier 1": 3} | {"Tier 0.5a": 7, "Tier 1": 6} |

### D. Voice output (TTS): Kokoro vs Paradee (`tts_eval/`)

| | Speed | Short phrase | Process memory | Word error (Whisper round trip) |
|---|---|---|---|---|
| Kokoro (the assistant's voice) | 4.3x real time | 450 ms | 0.66 GB | 4.7% |
| Paradee int8 (one female voice) | 17.7x | 131 ms | 0.54 GB (its phonemizer takes 0.37 GB) | 3.5% |

Not adopted: memory barely improves, the voice changes, and it needs Python 3.12 (spaCy has no build for 3.14). Subjective quality was not judged.

### E. Other measurements (not in a results folder)

- Display refresh 120 Hz -> 60 Hz on the TV: macOS compositor CPU 43.8% -> 41.3%, so refresh rate was not the main cost (the display runs a 4K-backed mode; idle load stayed near 38%).
- Ollama 0.35 -> 0.40: embeddings 16 -> 10 ms, generation speed unchanged (61 tok/s on qwen2.5:1.5b); the 0.40 server re-saved every model in a new format and **kept the old copies** (store grew 8.9 -> 35 GB until test downloads were removed).

### F. Earlier router bake-off, 2026-10-03: decision models vs qwen2.5:1.5b (`docs/router-benchmark.md`; 8 choices, 369 hand-written phrases)

A different test set from the tables above (numbers are not comparable across sets). "False triggers" = non-commands turned into actions; "missed" = commands answered `none`.

| Model | Accuracy | False triggers | Missed commands | Warm median | Size |
|---|---|---|---|---|---|
| **Kev-4B** (decision model) | **81.6%** | **9.2%** | 25.7% | 659 ms | 4.5 GB |
| lev | 79.4% | 26.3% | 3.9% | 1,505 ms | 4.5 GB |
| tev1:0.8b | 70.2% | 35.9% | 11.2% | 177 ms | 0.8 GB |
| qwen2.5:1.5b (then the router) | 67.8% | 43.3% | 4.6% | 336 ms | 1.0 GB |
| Julia-1 | 64.5% | 2.8% | 79.6% | 13 ms | 0.2 GB |
| Laya | 58.8% | 0.0% | 100% | 75 ms | 0.4 GB |

With 40 options: Kev-4B 83.0% (but 2.7 s per decision), qwen2.5:1.5b 68.6%, `tev1:0.8b` refuses more than 26 options. Takeaway then and now: decision models are good at saying "no"
(fewer false triggers) but slow down and get worse as the option list grows; Julia-1 and Laya are better as yes/no gates than as routers.

### G. Embedding backends and engines (`finetune_eval/results/`)

Correct command at about 16% false accepts (the router's own level), leak-free, Tier 0 first; held-out half of the intent set.

| Backend | No prefix / with task prefix | Single query | Memory | Notes |
|---|---|---|---|---|
| Ollama 0.35.0, EmbeddingGemma v1 | 61.8% | 16 ms (69 texts/s) | 648 MB loaded | the old live path |
| Ollama 0.40.0, EmbeddingGemma v1 | 61.8% | 10 ms (107 texts/s) | 648 MB loaded | generation speed unchanged (61 tok/s) |
| Ollama 0.40.0, EmbeddingGemma 2 270m | 54.9% / 52.0% | 15 ms | 330 MB | worse |
| **ONNX v1 q4, CPU (live default)** | **62.3% / 63.2%** | **6 ms** | **+0.10 GB** | 188 MB of weights |
| ONNX v1 q4f16, CPU | 61.8% / 63.7% | 8 ms | +0.15 GB | |
| ONNX v1 int8, CPU | 61.8% / 61.8% | 43 ms | +1.15 GB | slow and large |
| ONNX v2 q4 / q4f16 / fp16, CPU | 57.8% / 57.8% / 54.9% | 14 / 15 / 12 ms | +0.25 / +0.25 / +0.40 GB | worse than v1 |
| ONNX on CoreML (v1 q4f16 / v1 q4 / v1 int8 / all v2) | 61.8% / **0%** / 22% / fails | 13 ms / 131 ms / 270 ms | | CoreML provider unusable except q4f16 (slower than CPU) |

### H. Speech recognition: Phonon-2 vs Whisper

Covered by the benchmark section above in this README (`benchmarks/bench.py`, `results.jsonl`): Phonon-2 (Apple MLX) is about 7.2x faster than Whisper `base.en` on Apple silicon.

### I. Harder questions, 4B pipeline and wake word (2026-10-08)

**Hard questions** (`model_size_eval/bench_hard.py`; 10 screen questions on a dense task page, 10 reasoning questions):

| Model | Screen questions right | Reasoning right | Silent empty replies | Median reply (screen / reasoning) |
|---|---|---|---|---|
| qwen3-vl:2b-instruct | 9/10 | 6/10 | 0 | 1.3 s / 0.2 s |
| qwen3-vl:4b-instruct | 9/10 | **8/10** | 0 | 1.3 s / 0.4 s |
| qwen3-vl:8b-instruct (live) | 9/10 | 7/10 | 0 | 4.4 s / 0.6 s |
| qwen3-vl:8b (thinking) | 9/10 | 3/10 | 6 | 10.1 s / 16.3 s |

The 4B instruct build is at least as good as the 8B instruct build on these questions. The thinking 8B fails reasoning mostly by returning nothing. Samples are small (10 each): differences of one or two questions are noise.

**Full pipeline with 4B instruct as Tier 1** (`pipeline_eval/results/pipeline_end_to_end_tier0_tight_4b_instruct.json`): after the wake word 55/80 commands got an acceptable action (22 wrong, 3 not handled); in the wake window 40/80.

**Wake word** (`wake_eval/bench_wake.py`; Kokoro speech in 6 voices through the real recognizer, so indicative only):

| Parser | "Mac, <command>" recognised | TV-style lines that start with a name or verb (60 clips) woken | Non-command speech woken (360 clips) |
|---|---|---|---|
| Before 2026-10-08 (10 spellings, any sentence) | 100% | 56 | 3 |
| Now (command-aware: mac/mack/macs always; max/matt/mark/match/make/mike/mock only when a real command follows) | 98.6% | **16** | 3 |
| Strict (mac/mack/macs only) | 97.8% | 0 | 3 |

`VOICE_WAKE_LOOSE=always|command|off` selects the behaviour (default `command`).

### J. Backlog benches run without a microphone (2026-10-08)

**Command gate on its own** (`model_size_eval/bench_gate.py`; held-out half: 204 commands / 200 non-commands; "accepted" = P(command) >= 0.5):

| Gate model | Commands accepted | Non-commands accepted (false accepts) | Median latency | Size |
|---|---|---|---|---|
| `tev1:0.8b` (live) | 42.6% | 24.5% | 129 ms | 0.8 GB |
| **`tev1:4b`** | **83.8%** | **10.5%** | 419 ms | 4.4 GB |
| Kev-4B (Q4_K_M, llama-server 0.6.0) | 79.9% | 14.5% | 336 ms | 3.0 GB |

**Pipeline with the gate swapped** (4B instruct as Tier 1, Tier 0.5b off, 80 commands + 80 non-commands; the wake-window rows are the ones the gate affects):

| Setup | After wake word: correct | Wake window: correct | Wake window: false triggers |
|---|---|---|---|
| gate `tev1:0.8b` (live) | 55/80 | 40/80 | 13/80 |
| **gate `tev1:4b`** | 55/80 | **52/80** | **9/80** |
| gate `tev1:4b` + Tier 0.5b `tev1:4b` | 54/80 | 51/80 | 12/80 |

Non-commands that the gate rejects cost about 0.55 s median. `tev1:4b` as the Tier 0.5b decision model adds nothing, so that tier stays off. As a re-ranker behind the embedding tier (60 commands / 60 non-commands, shortlist of 8, `bench_decision_router.py`), `tev1:4b` gets 47/60 correct with 9/60 false triggers at P >= 0.5 (727 ms), versus 29/60 and 25/60 for `tev1:0.8b` and 25/60 and 6/60 for Kev-4B.

**Not runnable here:** Liquid `d1-omni-600M` and `d1-3B` (GGUF files pull into Ollama 0.40 but it rejects their decision type; the installed llama.cpp 0.6.0 says "unsupported decision model type: lfm2-d1"; llama.cpp's development branch has it, a local build was made but not run) and Amazon Strands Decider 2B (published as a LoRA adapter plus a head on a Qwen3.5 base, with no GGUF or Ollama build). Both remain untested.

**TTS: wait before the first word** (`tts_eval/bench_ttfa.py`, Kokoro af_heart, no playback; macOS `say` could not be timed while the audio daemon is hung). The assistant synthesizes the whole reply before playing it:

| Reply length | Synthesize whole reply | Synthesize first sentence only |
|---|---|---|
| 6 words | 0.44 s | 0.43 s |
| 20 words | 1.35 s | 1.20 s |
| 50 words | 3.62 s | 1.18 s |
| 100 words | 6.91 s | 1.15 s |
| 200 words | 13.4 s | 1.14 s |

Speaking sentence by sentence keeps the wait near 1.2 s for any length. **Implemented 2026-10-08** (`VOICE_TTS_STREAM`, replies of 160+ characters with more than one sentence).

**Replay of logged utterances** (`finetune_eval/replay_logged_utterances.py`): only 59 unique real utterances were in the log (most rows are ambient). At the 0.82 threshold both embedding backends accepted 15 (14 of them the same ones; 13 of those with the same action) and agreed on the top action for 51 of 59. Mean cosine 0.754 (ONNX) vs 0.751 (Ollama). Small sample, but no sign the ONNX default behaves differently on real speech-to-text output.

### K. New options researched 2026-10-08: embedding classifier, Apple's on-device model, newer speech recognizers

**Tier 0.5a as a trained classifier** (`finetune_eval/bench_classifier.py`; same ONNX embeddings, held-out half, Tier 0 first, best result with <=16% of non-commands acted on):

| Embedding tier | Commands correct | Non-commands acted on |
|---|---|---|
| Nearest example (live) | 70.1% | 16.0% |
| **Softmax classifier on the same examples + 112 negatives** | **76.5%** | 15.5% |
| Classifier + the other half of the intent set as extra training data | 83.8% | 8.0% |

Training takes 0.1 s with numpy. The third row uses synthetic test-style data, so it is optimistic for real speech, but it shows that more labelled phrases are the lever.

**Apple's on-device Foundation Model** (macOS 26.3 build; `model_size_eval/bench_apple_fm.py`, guided generation into a fixed answer list, greedy):

| Use | Zero-shot | With 40 labelled examples in the instructions |
|---|---|---|
| Command gate: commands / non-commands accepted | 93.6% / 91.5% | 12.3% / 3.5% |
| Router after Tier 0: correct / non-commands acted on | 67.2% / 78.0% | 52.0% / 18.5% |
| Median latency (gate / router) | 191 / 644 ms | 339 / 842 ms |

Not usable: it either accepts nearly everything or rejects nearly everything. The rebuilt model in macOS 27 was not tested (this Mac runs 26.3).

**Speech recognizers** (`asr_eval/bench_asr.py`; 80 held-out commands x 4 Kokoro voices = 320 clips; synthetic, clean speech):

| Recognizer | Word error rate | Exact transcripts | Router picks an acceptable action | Median per clip |
|---|---|---|---|---|
| Whisper base.en (live) | 5.0% | 279/320 | 197/320 | 156 ms |
| **Apple SpeechAnalyzer** (built into macOS 26) | **3.1%** | **290/320** | **202/320** | **102 ms** |
| Parakeet TDT 0.6B v3 (parakeet-mlx) | 4.6% | 280/320 | 191/320 | 179 ms |

The router gets 220/320 right on the exact reference text, so on clean speech the recognizer costs at most 18-29 commands while the router itself loses 100: the router, not the recognizer, is the bottleneck. Many counted "errors" are spelling (maximise / maximize, T V). Real voice in a room with a TV is untested.

### L. Phone-controlled orchestrator: tools checked 2026-10-08 (researched, not measured yet)

Design: [`docs/orchestrator-design.md`](../docs/orchestrator-design.md). One hub (Claude Code in Remote Control server mode) takes tasks from the phone and hands them to workers.

| Tool | Phone control | Can the hub drive it? | Finding |
|---|---|---|---|
| Claude Code | Remote Control (`claude remote-control`, already starts at login) | It is the hub | Outbound only; `--spawn worktree` isolates sessions; today it runs in this repo with the shared `same-dir` mode, which should change |
| Antigravity | Remote Control (since 2026-08-21), joins sessions open on the Mac | **Yes, locally**: `agentapi new-conversation / send-message / get-conversation-metadata` ships with the app; `agy -p` CLI not installed | Best second worker for code |
| Meta Muse (muse.ai in Chrome) | Own app; Sentinel approves outgoing actions | Browser only (no public API for individuals; Muse API announced for businesses) | Fragile: depends on page layout |
| Hark Handoff (web) | Own web app | Browser only; research preview / waitlist | Access for this account not confirmed |

To measure once it runs: phone message to first reply, tasks finished without help per worker, approvals per task, tasks that touched the wrong folder (must be 0).

### M. Trained classifier in the live Tier 0.5a (2026-10-08, applied)

Pipeline run (4B instruct as Tier 1, `tev1:4b` gate, Tier 0.5b off; 80 commands + 80 non-commands):

| Tier 0.5a | After wake word: correct | Wake window: correct | Wake window: false triggers |
|---|---|---|---|
| Nearest example (before) | 55/80 | 52/80 | 9/80 |
| Classifier, confidence >= 0.4 | 61/80 | 58/80 | 12/80 |
| **Classifier, confidence >= 0.5 (live)** | 61/80 | 58/80 | **10/80** |
| Classifier, confidence >= 0.6 | 61/80 | 58/80 | 10/80 |

Live defaults: `TIER05_CLASSIFIER=1`, `TIER05_CLASSIFIER_MIN_CONF=0.5`, `TIER05_CLASSIFIER_MIN_COS=0.7` (also needs a close example). The classifier trains at startup in under a second from the router's examples plus `tier05_negatives.txt`; with `TIER05_CLASSIFIER=0` or the hashing fallback the old nearest-example matching is used.

### N. Cloning a voice for the assistant: Qwen3-TTS (2026-10-08, benchmarked, not wired in)

Source: a post about Qwen's open TTS family (Apache 2.0, [paper](https://arxiv.org/abs/2601.15621)). The model linked in the post, `Qwen3-TTS-12Hz-1.7B-CustomVoice`, only has 9 preset voices and needs a CUDA GPU; **cloning is the Base variant** (3-second reference clip). Community MLX conversions run on Apple silicon through `mlx-audio` (`benchmarks/tts_eval/bench_qwen_tts.py`, runs in a throwaway `uv` environment).

| Voice engine | Speaks a 5.4 s reply in | Real-time factor | Memory | Heard correctly by Whisper | Voice |
|---|---|---|---|---|---|
| Kokoro (live) | about 1.2 s (first sentence) | 0.2 | about 0.3 GB | yes | 50 stock voices |
| **Qwen3-TTS 0.6B Base, 4-bit (MLX)** | 4.5 s (19 words) | 0.77-1.21 | 2.3 GB | **0% word errors** (3 clips) | **cloned from a short clip** |

Caveats: the reference clip here was a synthetic voice, so how closely it copies a real person was **not** measured; the first model load took 207 s (download); a 4-word reply took 1.7 s, so sentence-by-sentence playback (already live) would be needed, and a reply still starts about 2 s later than with Kokoro. Using someone's real voice needs their consent. The assistant would need a small separate local service for it (the model needs Python 3.12 + MLX, the assistant's venv is 3.14).
<!-- AUTO:ledger-standings:END -->

<details>
<summary><b>Timeline: everything we tried, with dates</b></summary>

<!-- AUTO:ledger-timeline:START -->
| Date | What | Result | Where |
|---|---|---|---|
| 2026-10-03 | Router bake-off: qwen2.5:1.5b vs decision models (Kev-4B, lev, tev1:0.8b, Julia-1, Laya); 8-option and 40-option tests | Kev-4B best: 81.6% accuracy, 9.2% false triggers, 0.66 s, 4.7 GB. tev1:0.8b 70.2% / 35.9% (max 26 options). qwen2.5:1.5b 67.8% / 43.3%. Laya, Julia-1 too cautious as routers; fine as yes/no gates | `docs/router-benchmark.md` |
| 2026-10-06 | Embedding bake-off: n-gram hash vs nomic-embed-text vs EmbeddingGemma v1 vs v2 (small hand-written set) | EmbeddingGemma v1 chosen | `embed_bakeoff/` |
| 2026-10-06 | Intent-eval harness (800 phrases); examples expanded; threshold 0.78 -> 0.82 | Reported 77.5% recall / 6.5% false accepts. **Later corrected**: it counted any match, and 75 test phrases were in the examples | `intent_eval/` |
| 2026-10-06 | Speaker ID: legacy model (same-speaker 0.26 vs other 0.0) -> WeSpeaker CAM++ (0.48 vs 0.03) | Better margin; feature **removed 2026-10-08** | (history) |
| 2026-10-07 | Fine-tune bake-off: router vs fine-tuned Qwen 0.5B (MLX LoRA) vs Laya zero-shot vs EmbeddingGemma 2 | Fair router baseline 61.8% / 16%; fine-tune beats it (65.7% / 9.5%, or 81.4% / 5.0% with more data); Laya dropped. Three failed first attempts documented (eval bug, divergence, collapse) | `finetune_eval/` |
| 2026-10-07 | ONNX vs Ollama embeddings; Ollama 0.35 vs 0.40; ONNX variants and CoreML | ONNX q4 matches accuracy at 6 ms / 0.1 GB: **made the default**. CoreML provider broken. Int8 slow | `finetune_eval/results/` |
| 2026-10-07 | TTS: Kokoro vs Paradee | Faster, equally intelligible, not worth switching | `tts_eval/` |
| 2026-10-08 | Qwen3-VL 8B (thinking) vs 4B (thinking) vs 4B / 8B instruct | Thinking builds are slow and sometimes silent; instruct builds fix it; 4B instruct routes like the 8B once the pre-fill is skipped | `model_size_eval/` |
| 2026-10-08 | End-to-end pipeline dry-run, with and without Tier 0.5b | 33 -> 22 of 80 false triggers without 0.5b | `pipeline_eval/` |
| 2026-10-08 | `tev1:0.8b` as action router (embedding shortlist + decision model) | Worse than embeddings alone; the API takes 2-26 options | `model_size_eval/` |
| 2026-10-08 | Tier 0 regex tightening (simulated on the data first, then applied) | 43 -> 3 false triggers on 400 non-commands; 144 -> 119 real commands matched at Tier 0 (the other 25 were being mishandled there). End to end: wake-window false triggers 21 -> 13 of 80, correct commands unchanged | `pipeline_eval/`, `intent_eval/` |
| 2026-10-08 | 8B instruct with / without the pre-filled answer start; pipeline with the planned live setup | 51/60 without (47/60 with); pipeline false triggers 21/80. **Applied to the live setup** (Tier 0.5b off, 8B instruct) | `model_size_eval/`, `pipeline_eval/` |
| 2026-10-08 | Embedding classifier vs nearest example; Apple on-device model as gate/router; Whisper vs Apple SpeechAnalyzer vs Parakeet | Classifier 76.5% vs 70.1% correct at the same false accepts; Apple model unusable on macOS 26; SpeechAnalyzer lowest error (3.1%) and fastest; router, not recognizer, is the bottleneck | `finetune_eval/`, `model_size_eval/`, `asr_eval/` |
| 2026-10-08 | Qwen3.5 0.8B / 2B and qwen3-vl 2B instruct | 2B instruct: 1.6 GB, 12/12 screen questions in 1.5 s, weak router. Qwen3.5 refused by Ollama's JSON mode | `model_size_eval/` |
<!-- AUTO:ledger-timeline:END -->

</details>

<details>
<summary><b>Where every raw result lives (folder index)</b></summary>

<!-- AUTO:ledger-index:START -->
| Folder | What it holds | Test set |
|---|---|---|
| `embed_bakeoff/` | n-gram hash vs nomic vs EmbeddingGemma | 77 positives / 30 negatives (hand-written) |
| `intent_eval/` | Router recall and false accepts (`run.py`); `testset.jsonl` | 400 commands + 400 adversarial non-commands, 17 intents |
| `finetune_eval/` | Fine-tuned small models, Laya, ONNX/Ollama embedding and engine benchmarks, "not in the bake-off" record | held-out half of `intent_eval/testset.jsonl` |
| `model_size_eval/` | Qwen3-VL / Qwen3.5 sizes, decision-model router | 60 + 60 held-out phrases, 12 screen questions, 10 general questions |
| `pipeline_eval/` | End-to-end dry-run of the real pipeline | 80 + 80 held-out phrases |
| `tts_eval/` | Kokoro vs Paradee | 10 typical assistant replies |
| `bench.py`, `results.jsonl` | Earlier component timings (speech recognition etc.) | n/a |
| `../docs/router-benchmark.md` | 2026-10-03 router vs decision-model bake-off | 369 + 264 hand-written phrases |
<!-- AUTO:ledger-index:END -->

</details>

<details>
<summary><b>Tried and dropped, or ruled out without running</b></summary>

<!-- AUTO:ledger-dropped:START -->
| Item | Why |
|---|---|
| Laya (zero-shot), `laya-mlx` | Weak as a router (52% / 63.5% false accepts) and 2.7 GB; the MLX build is a one-person conversion |
| EmbeddingGemma 2 (Ollama, ONNX), ONNX int8, ONNX on the CoreML provider | Less accurate than v1, slow, or broken |
| Paradee TTS | See section D |
| Unsloth Studio install, Unsloth decision-model fine-tuning | System-level install with no clear Apple-silicon training path; MLX LoRA used instead |
| OpenAI Decisions API (cloud) | Would send speech text off the device |
| Qwen3.8-27B variants (DFlash2, ANEMLL Neural Engine package, abliterated build), Underdog Saluki 27B | Too large for 16 GB next to the rest, mostly NVIDIA-only, or unsafe |
| Cloudflare Clef-flash 9B, Nimble 9B | Need roughly 41 GB of memory or more |
| Gemma 4 E2B | 7 GB download despite the name |
<!-- AUTO:ledger-dropped:END -->

</details>

<details>
<summary><b>What we will benchmark next (priority order)</b></summary>

<!-- AUTO:ledger-backlog:START -->
1. Grow `tier05_negatives.txt` and the example phrases from real misses (section M: the classifier gets better with every labelled phrase).
2. Apple SpeechAnalyzer as the recognizer (section K: lower error and faster than Whisper base.en on synthetic speech); test on real voice first.
3. Liquid d1 models and Amazon Strands Decider 2B: need a decision-capable runtime (llama.cpp development build) or the Strands Python stack.
4. Real-audio false-wake test (hours of TV and podcasts) and the live ONNX-vs-Ollama comparison on real speech (`TIER05_EMBED_BACKEND=compare`); both need a working microphone.
5. Compositor load at true idle: plain 1080p vs 4K-backed mode, wallpaper, Safari content (needs display changes and your OK).
6. Fine-tune a decision head on the assistant's own labeled utterances (data was the biggest lever).
<!-- AUTO:ledger-backlog:END -->

</details>

---

## 📊 End-to-End Latency by Tier

Measured on Apple Silicon unified memory:

| Tier | Routing Path | Median Latency (\(p50\)) | Mechanism |
|---|---|---|---|
| **ASR** | **Phonon-2 Speech-to-Text** | **25 ms – 42 ms** | **Apple MLX Parakeet-TDT (164 MB)** |
| **Tier 0** | Regex Reflex | **0.3 ms** | In-memory compiled regex table |
| **Tier 0** | Native Window Close | **2.1 ms** | macOS Accessibility `AXCloseButton` |
| **Tier 0** | Apple Vision OCR (Cached) | **60.1 ms** | Native macOS `VNRecognizeTextRequest` |
| **Tier 0.5** | **Semantic Intent Match** | **2.2 ms** | In-process cosine embedding similarity ($cos > 0.75$) |
| **Tier 0.5** | **Decision Model Slot Extract** | **42 ms – 68 ms** | Local `qwen2.5:1.5b` JSON mode classification |
| **Tier 1** | Local VLM (`qwen3-vl:8b`) | **1.26 s** | Autoregressive JSON decoding (`num_ctx=1024`) |
| **Tier 1** | Fast SVG Generation (`qwen2.5:1.5b`) | **1.82 s** | Lightweight text model generation |
| **Tier 2** | Gemini Free-Tier Grounding | **2.10 s** | Cloud Search + generative answer |

---

## 🔒 Privacy & Local by Default

- **Zero Audio Uploads:** Audio never leaves your Mac. Whisper and Ollama run 100% on your local GPU.
- **No Analytics / Telemetry:** No tracking, no user profiling, no phone-home pings.
- **Optional Cloud Expansion:** If you choose to add a free Gemini API key to `~/.free-voice/.env` for world trivia, web searches route through Google's free tier. If unset, the system runs completely offline.

---

## 📺 Samsung TV Setup (Optional)

Control Samsung Smart TV power, inputs, volume, and playback over Wi-Fi:

1. **SmartThings app:** Add your Samsung TV in the mobile SmartThings app on your Wi-Fi.
2. **Personal Access Token:** Go to [account.smartthings.com](https://account.smartthings.com) → *Personal Access Tokens* → Generate token with `r:devices:*` and `x:devices:*` scopes.
3. **Configure Environment:** Add to `~/.free-voice/.env`:
   ```bash
   SAMSUNG_ST_TOKEN=your-token-here
   SAMSUNG_TV_DEVICE_ID=your-tv-device-id
   ```
4. **Discover:** Run `python3 samsung_tv.py discover` to list devices and confirm input port aliases.

---

## 🧪 Bulletproof Testing

The test suite stubs all hardware (no mic, TV, or live Ollama instance required) for instant verification:

```bash
# Run the full unit-test suite (420 tests, about 4-5 minutes):
python3 -m unittest discover -s tests

# Run performance benchmarks:
python3 benchmarks/bench.py --quick
```

---

## 💻 Requirements

- **Apple Silicon Mac:** M1, M2, M3, or M4 (8 GB RAM minimum; 16 GB recommended for `qwen3-vl:8b`).
- **Microphone:** Built-in MacBook mic, USB mic, or iPhone Continuity microphone (Mac mini has no internal mic).
- **macOS Permissions:** Microphone, Accessibility, Input Monitoring (+ Screen Recording for UI button locator). The installer guides you through native prompts in ~60 seconds.

---

## 📄 License

MIT License — free for personal and commercial use.
