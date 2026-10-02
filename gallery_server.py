#!/usr/bin/env python3
"""
Screen Critic Clips Gallery — Web UI & Local Video Host
Provides a clean, modern web interface to preview, play, download, and share
video clips recorded when the screen companions roast.
Self-cleans videos older than 2 days automatically.
Runs on http://localhost:8765 (and accessible over local Wi-Fi).
"""

import os
import sys
import time
import json
import urllib.parse
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import subprocess
import threading

CLIPS_DIR = os.path.expanduser("~/.config/free-voice/clips")
PORT = 8765

def cleanup_old_clips(max_age_days: float = 2.0):
    """Prunes clips older than 2 days."""
    if not os.path.exists(CLIPS_DIR):
        return
    cutoff = time.time() - (max_age_days * 86400.0)
    for fname in os.listdir(CLIPS_DIR):
        fpath = os.path.join(CLIPS_DIR, fname)
        if os.path.isfile(fpath) and fname.endswith((".mp4", ".mov", ".json")):
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except Exception:
                pass

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Screen Critic — Video Clips</title>
  <style>
    :root {
      --bg: #0c0d12;
      --card-bg: #161822;
      --border: #282c3f;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.25);
      --text: #f1f5f9;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 32px 20px 60px;
    }
    .header {
      max-width: 1100px;
      margin: 0 auto 32px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 24px;
    }
    .title h1 {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .title p {
      color: var(--text-muted);
      font-size: 14px;
      margin-top: 4px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      background: rgba(56, 189, 248, 0.12);
      color: var(--accent);
      border: 1px solid var(--border);
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
    }
    .grid {
      max-width: 1100px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow: 0 8px 24px rgba(0,0,0,0.4);
      transition: transform 0.2s, border-color 0.2s;
    }
    .card:hover {
      transform: translateY(-3px);
      border-color: var(--accent);
    }
    video {
      width: 100%;
      height: 200px;
      background: #000;
      object-fit: cover;
    }
    .card-body {
      padding: 16px;
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .clip-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }
    .clip-title {
      font-weight: 600;
      font-size: 15px;
      color: var(--text);
    }
    .clip-date {
      font-size: 12px;
      color: var(--text-muted);
    }
    .actions {
      display: flex;
      gap: 10px;
      margin-top: 14px;
    }
    .btn {
      flex: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 9px 12px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: background 0.15s;
    }
    .btn-primary {
      background: var(--accent);
      color: #0b1120;
    }
    .btn-primary:hover {
      background: #7dd3fc;
    }
    .btn-secondary {
      background: #232738;
      color: var(--text);
    }
    .btn-secondary:hover {
      background: #31374e;
    }
    .empty {
      grid-column: 1 / -1;
      text-align: center;
      padding: 60px 20px;
      color: var(--text-muted);
    }
    .empty h3 { color: var(--text); font-size: 18px; margin-bottom: 8px; }
  </style>
</head>
<body>
  <div class="header">
    <div class="title">
      <h1>🎬 Screen Critic — Highlights Gallery</h1>
      <p>Automatic 8-second clips recorded when companions speak · Auto-cleans after 48 hours</p>
    </div>
    <div class="badge">🔥 __CLIP_COUNT__ Clips Available</div>
  </div>

  <div class="grid">
    __CLIPS_HTML__
  </div>

  <script>
    function copyUrl(url) {
      navigator.clipboard.writeText(url).then(() => {
        alert("Video link copied to clipboard!");
      });
    }
    function revealFinder(filename) {
      fetch('/reveal?file=' + encodeURIComponent(filename));
    }
  </script>
</body>
</html>
"""

class GalleryHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CLIPS_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # Action: Reveal in Finder
        if parsed.path == "/reveal":
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("file", [""])[0]
            if target:
                full_path = os.path.join(CLIPS_DIR, os.path.basename(target))
                if os.path.exists(full_path):
                    subprocess.run(["open", "-R", full_path])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            return

        # API: JSON list of clips
        if parsed.path == "/api/clips":
            os.makedirs(CLIPS_DIR, exist_ok=True)
            files = [f for f in os.listdir(CLIPS_DIR) if f.endswith((".mp4", ".mov"))]
            files.sort(key=lambda f: os.path.getmtime(os.path.join(CLIPS_DIR, f)), reverse=True)
            clips_meta = []
            for fname in files:
                fpath = os.path.join(CLIPS_DIR, fname)
                mtime = os.path.getmtime(fpath)
                clips_meta.append({
                    "filename": fname,
                    "url": f"/{urllib.parse.quote(fname)}",
                    "timestamp": mtime,
                    "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size_bytes": os.path.getsize(fpath)
                })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"clips": clips_meta}).encode("utf-8"))
            return

        # Main Gallery Page
        if parsed.path in ["/", "/index.html"]:
            os.makedirs(CLIPS_DIR, exist_ok=True)
            files = [f for f in os.listdir(CLIPS_DIR) if f.endswith((".mp4", ".mov"))]
            files.sort(key=lambda f: os.path.getmtime(os.path.join(CLIPS_DIR, f)), reverse=True)

            if not files:
                clips_html = """
                <div class="empty">
                  <h3>No clips recorded yet!</h3>
                  <p>When your companions roast you on screen, video clips will appear right here for preview & download.</p>
                </div>
                """
            else:
                cards = []
                for fname in files:
                    fpath = os.path.join(CLIPS_DIR, fname)
                    mtime = os.path.getmtime(fpath)
                    date_str = datetime.fromtimestamp(mtime).strftime("%b %d, %I:%M %p")
                    size_mb = os.path.getsize(fpath) / (1024 * 1024)

                    # Extract character tag from filename
                    parts = fname.replace(".mp4", "").split("_")
                    tag = "🎮 Screen Clip"
                    if len(parts) >= 4:
                        tag = f"🍿 {parts[3].capitalize()}"

                    download_url = f"/{urllib.parse.quote(fname)}"
                    card = f"""
                    <div class="card">
                      <video controls preload="metadata" src="{download_url}"></video>
                      <div class="card-body">
                        <div class="clip-meta">
                          <span class="clip-title">{tag}</span>
                          <span class="clip-date">{date_str} ({size_mb:.1f} MB)</span>
                        </div>
                        <div class="actions">
                          <a href="{download_url}" download="{fname}" class="btn btn-primary">⬇️ Download</a>
                          <button onclick="revealFinder('{fname}')" class="btn btn-secondary">📂 Finder</button>
                          <button onclick="copyUrl(window.location.origin + '{download_url}')" class="btn btn-secondary">🔗 Link</button>
                        </div>
                      </div>
                    </div>
                    """
                    cards.append(card)
                clips_html = "\n".join(cards)

            page = HTML_TEMPLATE.replace("__CLIP_COUNT__", str(len(files))).replace("__CLIPS_HTML__", clips_html)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))
            return

        # Serve static video files
        return super().do_GET()


CLEANUP_INTERVAL_S = 3600.0


def _cleanup_loop(interval_s: float = CLEANUP_INTERVAL_S):
    """Prune old clips periodically instead of on every request."""
    while True:
        time.sleep(interval_s)
        cleanup_old_clips(max_age_days=2.0)


def run_gallery():
    os.makedirs(CLIPS_DIR, exist_ok=True)
    cleanup_old_clips(max_age_days=2.0)
    threading.Thread(target=_cleanup_loop, daemon=True, name="clip-cleanup").start()
    host = os.environ.get("GALLERY_HOST", "127.0.0.1")
    # Threading server: one slow video download no longer blocks other clients
    server = ThreadingHTTPServer((host, PORT), GalleryHandler)
    print(f"🎬 Screen Critic Gallery running at: http://{host}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass



if __name__ == "__main__":
    run_gallery()
