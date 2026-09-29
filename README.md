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
| “set volume to 30” | Sets volume |
| “play” / “next” | Media keys |
| “set a timer for 5 minutes” | Background timer, speaks when done |
| “click the Reply button” | Clicks the real button in the front app |
| “search best pizza near me” | Google search |
| “what time is it” | Speaks the time |
| “lock” | Locks the screen |

Say **“help”** anytime to hear all ~58 commands. Or double-click **Voice Control.command** — no terminal needed.

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

Works whether you installed from `git clone` or the zip download. It pulls the latest version, preserves your virtual environment and `~/.free-voice/.env`, then re-runs the installer to refresh dependencies. (Git users can also just `git pull`.)

## How it stays free (30-second version)

Three tiers, fastest first. The system only uses a slower tier when the faster one can't help:

1. **Tier 0 — the reflex (<1 ms).** Pattern matching on your words. Handles every standard command instantly — even mid-sentence, but only when the command is complete (“open notes” fires; “open no” never misfires).
2. **Tier 1 — the local fallback (~1 s).** A tiny AI (Qwen 1.5B) running on your Mac translates unusual phrasing (“could you be a dear and open my browser thing”) into the same commands. Still $0, still on your Mac.
3. **Tier 2 — the answerer (optional).** Google's free API tier answers open-ended questions aloud. Needs a free API key; skip it and Tier 2 just stays quiet.

Full implementation details, latency math, and honest limits: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

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
