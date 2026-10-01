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


import objc

class CompanionView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(CompanionView, self).initWithFrame_(frame)
        self.theme_key = "couch_duo"
        self.idle_image = None
        self.point_image = None
        self.current_image = None

        self.speaker_tag = "🎮 LEO"
        self.subtitle_text = ""
        self.accent_rgb = (0.20, 0.75, 1.00)
        self.is_speaking = False
        self.show_subtitle = False
        self.bob_offset = 0.0

        self.load_theme(self.theme_key)
        return self

    def load_theme(self, theme_key):
        self.theme_key = theme_key
        theme = THEMES.get(theme_key, THEMES["couch_duo"])
        idle_p = theme.get("idle_sprite")
        point_p = theme.get("point_sprite", idle_p)

        if os.path.exists(idle_p):
            self.idle_image = AppKit.NSImage.alloc().initWithContentsOfFile_(idle_p)
        if os.path.exists(point_p):
            self.point_image = AppKit.NSImage.alloc().initWithContentsOfFile_(point_p)
        else:
            self.point_image = self.idle_image

        self.current_image = self.idle_image
        char = theme["characters"][0]
        self.speaker_tag = char["tag"]
        self.accent_rgb = char["accent_rgb"]
        self.setNeedsDisplay_(True)

    def set_pose(self, pose: str):
        if pose == "point" and self.point_image:
            self.current_image = self.point_image
        else:
            self.current_image = self.idle_image
        self.setNeedsDisplay_(True)

    def update_riff(self, char_dict, text):
        self.speaker_tag = char_dict["tag"]
        self.accent_rgb = char_dict["accent_rgb"]
        self.subtitle_text = text
        self.show_subtitle = True
        self.setNeedsDisplay_(True)

    def set_speaking(self, speaking):
        self.is_speaking = speaking
        if not speaking:
            # Keep subtitle showing for a moment or hide
            pass
        self.setNeedsDisplay_(True)

    def set_bob(self, offset):
        self.bob_offset = offset
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)

        w = rect.size.width
        h = rect.size.height

        # Draw Character Sprite anchored 100% flush at bottom-left with 3D Depth & Shadows
        img = self.current_image or self.idle_image
        if img:
            aspect = img.size().width / max(1.0, img.size().height)
            # Give headroom for shadow to cast upward & rightward
            img_h = min(h - 22.0, (w - 28.0) / aspect)
            img_w = img_h * aspect

            # 100% flush to bottom-left corner: zero margin
            img_x = 0.0
            img_y = max(0.0, self.bob_offset)

            dest_rect = AppKit.NSMakeRect(img_x, img_y, img_w, img_h)
            src_rect = AppKit.NSMakeRect(0, 0, img.size().width, img.size().height)

            ctx = AppKit.NSGraphicsContext.currentContext()

            # 1. Deep 3D Ambient Occlusion & Drop Shadow
            depth_shadow = AppKit.NSShadow.alloc().init()
            depth_shadow.setShadowColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.90))
            depth_shadow.setShadowOffset_(AppKit.NSMakeSize(8.0, 10.0))
            depth_shadow.setShadowBlurRadius_(22.0)

            ctx.saveGraphicsState()
            depth_shadow.set()
            img.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
            )
            ctx.restoreGraphicsState()

            # 2. Subtle Cinematic Rim Lighting / Accent Glow
            r, g, b = self.accent_rgb
            glow_shadow = AppKit.NSShadow.alloc().init()
            glow_shadow.setShadowColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.40))
            glow_shadow.setShadowOffset_(AppKit.NSMakeSize(0.0, 4.0))
            glow_shadow.setShadowBlurRadius_(14.0)

            ctx.saveGraphicsState()
            glow_shadow.set()
            img.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 0.50
            )
            ctx.restoreGraphicsState()

            # 3. Crisp Foreground Character Illustration Pass
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
        self.is_speaking = False
        return self

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

        win_w = 320.0
        win_h = 160.0

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
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |  # Required for macOS full-screen spaces
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
        win_w = 320.0
        win_h = 160.0

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

    def trigger_riff(self):
        if self.is_speaking:
            return

        def worker():
            self.is_speaking = True
            app, title = get_active_window_info()
            char, riff = generate_critic_riff(self.active_theme, app, title)

            # Switch to point/gesture pose
            run_on_main(lambda: self.view.set_pose("point"))
            run_on_main(lambda: self.view.update_riff(char, riff))
            run_on_main(lambda: self.view.set_speaking(True))

            # Speech cadence bobbing
            stop_bob = threading.Event()
            def bob_loop():
                start_t = time.time()
                while not stop_bob.is_set():
                    t = time.time() - start_t
                    offset = math.sin(t * 12.0) * 3.5
                    run_on_main(lambda o=offset: self.view.set_bob(o))
                    time.sleep(0.04)
                run_on_main(lambda: self.view.set_bob(0.0))

            t = threading.Thread(target=bob_loop, daemon=True)
            t.start()

            speak_voice(char["voice"], riff)

            stop_bob.set()
            run_on_main(lambda: self.view.set_speaking(False))
            time.sleep(1.2)  # Pause before returning to idle
            run_on_main(lambda: self.view.set_pose("idle"))
            self.is_speaking = False

        threading.Thread(target=worker, daemon=True).start()

    def start_animation_and_riff_loop(self):
        def idle_anim_loop():
            """Smooth continuous breathing micro-animation (30 FPS)."""
            start_t = time.time()
            last_curious_glance = time.time()

            while self.running:
                if not self.is_speaking:
                    now = time.time()
                    t = now - start_t
                    # Subtle breathing undulation
                    breath = math.sin(t * 2.2) * 1.5
                    run_on_main(lambda b=breath: self.view.set_bob(b))

                    # Occasional idle gesture (glance & point) every 25 seconds
                    if now - last_curious_glance > 26.0:
                        last_curious_glance = now
                        if self.active_theme == "couch_duo":
                            run_on_main(lambda: self.view.set_pose("point"))
                            time.sleep(2.0)
                            run_on_main(lambda: self.view.set_pose("idle"))

                time.sleep(0.033)

        threading.Thread(target=idle_anim_loop, daemon=True).start()

        def main_riff_loop():
            # Initial Welcome Greeting
            theme = THEMES.get(self.active_theme, THEMES["couch_duo"])
            speaker_name, intro = theme["intro"]
            char = next((c for c in theme["characters"] if c["name"] == speaker_name), theme["characters"][0])

            run_on_main(lambda: self.view.update_riff(char, intro))
            run_on_main(lambda: self.view.set_pose("point"))
            self.is_speaking = True
            speak_voice(char["voice"], intro)
            self.is_speaking = False
            time.sleep(1.0)
            run_on_main(lambda: self.view.set_pose("idle"))

            last_riff_time = time.time()
            interval = 24.0  # Roast every 24 seconds

            while self.running:
                if os.path.exists(COMMAND_FILE):
                    try:
                        with open(COMMAND_FILE, "r") as f:
                            cmd = f.read().strip()
                        os.remove(COMMAND_FILE)

                        if cmd == "roast_now":
                            self.trigger_riff()
                            last_riff_time = time.time()
                        elif cmd.startswith("set_theme:"):
                            new_theme = cmd.split("set_theme:", 1)[1].strip()
                            self.set_theme(new_theme)
                        elif cmd.startswith("set_pos:"):
                            new_pos = cmd.split("set_pos:", 1)[1].strip()
                            self.set_position(new_pos)
                        elif cmd == "stop":
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
            run_on_main(lambda: self.view.set_pose("point"))
            speak_voice(char["voice"], farewell)
            run_on_main(lambda: AppKit.NSApp().terminate_(None))

        threading.Thread(target=main_riff_loop, daemon=True).start()


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
