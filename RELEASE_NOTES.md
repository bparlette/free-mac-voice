# Release Notes — v2.1.0: High-Definition Neural Speech & Background Daemon

`v2.1.0` is a major upgrade to `free-mac-voice`, introducing **Kokoro-82M** as the default high-definition neural voice engine, persistent background daemon management via native macOS `LaunchAgent`, full couch/Apple TV media controls, and update-proof conversational shortcuts.

---

### 🌟 Key Highlights

#### 1. 🗣️ High-Definition Neural Voice (Kokoro-82M Default)
- **ElevenLabs Quality at $0:** Defaults to an 82M-parameter StyleTTS2 neural model running locally on Apple Silicon unified memory. Produces natural, human-grade voice inflection with zero cloud fees, zero subscriptions, and zero API keys.
- **Voice Auditioning by Voice:** Say *"Mac, pick a voice"* (or *"sample voices"*) to hear the assistant rotate through distinct human personas, each speaking a personalized sample sentence starting with their name.
- **Voice Selection & Switching:** Say *"Mac, use voice Heart"* or *"Mac, set voice to Adam"* to switch voices (defaults to **Fenrir**, second recommended: **Heart**). The assistant confirms your choice in that exact voice.
- **Update-Proof Persistence:** Preferences are saved in `~/.config/free-voice/config.json` outside the Git repository.
- **Instant Fallback:** Automatically degrades to native macOS `say` if models or dependencies are uninitialized, ensuring speech never breaks.

#### 2. 🍿 Apple TV & Couch Media Experience
- **Fluid Video Seeking:** Naturally seek across video players (`"skip 20 seconds"`, `"rewind"`, `"go back 15 seconds"`).
- **"What Did They Say?":** Rewinds 15 seconds and automatically toggles closed captions on (signature Apple TV feature).
- **Subtitle & Playback Controls:** `"subtitles on"`, `"toggle subtitles"`, `"next episode"`, and `"theater mode"`.
- **10-Foot Streaming Launchers:** Instant web launch for YouTube, Netflix, Hulu, Disney+, Max, Prime Video, and native macOS `TV.app`.
- **AirPlay Receiver:** Say `"airplay"` or `"screen mirroring"` to jump straight to AirPlay Receiver settings for iPhone/iPad casting.

#### 3. 🪄 Update-Proof Custom Shortcuts & Extensions
- **Voice Shortcuts (Zero Code):**
  - Multi-action sequences: *"Mac, when I say party mode, set volume to 80 and play some jazz"*
  - Aliases: *"Mac, alias surf to open safari"*
  - Shell scripts: *"Mac, when I say backup, run bash ~/backup.sh"*
  - Shortcut management: *"Mac, what are my shortcuts?"* and *"Mac, forget shortcut party mode"*
- **Custom Python Extensions:** Drop scripts into `~/.config/free-voice/extensions.py` using `register(add_command)` to hook arbitrary APIs, IoT devices, or background jobs.

#### 4. ⚙️ Persistent Background Daemon (`service.sh`)
- **Native LaunchAgent:** Run completely hands-free without keeping a terminal window open.
- **Auto-Start on Login:** Automatically launches on boot and restarts if needed.
- **Full Management CLI:**
  ```bash
  ./service.sh install    # Register and start LaunchAgent
  ./service.sh status     # Check PID, state, and recent logs
  ./service.sh logs       # Stream live daemon logs
  ./service.sh restart    # Restart service
  ./service.sh uninstall  # Remove LaunchAgent cleanly
  ```
- **Menu Bar Integration:** Interactive controls to audition voices, test chimes, and restart services from the macOS menu bar.

#### 5. 🧪 Bulletproof Reliability & Performance
- **213 Hermetic Unit Tests:** 100% test pass rate in <1 second across both virtual environment and system Python runtimes.
- **Sub-Millisecond Reflex Router:** Tier 0 regex and UI clicks execute in <0.3ms.
- **Fast Intent Decision Model:** Tier 0.5 executes in ~56ms on Apple Silicon M4.
- **Privacy First:** 100% local execution option; no personal data or API keys stored in source control.

---

### 📦 Quick Start / Install

```bash
git clone https://github.com/bparlette/free-mac-voice.git
cd free-mac-voice
./install.sh
```
