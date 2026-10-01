#!/usr/bin/env python3
"""
Generate ultra-crisp, high-resolution OpenGraph social preview cards for Free Mac Voice Control.
Outputs:
  - docs/assets/benchmark_card.png (1200x630, 2x retina rendered: 2400x1260)
  - docs/assets/social_preview.png (1280x640, 2x retina rendered: 2560x1280)
"""

import os
from PIL import Image, ImageDraw, ImageFont

def get_font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf"
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                # 0 is normal, 1 is bold in Helvetica.ttc
                index = 1 if (bold and c.endswith(".ttc")) else 0
                return ImageFont.truetype(c, size, index=index)
            except Exception:
                try:
                    return ImageFont.truetype(c, size)
                except Exception:
                    pass
    return ImageFont.load_default()

def draw_rounded_rect(draw, bbox, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=width)

def render_benchmark_card(out_path: str):
    # 2x supersampling for razor-sharp antialiasing
    scale = 2
    w, h = 1200 * scale, 630 * scale
    img = Image.new("RGBA", (w, h), (10, 11, 16, 255))
    draw = ImageDraw.Draw(img)

    # Subtle background gradient grid effect
    for y in range(0, h, 80 * scale):
        draw.line([(0, y), (w, y)], fill=(255, 255, 255, 4), width=1)
    for x in range(0, w, 80 * scale):
        draw.line([(x, 0), (x, h)], fill=(255, 255, 255, 4), width=1)

    # Outer border
    draw_rounded_rect(draw, [16 * scale, 16 * scale, w - 16 * scale, h - 16 * scale], 24 * scale, fill=None, outline=(255, 255, 255, 20), width=2 * scale)

    # Top Tag & Category
    badge_bg = (0, 113, 227, 45)
    badge_border = (0, 113, 227, 180)
    badge_box = [50 * scale, 48 * scale, 330 * scale, 86 * scale]
    draw_rounded_rect(draw, badge_box, 18 * scale, fill=badge_bg, outline=badge_border, width=2 * scale)
    
    font_badge = get_font(18 * scale, bold=True)
    draw.text((68 * scale, 56 * scale), "🎙️ FREE MAC VOICE CONTROL", fill=(64, 160, 255, 255), font=font_badge)

    font_subbadge = get_font(18 * scale, bold=False)
    draw.text((w - 380 * scale, 56 * scale), "github.com/bparlette/free-mac-voice", fill=(140, 145, 160, 255), font=font_subbadge)

    # Main Headline
    font_title = get_font(46 * scale, bold=True)
    draw.text((50 * scale, 108 * scale), "Phonon-2: 7.2x Faster Voice Control", fill=(255, 255, 255, 255), font=font_title)

    font_sub = get_font(21 * scale, bold=False)
    draw.text((50 * scale, 172 * scale), "NVIDIA Parakeet-TDT vs. OpenAI Whisper on Apple Silicon • 100% On-Device", fill=(160, 165, 185, 255), font=font_sub)

    # Benchmark Comparison Box
    bench_box = [50 * scale, 220 * scale, w - 50 * scale, 430 * scale]
    draw_rounded_rect(draw, bench_box, 20 * scale, fill=(18, 20, 28, 220), outline=(255, 255, 255, 28), width=2 * scale)

    font_label = get_font(19 * scale, bold=True)
    font_num = get_font(22 * scale, bold=True)
    font_tag = get_font(16 * scale, bold=False)

    # Row 1: Whisper base.en
    draw.text((75 * scale, 245 * scale), "OpenAI Whisper base.en (CPU/Torch)", fill=(200, 205, 220, 255), font=font_label)
    bar_x = 75 * scale
    bar_y = 280 * scale
    max_bar_w = (w - 280 * scale)
    whisper_w = int(max_bar_w * 0.88)
    draw_rounded_rect(draw, [bar_x, bar_y, bar_x + whisper_w, bar_y + 26 * scale], 12 * scale, fill=(75, 80, 95, 200))
    draw.text((bar_x + whisper_w + 16 * scale, bar_y - 2 * scale), "303.1 ms", fill=(180, 185, 200, 255), font=font_num)

    # Row 2: Phonon-2 MLX
    draw.text((75 * scale, 330 * scale), "Phonon-2 (Apple MLX GPU / Neural Engine)", fill=(52, 199, 89, 255), font=font_label)
    bar2_y = 365 * scale
    phonon_w = int(whisper_w / 7.16)
    draw_rounded_rect(draw, [bar_x, bar2_y, bar_x + phonon_w, bar2_y + 26 * scale], 12 * scale, fill=(52, 199, 89, 255))
    draw.text((bar_x + phonon_w + 16 * scale, bar2_y - 2 * scale), "42.3 ms", fill=(52, 199, 89, 255), font=font_num)

    # Speedup Callout Pill
    callout_box = [bar_x + phonon_w + 160 * scale, bar2_y - 4 * scale, bar_x + phonon_w + 350 * scale, bar2_y + 30 * scale]
    draw_rounded_rect(draw, callout_box, 16 * scale, fill=(52, 199, 89, 45), outline=(52, 199, 89, 200), width=2 * scale)
    font_callout = get_font(18 * scale, bold=True)
    draw.text((bar_x + phonon_w + 182 * scale, bar2_y + 2 * scale), "⚡ 7.2x FASTER", fill=(52, 230, 100, 255), font=font_callout)

    # 4 Stat Metric Cards at Bottom
    card_w = (w - 100 * scale - 45 * scale) // 4
    card_y = 455 * scale
    card_h = 130 * scale
    stats = [
        {"val": "42.3 ms", "label": "Avg Latency", "sub": "Sub-50ms Speech"},
        {"val": "164 MB", "label": "Model Footprint", "sub": "10x < Whisper Large"},
        {"val": "5.2% WER", "label": "Accuracy", "sub": "Matches Large-v3"},
        {"val": "0.0 ms", "label": "Silence Audio", "sub": "Zero Hallucination"}
    ]

    for i, s in enumerate(stats):
        cx = 50 * scale + i * (card_w + 15 * scale)
        cbox = [cx, card_y, cx + card_w, card_y + card_h]
        draw_rounded_rect(draw, cbox, 16 * scale, fill=(22, 24, 34, 230), outline=(255, 255, 255, 20), width=1 * scale)
        
        font_stat_val = get_font(26 * scale, bold=True)
        font_stat_lbl = get_font(16 * scale, bold=True)
        font_stat_sub = get_font(14 * scale, bold=False)

        val_color = (64, 180, 255, 255) if i == 0 else (255, 255, 255, 255)
        draw.text((cx + 18 * scale, card_y + 16 * scale), s["val"], fill=val_color, font=font_stat_val)
        draw.text((cx + 18 * scale, card_y + 58 * scale), s["label"], fill=(200, 205, 220, 255), font=font_stat_lbl)
        draw.text((cx + 18 * scale, card_y + 88 * scale), s["sub"], fill=(130, 135, 150, 255), font=font_stat_sub)

    # Downscale with Lanczos for razor-sharp antialiasing
    final_img = img.resize((1200, 630), Image.Resampling.LANCZOS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    final_img.save(out_path, "PNG", optimize=True)
    print(f"✅ Generated: {out_path} (1200x630)")

def render_social_preview(out_path: str):
    # 2x supersampling: 2560x1280 downscaled to 1280x640
    scale = 2
    w, h = 1280 * scale, 640 * scale
    img = Image.new("RGBA", (w, h), (10, 11, 16, 255))
    draw = ImageDraw.Draw(img)

    # Outer border
    draw_rounded_rect(draw, [18 * scale, 18 * scale, w - 18 * scale, h - 18 * scale], 24 * scale, fill=None, outline=(255, 255, 255, 20), width=2 * scale)

    # Top Tag
    badge_bg = (0, 113, 227, 45)
    badge_border = (0, 113, 227, 180)
    badge_box = [50 * scale, 48 * scale, 440 * scale, 86 * scale]
    draw_rounded_rect(draw, badge_box, 18 * scale, fill=badge_bg, outline=badge_border, width=2 * scale)
    
    font_badge = get_font(18 * scale, bold=True)
    draw.text((68 * scale, 56 * scale), "🎙️ 100% PRIVATE • APPLE SILICON", fill=(64, 160, 255, 255), font=font_badge)

    # Headline
    font_title = get_font(52 * scale, bold=True)
    draw.text((50 * scale, 112 * scale), "Free Mac Voice Control", fill=(255, 255, 255, 255), font=font_title)

    font_sub = get_font(23 * scale, bold=False)
    draw.text((50 * scale, 182 * scale), "Fast, on-device voice assistant for macOS • $0 Forever, Zero Cloud Subscriptions", fill=(160, 165, 185, 255), font=font_sub)

    # 5 Tech Stack Columns
    tech_box = [50 * scale, 240 * scale, w - 50 * scale, 570 * scale]
    draw_rounded_rect(draw, tech_box, 20 * scale, fill=(18, 20, 28, 220), outline=(255, 255, 255, 28), width=2 * scale)

    tiers = [
        {"icon": "⚡", "tier": "Tier 0: Reflex", "tech": "Compiled Regex", "lat": "< 0.3 ms", "desc": "Instant App & Window Control"},
        {"icon": "🧠", "tier": "Tier 0.5: Router", "tech": "Cosine + Qwen2.5", "lat": "48 ms", "desc": "Semantic Slot Classification"},
        {"icon": "🎙️", "tier": "ASR Front-End", "tech": "Phonon-2 (MLX)", "lat": "42 ms", "desc": "164MB Parakeet-TDT Model"},
        {"icon": "👁️", "tier": "Tier 1: Vision", "tech": "Qwen3-VL:8b", "lat": "1.2 s", "desc": "On-Device Screen OCR & UI"},
        {"icon": "🗣️", "tier": "Neural Speech", "tech": "Kokoro-82M", "lat": "150 ms", "desc": "Studio-Quality Human Voice"}
    ]

    col_w = (w - 100 * scale - 40 * scale) // 5
    for i, t in enumerate(tiers):
        tx = 50 * scale + 10 * scale + i * col_w
        ty = 265 * scale
        
        # Column card
        col_box = [tx, ty, tx + col_w - 15 * scale, ty + 275 * scale]
        draw_rounded_rect(draw, col_box, 16 * scale, fill=(24, 26, 36, 200), outline=(255, 255, 255, 15), width=1 * scale)

        font_icon = get_font(30 * scale, bold=False)
        font_tier = get_font(18 * scale, bold=True)
        font_tech = get_font(16 * scale, bold=False)
        font_lat = get_font(22 * scale, bold=True)
        font_desc = get_font(14 * scale, bold=False)

        draw.text((tx + 18 * scale, ty + 20 * scale), t["icon"], font=font_icon)
        draw.text((tx + 18 * scale, ty + 68 * scale), t["tier"], fill=(255, 255, 255, 255), font=font_tier)
        draw.text((tx + 18 * scale, ty + 98 * scale), t["tech"], fill=(160, 165, 185, 255), font=font_tech)
        draw.text((tx + 18 * scale, ty + 155 * scale), t["lat"], fill=(52, 199, 89, 255), font=font_lat)
        draw.text((tx + 18 * scale, ty + 200 * scale), t["desc"], fill=(130, 135, 150, 255), font=font_desc)

    final_img = img.resize((1280, 640), Image.Resampling.LANCZOS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    final_img.save(out_path, "PNG", optimize=True)
    print(f"✅ Generated: {out_path} (1280x640)")

if __name__ == "__main__":
    render_benchmark_card("docs/assets/benchmark_card.png")
    render_social_preview("docs/assets/social_preview.png")
