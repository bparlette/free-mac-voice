#!/usr/bin/env python3
"""
Mystery Science Theater 3000 (MST3K) — Screen Riffing & Roasting Overlay
Spawns an iconic transparent, click-through macOS theater overlay of Crow T. Robot,
the movie watcher, and Tom Servo sitting in cinema seats at the bottom of the screen,
riffing and roasting whatever you are doing (gaming, watching TV/YouTube, writing code)
using local vision/context and Kokoro neural voices.
"""

import os
import sys
import time
import json
import signal
import base64
import urllib.request
import subprocess
import threading
import random
from datetime import datetime

import AppKit
from Foundation import NSObject, NSTimer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SILHOUETTE_PATH = os.path.join(ASSETS_DIR, "mst3k_silhouette.png")
PID_FILE = "/tmp/masterpiece_critic.pid"
COMMAND_FILE = "/tmp/masterpiece_critic_cmd.txt"

KOKORO_DIR = os.path.expanduser("~/.config/free-voice/models/kokoro")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")

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

def speak_riff(character: str, text: str):
    """
    Speaks riff using Kokoro neural voice matching character:
      - Crow T. Robot: am_puck (snarky, energetic)
      - Tom Servo: bm_george (pretentious, theatrical baritone)
    """
    voice = "am_puck" if "crow" in character.lower() else "bm_george"
    lang = "en-us" if voice == "am_puck" else "en-gb"
    speed = 0.96 if voice == "am_puck" else 0.92

    kokoro = get_kokoro()
    wav_path = "/tmp/mst3k_riff.wav"
    if kokoro:
        try:
            import soundfile as sf
            samples, sr = kokoro.create(text, voice=voice, speed=speed, lang=lang)
            sf.write(wav_path, samples, sr)
            subprocess.run(["afplay", wav_path], check=True)
            return
        except Exception as e:
            print(f"[MST3K] Kokoro voice error ({e}), falling back to say")

    # Fallback to system say
    sys_voice = "Zarvox" if "crow" in character.lower() else "Daniel"
    subprocess.run(["say", "-v", sys_voice, "-r", "170", text])

def get_active_window_info() -> tuple[str, str]:
    """Retrieves frontmost app and window title via AppleScript."""
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

def generate_mst3k_riff(app_name: str, win_title: str) -> tuple[str, str]:
    """
    Generates an MST3K style riff from Crow T. Robot or Tom Servo.
    Returns (character, text).
    """
    speaker = random.choice(["Crow T. Robot", "Tom Servo"])
    
    prompt = (
        f"You are {speaker} from Mystery Science Theater 3000 (MST3K). "
        f"You are sitting in the theater seats at the bottom of the screen riffing on whatever the human is doing on their Mac. "
        f"The human is currently using the app: '{app_name}' with window: '{win_title}'. "
        f"Deliver a 1 to 2 sentence hilarious, snarky movie-riff roast about their gameplay, video watching, or computer habits. "
        f"Be witty, sarcastic, pop-culture savvy, and funny. "
        f"Return ONLY the spoken riff without character name tags or quotation marks."
    )

    body = {
        "model": "qwen2.5:1.5b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.88, "num_predict": 90}
    }

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            riff = data.get("response", "").strip().strip('"')
            # Clean any leading character name if the model echoed it
            for prefix in ["Crow:", "Crow T. Robot:", "Tom Servo:", "Tom:"]:
                if riff.lower().startswith(prefix.lower()):
                    riff = riff[len(prefix):].strip()
            if riff:
                return speaker, riff
    except Exception:
        pass

    # High-quality fallback riffs
    crow_fallbacks = [
        f"Did he just click that on purpose? Someone revoke this human's mouse privileges!",
        f"Oh great, {app_name}! I can feel my circuits slowly turning into rust watching this.",
        f"If boredom was an Olympic sport, this playthrough of {win_title} would take home the gold.",
        f"Look, he's checking his tabs again! That's three minutes of my robot life I'm never getting back."
    ]
    tom_fallbacks = [
        f"Behold, ladies and gentlemen: the dramatic tension of watching someone fail at {app_name}.",
        f"I've seen more compelling narrative development on an ingredient label.",
        f"Ah yes, {win_title}. Truly an avant-garde exploration of missing the point entirely.",
        f"Could we perhaps hit fast-forward on this? My gumball head is starting to overheat."
    ]

    riff = random.choice(crow_fallbacks if speaker == "Crow T. Robot" else tom_fallbacks)
    return speaker, riff


import objc

class MST3KView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(MST3KView, self).initWithFrame_(frame)
        self.image = AppKit.NSImage.alloc().initWithContentsOfFile_(SILHOUETTE_PATH)
        self.speaker = "🤖 CROW T. ROBOT"
        self.subtitle_text = "Movie sign! We've got movie sign!"
        self.is_speaking = False
        self.bob_offset = 0.0
        return self

    def update_riff(self, speaker, text):
        self.speaker = f"🤖 {speaker.upper()}" if "crow" in speaker.lower() else f"🍿 {speaker.upper()}"
        self.subtitle_text = text
        self.setNeedsDisplay_(True)

    def update_speaking(self, speaking):
        self.is_speaking = speaking
        self.setNeedsDisplay_(True)

    def update_bob(self, offset):
        self.bob_offset = offset
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)

        w = rect.size.width
        h = rect.size.height

        # 1. Draw Cinema Subtitle / Riffing Box at the top
        box_margin = 20.0
        box_w = w - (box_margin * 2)
        box_h = 115.0
        box_y = h - box_h - 10.0

        box_rect = AppKit.NSMakeRect(box_margin, box_y, box_w, box_h)
        bg_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box_rect, 10.0, 10.0)

        # Semi-transparent movie theater dark background
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.04, 0.06, 0.88).setFill()
        bg_path.fill()

        # Neon amber / retro sci-fi border
        is_crow = "CROW" in self.speaker
        if is_crow:
            border_col = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.72, 0.20, 0.90)  # Gold/Yellow for Crow
        else:
            border_col = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.35, 0.30, 0.90)  # Red for Tom Servo

        border_col.setStroke()
        bg_path.setLineWidth_(2.0)
        bg_path.stroke()

        # Speaker Tag: CROW T. ROBOT or TOM SERVO
        speaker_font = AppKit.NSFont.boldSystemFontOfSize_(11.5)
        speaker_attrs = {
            AppKit.NSFontAttributeName: speaker_font,
            AppKit.NSForegroundColorAttributeName: border_col
        }
        title_str = AppKit.NSString.stringWithString_(f"🚀 MST3K · {self.speaker}")
        title_str.drawAtPoint_withAttributes_(AppKit.NSMakePoint(box_margin + 16.0, box_y + box_h - 22.0), speaker_attrs)

        # Body Riff Text (Crisp retro yellow/white subtitle font)
        body_font = AppKit.NSFont.fontWithName_size_("Helvetica-Bold", 14.0)
        if not body_font:
            body_font = AppKit.NSFont.boldSystemFontOfSize_(14.0)

        para_style = AppKit.NSMutableParagraphStyle.alloc().init()
        para_style.setLineSpacing_(3.0)

        body_attrs = {
            AppKit.NSFontAttributeName: body_font,
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            AppKit.NSParagraphStyleAttributeName: para_style
        }
        text_rect = AppKit.NSMakeRect(box_margin + 16.0, box_y + 10.0, box_w - 32.0, box_h - 38.0)
        body_str = AppKit.NSString.stringWithString_(f'"{self.subtitle_text}"')
        body_str.drawInRect_withAttributes_(text_rect, body_attrs)

        # 2. Draw MST3K Theater Silhouette Row at bottom
        if self.image:
            img_w = w * 0.92
            img_h = (img_w / self.image.size().width) * self.image.size().height
            img_x = (w - img_w) / 2.0
            img_y = self.bob_offset  # Bobbing when speaking

            dest_rect = AppKit.NSMakeRect(img_x, img_y, img_w, img_h)
            src_rect = AppKit.NSMakeRect(0, 0, self.image.size().width, self.image.size().height)
            self.image.drawInRect_fromRect_operation_fraction_(
                dest_rect, src_rect, AppKit.NSCompositingOperationSourceOver, 1.0
            )


def run_on_main(fn):
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class MST3KOverlayController(NSObject):
    def init(self):
        self = objc.super(MST3KOverlayController, self).init()
        self.window = None
        self.view = None
        self.running = True
        return self

    def setupWindow(self):
        screen = AppKit.NSScreen.mainScreen()
        screen_frame = screen.frame()

        win_w = 640.0
        win_h = 360.0
        # Position at the bottom center of the main display
        win_x = (screen_frame.size.width - win_w) / 2.0
        win_y = 10.0

        rect = AppKit.NSMakeRect(win_x, win_y, win_w, win_h)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False
        )

        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.window.setLevel_(AppKit.NSStatusWindowLevel + 2)  # Stays above all apps/games
        self.window.setIgnoresMouseEvents_(True)  # Completely click-through!
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )

        self.view = MST3KView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, win_w, win_h))
        self.window.setContentView_(self.view)
        self.window.makeKeyAndOrderFront_(None)

    def triggerRiff(self):
        def worker():
            app, title = get_active_window_info()
            speaker, riff = generate_mst3k_riff(app, title)

            # Update UI on main thread
            run_on_main(lambda: self.view.update_riff(speaker, riff))
            run_on_main(lambda: self.view.update_speaking(True))

            # Bob animation loop while speaking
            stop_bob = threading.Event()
            def bob_loop():
                step = 0
                while not stop_bob.is_set():
                    offset = 3.5 if (step % 2 == 0) else 0.0
                    run_on_main(lambda o=offset: self.view.update_bob(o))
                    step += 1
                    time.sleep(0.18)
                run_on_main(lambda: self.view.update_bob(0.0))

            t = threading.Thread(target=bob_loop, daemon=True)
            t.start()

            speak_riff(speaker, riff)

            stop_bob.set()
            run_on_main(lambda: self.view.update_speaking(False))

        threading.Thread(target=worker, daemon=True).start()

    def startRiffLoop(self):
        def loop():
            # Initial introductory MST3K greeting
            intro_speaker = "Crow T. Robot"
            intro = "Movie sign! Settle in, folks, we're watching whatever questionable nonsense is on this screen today!"
            run_on_main(lambda: self.view.update_riff(intro_speaker, intro))
            speak_riff(intro_speaker, intro)

            last_riff_time = time.time()
            interval = 22.0  # Roast every 22 seconds

            while self.running:
                # Check for on-demand roast signals
                if os.path.exists(COMMAND_FILE):
                    try:
                        with open(COMMAND_FILE, "r") as f:
                            cmd = f.read().strip()
                        os.remove(COMMAND_FILE)
                        if cmd == "roast_now":
                            self.triggerRiff()
                            last_riff_time = time.time()
                        elif cmd == "stop":
                            self.running = False
                            break
                    except Exception:
                        pass

                if time.time() - last_riff_time >= interval:
                    self.triggerRiff()
                    last_riff_time = time.time()

                time.sleep(1.0)

            # Farewell
            farewell_speaker = "Tom Servo"
            farewell = "Push the button, Frank! We are outta here!"
            run_on_main(lambda: self.view.update_riff(farewell_speaker, farewell))
            speak_riff(farewell_speaker, farewell)
            run_on_main(lambda: AppKit.NSApp().terminate_(None))

        threading.Thread(target=loop, daemon=True).start()


def run_overlay():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = MST3KOverlayController.alloc().init()
    controller.setupWindow()
    controller.startRiffLoop()

    app.run()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "roast":
        with open(COMMAND_FILE, "w") as f:
            f.write("roast_now")
        print("Triggered on-demand MST3K riff.")
    elif len(sys.argv) > 1 and sys.argv[1] == "stop":
        with open(COMMAND_FILE, "w") as f:
            f.write("stop")
        print("Stopping MST3K Riffers...")
    else:
        run_overlay()
