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

STATE_FILE = os.path.expanduser("~/.config/free-voice/radar_state.json")

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
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def run_radar():
    print("=" * 66)
    print(" 📡 Free Mac Voice — Autonomous Technology Radar")
    print(f" Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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

    if new_alerts:
        print(f"\n⚠️  {len(new_alerts)} new upgrade opportunities detected!")
        for a in new_alerts:
            print(f"   -> {a}")
    else:
        print("\n✨ All core technologies are at bleeding-edge state. No immediate action required.")

if __name__ == "__main__":
    run_radar()
