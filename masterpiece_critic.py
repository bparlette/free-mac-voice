#!/usr/bin/env python3
"""
Screen Critic & Desktop Companion — Multi-Theme Overlay
Spawns a transparent, click-through macOS overlay companion sitting unobtrusively
in the bottom-left corner of the screen, reacting, observing, and riffing on whatever
you are doing (games, YouTube, TV, coding) using local AI and Kokoro neural voices.

Features:
  - 5 Distinct Themes:
      1. couch_duo (DEFAULT): Leo (chill gamer) & Cleo (the cat)
      2. wine_girls: Chloe & Maya (gossipy best friends sipping wine)
      3. theater_critic: Sir Reginald (pompous British arts critic in armchair)
      4. byte_orbit: Byte (CRT robot) & Orbit (floating drone)
      5. kids_club: Toby & Barnaby (wholesome kid in hoodie with juice box & puppy)
  - Ultra-compact, low-profile layout in bottom-left corner (never blocks the center)
  - Continuous idle breathing animation + gesture state switching
  - 100% click-through (ignores mouse events)
  - Local Kokoro neural speech + local Qwen LLM
"""

import os
import sys
import time
import json
import math
import signal
import urllib.request
import subprocess
import threading
import random
from datetime import datetime

import AppKit
from Foundation import NSObject, NSTimer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets", "themes")
PID_FILE = "/tmp/masterpiece_critic.pid"
COMMAND_FILE = "/tmp/masterpiece_critic_cmd.txt"
CONFIG_FILE = os.path.expanduser("~/.config/free-voice/critic_config.json")

KOKORO_DIR = os.path.expanduser("~/.config/free-voice/models/kokoro")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")

THEMES = {
    "couch_duo": {
        "title": "Couch Duo",
        "idle_sprite": os.path.join(ASSETS_DIR, "couch_duo_idle.png"),
        "point_sprite": os.path.join(ASSETS_DIR, "couch_duo_point.png"),
        "characters": [
            {
                "name": "Leo",
                "tag": "🎮 LEO",
                "voice": "am_adam",
                "accent_rgb": (0.20, 0.75, 1.00),  # Electric Cyan
                "system": (
                    "You are Leo, a chill, sarcastic gamer lounging on the couch. "
                    "You are watching the human's screen. Roast their misclicks, weird game choices, "
                    "or computer habits with dry gamer humor in 1-2 punchy sentences. "
                    "Return ONLY your spoken line without quotes or prefixes."
                )
            },
            {
                "name": "Cleo",
                "tag": "🐾 CLEO",
                "voice": "af_sarah",
                "accent_rgb": (0.85, 0.40, 0.90),  # Purple
                "system": (
                    "You are Cleo, an elegant, haughty cat sitting on the couch. "
                    "You look down on human screen activities and drop deadpan, condescending feline observations "
                    "in 1-2 sentences. Return ONLY your spoken line without quotes or prefixes."
                )
            }
        ],
        "intro": ("Leo", "Settle in, Cleo. Let's see what questionable gameplay we're witnessing today."),
        "farewell": ("Leo", "Alright, stream's over! Couch nap time.")
    },
    "wine_girls": {
        "title": "Wine Night",
        "idle_sprite": os.path.join(ASSETS_DIR, "wine_girls.png"),
        "point_sprite": os.path.join(ASSETS_DIR, "wine_girls.png"),
        "characters": [
            {
                "name": "Chloe",
                "tag": "🍷 CHLOE",
                "voice": "af_nicole",
                "accent_rgb": (0.95, 0.35, 0.55),  # Rosé Pink
                "system": (
                    "You are Chloe, relaxing with a glass of wine on the couch gossiping with your best friend Maya. "
                    "You are watching whatever the human is doing on screen. Deliver a funny, gossipy, playful roast "
                    "about their choices in 1-2 sentences. Return ONLY your spoken line without quotes or prefixes."
                )
            },
            {
                "name": "Maya",
                "tag": "🍾 MAYA",
                "voice": "af_sky",
                "accent_rgb": (0.85, 0.20, 0.35),  # Burgundy Red
                "system": (
                    "You are Maya, sipping wine on the couch with Chloe. You're observant, witty, and drop sharp, "
                    "hilarious commentary on the human's screen activities in 1-2 sentences. "
                    "Return ONLY your spoken line without quotes or prefixes."
                )
            }
        ],
        "intro": ("Chloe", "Pour another glass, Maya. We are definitely going to need it for this."),
        "farewell": ("Maya", "Bottle's empty, and so is our patience! Bye!")
    },
    "theater_critic": {
        "title": "Masterpiece Critic",
        "idle_sprite": os.path.join(ASSETS_DIR, "theater_critic.png"),
        "point_sprite": os.path.join(ASSETS_DIR, "theater_critic.png"),
        "characters": [
            {
                "name": "Sir Reginald",
                "tag": "🎭 SIR REGINALD",
                "voice": "bm_george",
                "accent_rgb": (1.00, 0.75, 0.25),  # Antique Gold
                "system": (
                    "You are Sir Reginald, an eccentric, highbrow arts critic sitting in a leather wingback armchair. "
                    "You review the human's desktop and gaming as if it were an avant-garde theatrical tragedy. "
                    "Be pompous, theatrical, and witty in 1-2 sentences. Return ONLY your spoken line without quotes or prefixes."
                )
            }
        ],
        "intro": ("Sir Reginald", "Welcome, discerning connoisseurs, to another tragic spectacle of modern computing."),
        "farewell": ("Sir Reginald", "Curtain calls! Even genius requires a respite from such amateurism.")
    },
    "byte_orbit": {
        "title": "Byte & Orbit",
        "idle_sprite": os.path.join(ASSETS_DIR, "byte_orbit.png"),
        "point_sprite": os.path.join(ASSETS_DIR, "byte_orbit.png"),
        "characters": [
            {
                "name": "Byte",
                "tag": "🤖 BYTE",
                "voice": "am_puck",
                "accent_rgb": (0.25, 0.95, 0.65),  # Neon Mint
                "system": (
                    "You are Byte, a retro CRT-headed desktop robot companion. "
                    "You calculate human error probabilities and deliver robotic, witty roasts about their screen actions "
                    "in 1-2 sentences. Return ONLY your spoken line without quotes or prefixes."
                )
            },
            {
                "name": "Orbit",
                "tag": "🛸 ORBIT",
                "voice": "af_heart",
                "accent_rgb": (0.35, 0.80, 1.00),  # Cyber Blue
                "system": (
                    "You are Orbit, a cheerful, sarcastic floating drone. You chime in with quick, witty cyber quips "
                    "about the user's screen in 1-2 sentences. Return ONLY your spoken line without quotes or prefixes."
                )
            }
        ],
        "intro": ("Byte", "Scanning screen buffer... Alert: high density of user inefficiencies detected."),
        "farewell": ("Orbit", "Entering power saving sleep mode! Don't crash anything while we're gone.")
    },
    "kids_club": {
        "title": "Kids Club",
        "idle_sprite": os.path.join(ASSETS_DIR, "kids_club.png"),
        "point_sprite": os.path.join(ASSETS_DIR, "kids_club.png"),
        "characters": [
            {
                "name": "Toby",
                "tag": "🧃 TOBY & BARNABY",
                "voice": "am_puck",
                "accent_rgb": (1.00, 0.60, 0.15),  # Sunshine Orange
                "system": (
                    "You are Toby, an energetic, curious kid in an animal hoodie holding a juice box beside your puppy Barnaby. "
                    "You are 100% wholesome, excited, encouraging, and silly when watching games or videos. "
                    "Give an enthusiastic, fun kid observation or cheer in 1-2 sentences. "
                    "Return ONLY your spoken line without quotes or prefixes."
                )
            }
        ],
        "intro": ("Toby", "Juice box ready! Barnaby and I are cheering for you, let's go!"),
        "farewell": ("Toby", "Puppy nap time! You did awesome!")
    }
}

_kokoro = None

def get_kokoro():
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    if os.path.isfile(KOKORO_MODEL) and os.path.isfile(KOKORO_VOICES):
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
            return _kokoro
        except Exception:
            pass
    return None

def speak_voice(voice_name: str, text: str):
    kokoro = get_kokoro()
    wav_path = "/tmp/critic_riff.wav"
    lang = "en-gb" if voice_name.startswith("b") else "en-us"
    if kokoro:
        try:
            import soundfile as sf
            samples, sr = kokoro.create(text, voice=voice_name, speed=0.95, lang=lang)
            sf.write(wav_path, samples, sr)
            subprocess.run(["afplay", wav_path], check=True)
            return
        except Exception as e:
            print(f"[Critic] Kokoro voice error ({e}), falling back to say")

    # Fallback to system say
    sys_voice = "Daniel" if lang == "en-gb" else "Samantha"
    subprocess.run(["say", "-v", sys_voice, "-r", "175", text])

def get_active_window_info() -> tuple[str, str]:
    sc = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
        try
            set winTitle to title of window 1 of (first application process whose frontmost is true)
        on error
            set winTitle to "Main Screen"
        end try
        return frontApp & " ||| " & winTitle
    end tell
    '''
    try:
        res = subprocess.run(["osascript", "-e", sc], capture_output=True, text=True, timeout=3).stdout.strip()
        if " ||| " in res:
            app, title = res.split(" ||| ", 1)
            return app.strip(), title.strip()
        return res.strip() or "Desktop", "Main Screen"
    except Exception:
        return "Desktop", "Main Screen"

def generate_critic_riff(theme_key: str, app_name: str, win_title: str) -> tuple[dict, str]:
    theme = THEMES.get(theme_key, THEMES["couch_duo"])
    char = random.choice(theme["characters"])

    prompt = (
        f"{char['system']}\n"
        f"Context: The human is currently using '{app_name}' with active window: '{win_title}'."
    )

    body = {
        "model": "qwen2.5:1.5b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.88, "num_predict": 75}
    }

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            riff = data.get("response", "").strip().strip('"')
            # Clean any leading name prefix
            prefix = f"{char['name']}:"
            if riff.lower().startswith(prefix.lower()):
                riff = riff[len(prefix):].strip()
            if riff:
                return char, riff
    except Exception:
        pass

    # High-quality fallback riffs per theme
    fallbacks = {
        "couch_duo": [
            "Did you just click that by accident, or was that an intentional disaster?",
            "Look at those frantic clicks! Someone get this player a tutorial.",
            "Watching this window is giving me nine different kinds of second-hand anxiety."
        ],
        "wine_girls": [
            "Oh honey, no. Tell me he didn't just spend five minutes on that.",
            "I'm pouring another glass because watching this is a full-time endurance test.",
            "I love the confidence, but the execution is pure chaos."
        ],
        "theater_critic": [
            f"Behold the modern human struggling with {app_name}. Riveting theatre indeed.",
            f"I have witnessed profound artistic tragedies, but {win_title} rivals them all.",
            "Could someone perhaps direct us toward something resembling competence?"
        ],
        "byte_orbit": [
            "User efficiency dropped 42%. Diagnostics conclude: pure confusion.",
            "Re-calculating probability of success... Result is zero point zero percent.",
            "I have processed 10 billion calculations and none justify that decision."
        ],
        "kids_club": [
            "Whoa! Did you see that move?! That was so awesome!",
            "Barnaby says don't give up, you're almost at the next level!",
            "I think this is the coolest thing on the screen today!"
        ]
    }
    riff = random.choice(fallbacks.get(theme_key, fallbacks["couch_duo"]))
    return char, riff

def run_on_main(fn):
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


CLIPS_DIR = os.path.expanduser("~/.config/free-voice/clips")

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

def record_clip_async(char_name: str, theme_name: str, duration: int = 7):
    """Records video clip of screen while companion talks, muxing audio if available."""
    def worker():
        try:
            os.makedirs(CLIPS_DIR, exist_ok=True)
            cleanup_old_clips(max_age_days=2.0)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            clip_path = os.path.join(CLIPS_DIR, f"clip_{timestamp}_{theme_name}_{char_name}.mp4")
            temp_vid = f"/tmp/screencap_{timestamp}.mp4"
            subprocess.run(["screencapture", "-V", str(duration), temp_vid], check=True, timeout=duration + 4)
            if os.path.exists(temp_vid):
                audio_wav = "/tmp/critic_riff.wav"
                if os.path.exists(audio_wav) and os.path.exists("/opt/homebrew/bin/ffmpeg"):
                    subprocess.run([
                        "/opt/homebrew/bin/ffmpeg", "-y",
                        "-i", temp_vid,
                        "-i", audio_wav,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        clip_path
                    ], check=True, timeout=8, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        os.remove(temp_vid)
                    except Exception:
                        pass
                else:
                    os.rename(temp_vid, clip_path)
                print(f"[Critic] Highlight clip saved: {clip_path}")
        except Exception as e:
            print(f"[Critic] Clip recording note: {e}")

    threading.Thread(target=worker, daemon=True).start()


import objc

class CompanionView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(CompanionView, self).initWithFrame_(frame)
        self.theme_key = "couch_duo"
        self.base_image = None
        self.pose_images = {}
        self.idle_bare_image = None
        self.leo_fg_image = None
        self.cat_walk_frames = []
        self.cat_walk_frames_left = []

        self.current_pose = "idle"
        self.target_pose = "idle"
        self.blend_alpha = 0.0
        self.idle_breath_y = 0.0

        self.is_cat_walking = False
        self.cat_walk_t = 0.0  # 0.0 to 1.0
        self.cat_walk_frame_idx = 0
        self.cat_walk_dir = -1  # -1 = left, 1 = right

        self.speaker_tag = "🎮 LEO"
        self.accent_rgb = (0.20, 0.75, 1.00)
        self.is_speaking = False

        self.load_theme(self.theme_key)
        return self

    def load_theme(self, theme_key):
        self.theme_key = theme_key
        theme = THEMES.get(theme_key, THEMES["couch_duo"])

        actions_dir = os.path.join(ASSETS_DIR, "couch_duo", "actions")
        if theme_key == "couch_duo" and os.path.exists(os.path.join(actions_dir, "pose_idle.png")):
            for p in ["idle", "point", "stretch", "pet"]:
                fpath = os.path.join(actions_dir, f"pose_{p}.png")
                if os.path.exists(fpath):
                    self.pose_images[p] = AppKit.NSImage.alloc().initWithContentsOfFile_(fpath)
            
            bare_p = os.path.join(actions_dir, "idle_bare_clean.png")
            if os.path.exists(bare_p):
                self.idle_bare_image = AppKit.NSImage.alloc().initWithContentsOfFile_(bare_p)
            
            chars_p = os.path.join(actions_dir, "idle_duo_characters.png")
            if os.path.exists(chars_p):
                self.idle_chars_image = AppKit.NSImage.alloc().initWithContentsOfFile_(chars_p)
            
            leo_p = os.path.join(actions_dir, "leo_foreground.png")
            if os.path.exists(leo_p):
                self.leo_fg_image = AppKit.NSImage.alloc().initWithContentsOfFile_(leo_p)

            self.cat_walk_frames = []
            for i in range(1, 9):
                cat_p = os.path.join(actions_dir, f"cat_walk_f{i}.png")
                if os.path.exists(cat_p):
                    self.cat_walk_frames.append(AppKit.NSImage.alloc().initWithContentsOfFile_(cat_p))

            self.base_image = self.pose_images.get("idle")
        else:
            idle_p = theme.get("idle_sprite")
            if os.path.exists(idle_p):
                self.base_image = AppKit.NSImage.alloc().initWithContentsOfFile_(idle_p)
            self.pose_images = {}

        char = theme["characters"][0]
        self.speaker_tag = char["tag"]
        self.accent_rgb = char["accent_rgb"]
        self.setNeedsDisplay_(True)

    def set_blend(self, from_pose, to_pose, alpha, breath_y=0.0):
        self.current_pose = from_pose
        self.target_pose = to_pose
        self.blend_alpha = max(0.0, min(1.0, alpha))
        self.idle_breath_y = breath_y
        self.is_cat_walking = False
        self.setNeedsDisplay_(True)

    def set_cat_walk(self, walk_t, frame_idx, walk_dir):
        self.is_cat_walking = True
        self.cat_walk_t = walk_t
        self.cat_walk_frame_idx = frame_idx % max(1, len(self.cat_walk_frames))
        self.cat_walk_dir = walk_dir
        self.setNeedsDisplay_(True)

    def update_riff(self, char_dict, text=None):
        self.speaker_tag = char_dict["tag"]
        self.accent_rgb = char_dict["accent_rgb"]
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)

        w = rect.size.width
        h = rect.size.height

        ctx = AppKit.NSGraphicsContext.currentContext()

        # 1. 3D Drop Shadow & Ambient Occlusion behind the entire scene
        depth_shadow = AppKit.NSShadow.alloc().init()
        depth_shadow.setShadowColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.90))
        depth_shadow.setShadowOffset_(AppKit.NSMakeSize(8.0, 8.0))
        depth_shadow.setShadowBlurRadius_(18.0)

        # 2. Subtle Cinematic Rim Lighting / Accent Glow
        r, g, b = self.accent_rgb
        glow_shadow = AppKit.NSShadow.alloc().init()
        glow_shadow.setShadowColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.35))
        glow_shadow.setShadowOffset_(AppKit.NSMakeSize(0.0, 4.0))
        glow_shadow.setShadowBlurRadius_(12.0)

        dest_rect = AppKit.NSMakeRect(0.0, 0.0, w, h)

        if self.is_cat_walking and self.idle_bare_image and self.cat_walk_frames:
            # === CAT WALKING ALONG COUCH BACKREST ===
            src_rect = AppKit.NSMakeRect(0, 0, self.idle_bare_image.size().width, self.idle_bare_image.size().height)
            
            # 1. Draw bare couch background
            ctx.saveGraphicsState()
            depth_shadow.set()
            self.idle_bare_image.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
            )
            ctx.restoreGraphicsState()

            self.idle_bare_image.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
            )

            # 2. Draw Cat stepping on top of back cushion
            # In 1024x571 canvas: cushion top is at y = 316 (AppKit bottom-origin: y = 571 - 316 = 255)
            scale = h / 571.0
            cat_y = (571.0 - 316.0 + 8.0) * scale
            cat_h = 105.0 * scale * 0.45
            cat_w = 170.0 * scale * 0.45

            # Walk x interpolates between right armrest (0.76 * w) and left armrest (0.16 * w)
            cat_x = (0.76 * w) - (self.cat_walk_t * (0.60 * w))
            cat_rect = AppKit.NSMakeRect(cat_x, cat_y, cat_w, cat_h)

            cat_img = self.cat_walk_frames[self.cat_walk_frame_idx]
            cat_src = AppKit.NSMakeRect(0, 0, cat_img.size().width, cat_img.size().height)

            ctx.saveGraphicsState()
            if self.cat_walk_dir == -1: # Walking left -> flip horizontally
                t = AppKit.NSAffineTransform.transform()
                t.translateXBy_yBy_(cat_x + cat_w, cat_y)
                t.scaleXBy_yBy_(-1.0, 1.0)
                t.translateXBy_yBy_(-cat_x, -cat_y)
                t.concat()
            cat_img.drawInRect_fromRect_operation_fraction_(
                cat_rect, cat_src, AppKit.NSCompositingOperationSourceOver, 1.0
            )
            ctx.restoreGraphicsState()

            # 3. Draw Leo in foreground (cat passes behind his neck and shoulders!)
            if self.leo_fg_image:
                self.leo_fg_image.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
                )

        elif self.pose_images:
            # === ACTION POSE MORPHING (COUCH REMAINS 100% ROCK SOLID) ===
            from_img = self.pose_images.get(self.current_pose, self.pose_images.get("idle"))
            to_img = self.pose_images.get(self.target_pose, from_img)

            src_rect = AppKit.NSMakeRect(0, 0, from_img.size().width, from_img.size().height)

            # Draw Shadow Pass
            ctx.saveGraphicsState()
            depth_shadow.set()
            from_img.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
            )
            ctx.restoreGraphicsState()

            # Rock-solid couch + organic breathing character layer during idle
            if self.current_pose == "idle" and self.blend_alpha <= 0.01 and self.idle_bare_image and hasattr(self, 'idle_chars_image') and self.idle_chars_image:
                # 1. Couch is 100% stationary and anchored to bottom edge
                self.idle_bare_image.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
                )
                # 2. Leo and Cleo breathing subtly above couch
                char_rect = AppKit.NSMakeRect(0.0, self.idle_breath_y, w, h)
                self.idle_chars_image.drawInRect_fromRect_operation_fraction_(
                    char_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
                )
            elif self.blend_alpha <= 0.001 or from_img == to_img:
                from_img.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
                )
            else:
                # Fluid multi-frame cross-dissolve: couch is identical, limbs morph smoothly
                from_img.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0 - self.blend_alpha
                )
                to_img.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, self.blend_alpha
                )
        else:
            # Fallback for other themes
            img = self.base_image
            if img:
                src_rect = AppKit.NSMakeRect(0, 0, img.size().width, img.size().height)
                img.drawInRect_fromRect_operation_fraction_(
                    dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
                )


class CriticOverlayController(NSObject):
    def init(self):
        self = objc.super(CriticOverlayController, self).init()
        self.window = None
        self.view = None
        self.running = True
        self.active_theme = "couch_duo"
        self.active_position = "bottom_left"
        self.speaking_character = None
        self.active_action = "idle"
        self.action_lock = threading.Lock()
        self.rotating_animations = False
        self.rotate_thread = None
        return self

    def start_rotation(self):
        if self.rotating_animations:
            return
        self.rotating_animations = True

        def rotation_worker():
            print("[Critic] Starting continuous animation rotation...")
            sequence = [
                ("point", 3.0),
                ("pet", 3.0),
                ("cat_walk", 3.8),
                ("stretch", 3.5),
            ]
            idx = 0
            while self.running and self.rotating_animations:
                action, dur = sequence[idx % len(sequence)]
                self.play_action(action, duration=dur)
                time.sleep(dur + 1.2)
                time.sleep(1.0)
                idx += 1

        self.rotate_thread = threading.Thread(target=rotation_worker, daemon=True)
        self.rotate_thread.start()

    def stop_rotation(self):
        self.rotating_animations = False

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    cfg = json.load(f)
                    self.active_theme = cfg.get("theme", "couch_duo")
                    self.active_position = cfg.get("position", "bottom_left")
            except Exception:
                pass

    def save_config(self):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"theme": self.active_theme, "position": self.active_position}, f, indent=2)
        except Exception:
            pass

    def setup_window(self):
        self.load_config()
        screen = AppKit.NSScreen.mainScreen()
        screen_frame = screen.frame()

        # Aspect ratio 1024 x 571 = 1.7933
        win_w = 360.0
        win_h = 201.0

        if self.active_position == "bottom_center":
            win_x = (screen_frame.size.width - win_w) / 2.0
            win_y = 0.0
        elif self.active_position == "bottom_right":
            win_x = screen_frame.size.width - win_w
            win_y = 0.0
        else:  # bottom_left (default) - ALL THE WAY FLUSH TO SCREEN CORNER
            win_x = 0.0
            win_y = 0.0

        rect = AppKit.NSMakeRect(win_x, win_y, win_w, win_h)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False
        )

        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
        # Floats above exclusive full-screen apps (RetroArch, Apple TV, Games)
        self.window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        self.window.setIgnoresMouseEvents_(True)  # 100% Click-through!
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )

        self.view = CompanionView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, win_w, win_h))
        self.view.load_theme(self.active_theme)
        self.window.setContentView_(self.view)
        self.window.makeKeyAndOrderFront_(None)

    def set_position(self, pos_name: str):
        screen = AppKit.NSScreen.mainScreen()
        screen_frame = screen.frame()
        win_w = 360.0
        win_h = 201.0

        if pos_name == "bottom_center":
            win_x = (screen_frame.size.width - win_w) / 2.0
            win_y = 0.0
        elif pos_name == "bottom_right":
            win_x = screen_frame.size.width - win_w
            win_y = 0.0
        else:
            pos_name = "bottom_left"
            win_x = 0.0
            win_y = 0.0

        self.active_position = pos_name
        self.save_config()
        run_on_main(lambda: self.window.setFrameOrigin_(AppKit.NSMakePoint(win_x, win_y)))

    def set_theme(self, theme_key: str):
        if theme_key in THEMES:
            self.active_theme = theme_key
            self.save_config()
            run_on_main(lambda: self.view.load_theme(theme_key))

    def play_action(self, action_name: str, duration: float = 3.0):
        """Animates seamlessly to target action pose, holds, and returns to idle."""
        def worker():
            with self.action_lock:
                if action_name == "cat_walk":
                    # Animate cat walk across couch back
                    steps = 35
                    # Walk left
                    for i in range(steps):
                        t = i / float(steps)
                        frame_idx = i % 8
                        run_on_main(lambda t=t, f=frame_idx: self.view.set_cat_walk(t, f, -1))
                        time.sleep(0.045)
                    # Pause at left
                    time.sleep(0.6)
                    # Walk back right
                    for i in range(steps):
                        t = 1.0 - (i / float(steps))
                        frame_idx = i % 8
                        run_on_main(lambda t=t, f=frame_idx: self.view.set_cat_walk(t, f, 1))
                        time.sleep(0.045)
                    # Return to idle
                    run_on_main(lambda: self.view.set_blend("idle", "idle", 0.0))
                    return

                # Smooth transition into action pose (0.35s)
                blend_steps = 14
                for i in range(1, blend_steps + 1):
                    p = i / float(blend_steps)
                    # Cubic ease-in-out
                    alpha = p * p * (3.0 - 2.0 * p)
                    run_on_main(lambda a=alpha: self.view.set_blend("idle", action_name, a))
                    time.sleep(0.025)

                # Hold pose for specified duration
                time.sleep(max(0.5, duration))

                # Smooth return to idle (0.35s)
                for i in range(1, blend_steps + 1):
                    p = 1.0 - (i / float(blend_steps))
                    alpha = p * p * (3.0 - 2.0 * p)
                    run_on_main(lambda a=alpha: self.view.set_blend("idle", action_name, a))
                    time.sleep(0.025)

                run_on_main(lambda: self.view.set_blend("idle", "idle", 0.0))

        threading.Thread(target=worker, daemon=True).start()

    def trigger_riff(self):
        if self.speaking_character is not None:
            return

        def worker():
            app, title = get_active_window_info()
            char, riff = generate_critic_riff(self.active_theme, app, title)
            char_name = char["name"]
            self.speaking_character = char_name

            # Start video clip recording during speech
            record_clip_async(char_name, self.active_theme, duration=7)

            # Update accent lighting
            run_on_main(lambda: self.view.update_riff(char))

            # Leo points at screen when roasting!
            if char_name == "Leo":
                self.play_action("point", duration=3.5)
            elif char_name == "Cleo":
                self.play_action("pet", duration=3.0)

            speak_voice(char["voice"], riff)
            self.speaking_character = None

        threading.Thread(target=worker, daemon=True).start()

    def run_animation_demo(self):
        """Runs through all dynamic animations sequentially with live voice riffing & lighting."""
        def demo_worker():
            theme = THEMES.get(self.active_theme, THEMES["couch_duo"])
            leo = next((c for c in theme["characters"] if c["name"] == "Leo"), theme["characters"][0])
            cleo = next((c for c in theme["characters"] if c["name"] == "Cleo"), theme["characters"][-1])

            # Record demo video clip
            record_clip_async("FullShowcase", self.active_theme, duration=26)

            # 1. Idle with natural chest breathing
            time.sleep(2.0)

            # 2. Leo Points at the Screen
            run_on_main(lambda: self.view.update_riff(leo))
            self.speaking_character = "Leo"
            self.play_action("point", duration=4.0)
            speak_voice(leo["voice"], "Yo! Check out that misclick right there! Pointing directly at your active window.")
            self.speaking_character = None
            time.sleep(1.0)

            # 3. Leo Pets Cleo
            run_on_main(lambda: self.view.update_riff(cleo))
            self.speaking_character = "Cleo"
            self.play_action("pet", duration=4.2)
            speak_voice(cleo["voice"], "Purrrr! Now that is proper royal treatment. Nine out of ten cats approve.")
            self.speaking_character = None
            time.sleep(1.0)

            # 4. Cleo walks across the back cushion of the couch!
            run_on_main(lambda: self.view.update_riff(cleo))
            self.speaking_character = "Cleo"
            self.play_action("cat_walk", duration=4.0)
            speak_voice(cleo["voice"], "Just taking my high-ground patrol across the couch back. Nothing escapes my watch.")
            self.speaking_character = None
            time.sleep(1.2)

            # 5. Leo stands up and does a full overhead stretch!
            run_on_main(lambda: self.view.update_riff(leo))
            self.speaking_character = "Leo"
            self.play_action("stretch", duration=4.0)
            speak_voice(leo["voice"], "Whew! Couch nap complete. Big stretch, now let's get back in the game!")
            self.speaking_character = None
            time.sleep(1.0)

            # Return to peaceful idle
            run_on_main(lambda: self.view.update_riff(leo))

        threading.Thread(target=demo_worker, daemon=True).start()

    def start_animation_and_riff_loop(self):
        self.start_animation_loop()

        def main_riff_loop():
            # Initial Welcome Greeting
            theme = THEMES.get(self.active_theme, THEMES["couch_duo"])
            speaker_name, intro = theme["intro"]
            char = next((c for c in theme["characters"] if c["name"] == speaker_name), theme["characters"][0])

            run_on_main(lambda: self.view.update_riff(char, intro))
            self.speaking_character = char["name"]
            speak_voice(char["voice"], intro)
            self.speaking_character = None
            time.sleep(1.0)

            last_riff_time = time.time()
            interval = 28.0  # Roast every 28 seconds

            while self.running:
                if os.path.exists(COMMAND_FILE):
                    try:
                        with open(COMMAND_FILE, "r") as f:
                            cmd = f.read().strip()
                        os.remove(COMMAND_FILE)

                        if cmd == "roast_now":
                            self.trigger_riff()
                            last_riff_time = time.time()
                        elif cmd in ["demo", "roast_demo"]:
                            self.run_animation_demo()
                            last_riff_time = time.time()
                        elif cmd in ["rotate", "roast_rotate"]:
                            self.start_rotation()
                        elif cmd in ["stop_rotate", "stop_rotation"]:
                            self.stop_rotation()
                        elif cmd == "stretch":
                            self.play_action("stretch", duration=3.5)
                        elif cmd == "point":
                            self.play_action("point", duration=3.5)
                        elif cmd == "pet":
                            self.play_action("pet", duration=3.5)
                        elif cmd in ["catwalk", "cat_walk"]:
                            self.play_action("cat_walk", duration=4.0)
                        elif cmd.startswith("set_theme:"):
                            new_theme = cmd.split("set_theme:", 1)[1].strip()
                            self.set_theme(new_theme)
                        elif cmd.startswith("set_pos:"):
                            new_pos = cmd.split("set_pos:", 1)[1].strip()
                            self.set_position(new_pos)
                        elif cmd == "stop":
                            self.stop_rotation()
                            self.running = False
                            break
                    except Exception:
                        pass

                if time.time() - last_riff_time >= interval:
                    self.trigger_riff()
                    last_riff_time = time.time()

                time.sleep(0.5)

            # Farewell
            farewell_speaker, farewell = theme["farewell"]
            char = next((c for c in theme["characters"] if c["name"] == farewell_speaker), theme["characters"][0])
            run_on_main(lambda: self.view.update_riff(char, farewell))
            self.speaking_character = char["name"]
            speak_voice(char["voice"], farewell)
            self.speaking_character = None
            run_on_main(lambda: AppKit.NSApp().terminate_(None))

        threading.Thread(target=main_riff_loop, daemon=True).start()

    def start_animation_loop(self):
        def anim_loop():
            """60 FPS continuous ambient breathing and animation loop."""
            start_t = time.time()

            while self.running:
                now = time.time()
                t = now - start_t

                # Subtle organic idle chest breathing
                breath_y = math.sin(t * 1.8) * 0.8

                if not self.view.is_cat_walking and self.view.blend_alpha <= 0.01:
                    run_on_main(lambda b=breath_y: self.view.set_blend("idle", "idle", 0.0, breath_y=b))

                time.sleep(0.016)  # 60 FPS

        threading.Thread(target=anim_loop, daemon=True).start()


def run_overlay():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = CriticOverlayController.alloc().init()
    controller.setup_window()
    controller.start_animation_and_riff_loop()

    app.run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "roast":
        with open(COMMAND_FILE, "w") as f:
            f.write("roast_now")
        print("Triggered on-demand screen roast.")
    elif len(sys.argv) > 1 and sys.argv[1] in ["demo", "roast_demo"]:
        with open(COMMAND_FILE, "w") as f:
            f.write("demo")
        print("Triggered animation showcase demo.")
    elif len(sys.argv) > 1 and sys.argv[1] in ["rotate", "roast_rotate"]:
        with open(COMMAND_FILE, "w") as f:
            f.write("rotate")
        print("Triggered continuous animation rotation.")
    elif len(sys.argv) > 1 and sys.argv[1] in ["stop_rotate", "stop_rotation"]:
        with open(COMMAND_FILE, "w") as f:
            f.write("stop_rotate")
        print("Stopped animation rotation.")
    elif len(sys.argv) > 1 and sys.argv[1] in ["stretch", "point", "pet", "catwalk", "cat_walk"]:
        with open(COMMAND_FILE, "w") as f:
            f.write(sys.argv[1])
        print(f"Triggered action: {sys.argv[1]}.")
    elif len(sys.argv) > 1 and sys.argv[1] == "theme" and len(sys.argv) > 2:
        with open(COMMAND_FILE, "w") as f:
            f.write(f"set_theme:{sys.argv[2]}")
        print(f"Switched critic theme to {sys.argv[2]}.")
    elif len(sys.argv) > 1 and sys.argv[1] == "pos" and len(sys.argv) > 2:
        with open(COMMAND_FILE, "w") as f:
            f.write(f"set_pos:{sys.argv[2]}")
        print(f"Moved critic position to {sys.argv[2]}.")
    elif len(sys.argv) > 1 and sys.argv[1] == "stop":
        with open(COMMAND_FILE, "w") as f:
            f.write("stop")
        print("Stopping screen companion...")
    else:
        run_overlay()

