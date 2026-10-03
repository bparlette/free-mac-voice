# System Architecture & Handoff Brief: Free Mac Voice

## 1. Hardware & System Target
* **Device**: Apple Mac mini (M4, 2024), 16 GB Unified Memory, macOS 26.3.
* **Storage / Environment**: 50 GB free NVMe, Python 3.14 virtualenv, local Ollama + Metal acceleration.
* **Operational Constraint**: Max RAM budget for models ~5-6 GB total to prevent any macOS unified memory swap thrashing while user runs browser, IDE, etc.

## 2. Core Goal
A 100% free, private, local-first voice assistant for macOS that feels **instant (<500ms perceived latency)**, operates reliably in a **living room with ambient TV/podcast chatter** (zero false triggers), and provides deep, robust OS automation without chatbot fluff.

---

## 3. Current Architecture & Pipeline

### A. Audio & Transcription (STT)
* **Engine**: `mlx-whisper` (Apple Silicon Metal) or `whisper.cpp` (`tiny.en` / `small.en`).
* **VAD / Wake**: WebRTC VAD + Push-to-Talk (`Right Option`) or continuous wake-word (`"Mac"`).
* **Latency**: ~150–250ms for typical spoken command.

### B. Routing & Intent Classification Cascade
1. **Tier 0 (0ms)**: Exact regex matchers for high-frequency commands (`"open <app>"`, `"mute"`, `"snap left"`).
2. **Tier 0.5a (0ms)**: Cosine embedding vector match (`all-MiniLM-L6-v2` / local) against precomputed command anchors.
3. **Pre-Routing Gate (`is_voice_command`)**:
   * *Fast path (0ms)*: Regex for imperative command verbs.
   * *Filter (~100ms)*: TypeSafe decision model (`tev1:0.8b` via `/v1/systemone`) evaluates if utterance is actually directed at the computer. Rejects conversational / TV dialogue.
4. **Tier 0.5b Intent Classifier (~177ms)**:
   * **Default**: `tev1:0.8b` (811 MB, 1-token output via `/v1/systemone`) constrained to **8 core actions** (`open_app`, `switch_app`, `quit_app`, `set_volume`, `media`, `timer`, `web_search`, `none`) with a `0.7` confidence threshold.
   * **Fallback Toggle**: `qwen2.5:1.5b` via `.env` (`OLLAMA_DECISION_MODEL=qwen2.5:1.5b`).
5. **Tier 1 Planner**: Multi-action chaining (`"Open Safari, search for weather, and snap left"`) routed to `OLLAMA_PLANNER_MODEL` (`qwen2.5:1.5b` or `qwen3-vl:8b`).
6. **Tier 2 (Open-ended QA)**: Google Gemini 2.5 Flash free tier with Google Search grounding for world knowledge.

### C. OS Execution & UI
* **Window Snapping & Display Movement**: Native in-process PyObjC `AXUIElement` (~1–2ms). Zero subprocess spawn, works across native, web, and Electron apps.
* **Typing & Keystrokes**: In-process Quartz `CGEvent` / `pynput` (0ms).
* **UI Clicking & Locating**: `xa11y` (macOS Accessibility tree traversal) + Apple Vision OCR fallback.
* **Speech Synthesis (TTS)**: `kokoro-onnx` neural voice (~150ms) + macOS native `say` fallback.
* **Floating Visual HUD**: Hammerspoon `hs.canvas` translucent pill over local HTTP POST (port 19825, non-blocking daemon thread, 0ms pipeline impact). Shows listening state, recognized speech, and action confirmations.

---

## 4. Benchmark & Iteration History

### What was tested & discarded:
1. **Subprocess `osascript`**: Forcing AppleScript for window snapping and keystrokes caused 100–250ms spawn delays and failed on Electron apps. Replaced with in-process PyObjC (`AXUIElement`, `CGEvent`).
2. **Monolithic 39-action Qwen prompt**: Asking `qwen2.5:1.5b` to pick from 39 actions simultaneously caused a **43.3% false trigger rate** on non-commands.
3. **Router Bake-Off on M4 Mac mini** (tested across 369 real/synthetic phrases):
   * **Kev-4B (Q8_0, 4.48 GB)**: 81.6% accuracy, 9.2% false triggers, 659ms warm median. (High accuracy, but heavy RAM and slower).
   * **tev1:0.8b (Q8_0, 811 MB)**: 70.2% accuracy, cuts false triggers to ~16% with confidence $\ge 0.7$, **177ms warm median**. (Adopted as default).
   * **Julia-1 / Laya**: 13–75ms, but over-cautious (missed 80–100% of real commands).

---

## 5. Architectural Review Questions for Claude
1. Based on an M4 Mac mini with 16 GB Unified Memory and a target of <500ms total latency with zero TV false-triggers, what is your first-thought optimal architecture?
2. How does this 4-tier cascade (Regex $\to$ Embeddings $\to$ Gate $\to$ Tev1/Qwen $\to$ Native AXUIElement) compare to what you would build from scratch?
3. Would you recommend tearing down any part of this stack to start over, or keeping this foundation and tuning specific bottlenecks?
4. What are the top 2–3 failure modes or architectural blind spots you see in this current design?
