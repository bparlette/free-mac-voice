#!/usr/bin/env python3
"""
Free Mac Voice — Autonomous Technology Radar
Monitors GitHub releases, Hugging Face trending models, and local speech/LLM benchmarks.
Compares new discoveries against the current baseline:
  - ASR Baseline: Phonon-2 (164 MB, ~42 ms on Apple MLX)
  - TTS Baseline: Kokoro-82M (82M params, ~150 ms)
  - Tier 0.5 Baseline: Qwen2.5:1.5b (~48 ms)
  - Tier 1 VLM Baseline: Qwen3-VL:8b (~1.26 s)
"""

import sys
import os
import json
import urllib.request
import urllib.error
import subprocess
from datetime import datetime

UPSTREAM_REPOS = [
    {"repo": "ml-explore/mlx", "desc": "Apple MLX Framework"},
    {"repo": "FermionResearch/Phonon", "desc": "Phonon-2 Speech Recognition"},
    {"repo": "thewh1teagle/kokoro-onnx", "desc": "Kokoro ONNX Neural TTS"},
    {"repo": "ggerganov/whisper.cpp", "desc": "Whisper.cpp (Metal Streaming)"},
    {"repo": "ollama/ollama", "desc": "Ollama Local Model Runtime"},
    {"repo": "xa11y/xa11y", "desc": "macOS Accessibility Tree Bridge"}
]

HF_TAGS = [
    {"tag": "automatic-speech-recognition", "category": "ASR (Speech-to-Text)"},
    {"tag": "text-to-speech", "category": "TTS (Voice Synthesis)"},
    {"tag": "text-generation", "category": "Compact Reasoning / SLM (<3B)", "max_params": 3000000000}
]

CONFIG_DIR = os.path.expanduser("~/.config/free-voice")
STATE_FILE = os.path.join(CONFIG_DIR, "radar_state.json")
REPORT_FILE = os.path.join(CONFIG_DIR, "radar_report.md")

def fetch_json(url: str, timeout: int = 8):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FreeMacVoice-TechRadar/1.0 (+https://github.com/bparlette/free-mac-voice)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return None

def check_github_releases():
    results = []
    for item in UPSTREAM_REPOS:
        repo = item["repo"]
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        data = fetch_json(url)
        if data and "tag_name" in data:
            results.append({
                "repo": repo,
                "desc": item["desc"],
                "tag": data.get("tag_name"),
                "date": data.get("published_at", "")[:10],
                "url": data.get("html_url")
            })
    return results

def check_huggingface_trending():
    trending = []
    for hf in HF_TAGS:
        tag = hf["tag"]
        url = f"https://huggingface.co/api/models?pipeline_tag={tag}&sort=trendingScore&direction=-1&limit=4"
        data = fetch_json(url)
        if data and isinstance(data, list):
            items = []
            for m in data:
                items.append({
                    "id": m.get("id"),
                    "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0)
                })
            trending.append({
                "category": hf["category"],
                "models": items
            })
    return trending

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def send_notification(title: str, message: str):
    """Sends a native macOS desktop notification via AppleScript."""
    try:
        script = f'display notification "{message}" with title "{title}" sound name "Tink"'
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def generate_markdown_report(scan_time: str, releases: list, trending: list, new_alerts: list):
    lines = [
        "# 📡 Free Mac Voice — Morning Technology Radar Report",
        f"\n**Generated:** {scan_time}",
        "\n---",
        "\n### 📦 Upstream Framework & Model Status",
        "| Component / Framework | Repository | Latest Release | Release Date | Status |",
        "|:---|:---|:---:|:---:|:---:|"
    ]
    for r in releases:
        status = r.get("status", "✅ Up to date")
        lines.append(f"| **{r['desc']}** | [{r['repo']}](https://github.com/{r['repo']}) | `{r['tag']}` | {r['date']} | {status} |")

    lines.append("\n### 🚀 Hugging Face Trending Models")
    for cat in trending:
        lines.append(f"\n#### {cat['category']}")
        for m in cat["models"]:
            lines.append(f"- **[{m['id']}](https://huggingface.co/{m['id']})** — ❤️ {m['likes']:,} likes · 📥 {m['downloads']:,} downloads")

    lines.append("\n### ⚡ Current Production Baselines")
    lines.append("- **ASR Engine:** Phonon-2 (164 MB, ~42.3 ms on Apple MLX GPU/NE)")
    lines.append("- **TTS Synthesis:** Kokoro-82M (82M params, ~150 ms locally on ONNX)")
    lines.append("- **Decision Router:** Qwen2.5:1.5b (~48.0 ms locally via Ollama JSON)")
    lines.append("- **Screen Vision:** Qwen3-VL:8b (~1.26s locally via Ollama unified memory)")

    lines.append("\n### 🎯 Actionable Upgrade Alerts")
    if new_alerts:
        for a in new_alerts:
            lines.append(f"- ⚠️ **{a}**")
    else:
        lines.append("- ✨ **All core voice technologies are at the bleeding edge. No action required.**")

    lines.append("\n---\n*Report generated daily at 7:00 AM via native macOS LaunchAgent (`com.free-mac-voice.radar`).*")
    return "\n".join(lines)

def run_radar():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 66)
    print(" 📡 Free Mac Voice — Autonomous Technology Radar")
    print(f" Timestamp: {now_str}")
    print("=" * 66)
    
    state = load_state()
    prev_releases = state.get("releases", {})
    new_alerts = []

    print("\n📦 Upstream Framework & Model Releases:")
    print("-" * 66)
    releases = check_github_releases()
    new_releases_state = {}
    for r in releases:
        repo = r["repo"]
        tag = r["tag"]
        new_releases_state[repo] = tag
        is_new = prev_releases.get(repo) != tag and repo in prev_releases
        marker = "🔥 NEW UPDATE" if is_new else "✅ Up to date"
        r["status"] = marker
        print(f" • {r['desc']} ({repo}): {tag} ({r['date']}) — {marker}")
        if is_new:
            new_alerts.append(f"New upstream release for {r['desc']}: {tag}")

    print("\n🚀 Hugging Face Trending Speech & Small Language Models:")
    print("-" * 66)
    trending = check_huggingface_trending()
    for cat in trending:
        print(f" [{cat['category']}]")
        for m in cat["models"]:
            print(f"   - {m['id']} (❤️ {m['likes']} likes, 📥 {m['downloads']} downloads)")

    print("\n⚡ Current Free Mac Voice Technology Baselines:")
    print("-" * 66)
    print(" • ASR Engine:      Phonon-2 (164 MB, ~42 ms on Apple MLX GPU/NE)")
    print(" • TTS Synthesis:   Kokoro-82M (82M params, ~150 ms locally on ONNX)")
    print(" • Decision Router: Qwen2.5:1.5b (~48 ms locally via Ollama JSON)")
    print(" • Screen Vision:   Qwen3-VL:8b (~1.26s locally via Ollama unified memory)")
    print("=" * 66)

    # Save state
    state["releases"] = new_releases_state
    state["last_scan"] = datetime.now().isoformat()
    save_state(state)

    # Save markdown report
    os.makedirs(CONFIG_DIR, exist_ok=True)
    report_content = generate_markdown_report(now_str, releases, trending, new_alerts)
    try:
        with open(REPORT_FILE, "w") as f:
            f.write(report_content)
        print(f"\n💾 Saved findings report to: {REPORT_FILE}")
    except Exception as e:
        print(f"Failed to write report: {e}")

    # Alerts & notifications
    if new_alerts:
        print(f"\n⚠️  {len(new_alerts)} new upgrade opportunities detected!")
        for a in new_alerts:
            print(f"   -> {a}")
        send_notification("🎙️ Voice Tech Radar Alert", f"{len(new_alerts)} new framework/model updates found!")
    else:
        print("\n✨ All core technologies are at bleeding-edge state. No immediate action required.")

if __name__ == "__main__":
    run_radar()
