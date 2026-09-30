#!/usr/bin/env python3
"""Lightweight macOS Menu Bar status indicator for free-mac-voice.

Reads /tmp/free-voice-state.json and reflects current status:
    🎙️  = Idle / listening for "Mac"
    👂  = Wake word active (listening for command window)
    ⚙️  = Processing command
    💤  = Paused / inactive

Runs standalone:
    python3 menu_bar.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time

STATE_FILE = os.path.join(tempfile.gettempdir(), "free-voice-state.json")
LOG_FILE = "/tmp/free-mac-voice.log"
CONFIG_FILE = os.path.expanduser("~/.free-voice/.env")


def read_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def run_menu_bar() -> None:
    try:
        import AppKit
        import objc
    except ImportError:
        print("menu_bar requires PyObjC / AppKit — install with: pip install pyobjc",
              file=sys.stderr)
        return

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    status_bar = AppKit.NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)

    button = status_item.button()
    button.setTitle_("🎙️ Mac")

    menu = AppKit.NSMenu.alloc().init()

    # Menu items
    item_status = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Status: Starting…", None, ""
    )
    item_status.setEnabled_(False)
    menu.addItem_(item_status)

    item_last = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Last Command: None", None, ""
    )
    item_last.setEnabled_(False)
    menu.addItem_(item_last)

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    class ActionHandler(AppKit.NSObject):
        @objc.typedSelector(b"v@:@")
        def openLog_(self, sender):
            if os.path.exists(LOG_FILE):
                subprocess.Popen(["open", LOG_FILE])
            else:
                subprocess.Popen(["open", tempfile.gettempdir()])

        @objc.typedSelector(b"v@:@")
        def openConfig_(self, sender):
            if os.path.exists(CONFIG_FILE):
                subprocess.Popen(["open", CONFIG_FILE])
            else:
                subprocess.Popen(["open", os.path.expanduser("~/.free-voice")])

        @objc.typedSelector(b"v@:@")
        def testChime_(self, sender):
            subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])

        @objc.typedSelector(b"v@:@")
        def quitApp_(self, sender):
            AppKit.NSApp.terminate_(self)

    handler = ActionHandler.alloc().init()

    item_chime = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Test Wake Chime", "testChime:", ""
    )
    item_chime.setTarget_(handler)
    menu.addItem_(item_chime)

    item_cfg = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open Config (.env)", "openConfig:", ""
    )
    item_cfg.setTarget_(handler)
    menu.addItem_(item_cfg)

    item_log = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "View Log", "openLog:", ""
    )
    item_log.setTarget_(handler)
    menu.addItem_(item_log)

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    item_quit = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit Menu Bar", "quitApp:", "q"
    )
    item_quit.setTarget_(handler)
    menu.addItem_(item_quit)

    status_item.setMenu_(menu)

    class TimerTarget(AppKit.NSObject):
        @objc.typedSelector(b"v@:@")
        def tick_(self, timer):
            st = read_state()
            state = st.get("state", "idle")
            ts = st.get("ts", 0)
            is_stale = (time.time() - ts) > 30

            if is_stale or not st:
                button.setTitle_("🎙️ (idle)")
                item_status.setTitle_("Status: Idle / Not Running")
            elif state == "wake_heard":
                button.setTitle_("👂 Mac")
                item_status.setTitle_("Status: Wake Word Active (Listening)")
            elif state == "processing":
                button.setTitle_("⚙️ Mac")
                item_status.setTitle_("Status: Processing Command")
            elif state == "listening":
                wake = st.get("wake_word", "mac")
                title = f"🎙️ {wake.title()}" if wake else "🎙️ Open"
                button.setTitle_(title)
                item_status.setTitle_(f"Status: Listening for '{wake}'" if wake else "Status: Open Mic")
            else:
                button.setTitle_("🎙️ Mac")
                item_status.setTitle_(f"Status: {state}")

            last_cmd = st.get("command") or st.get("last_command")
            if last_cmd:
                item_last.setTitle_(f"Last Command: {last_cmd[:30]}")

    target = TimerTarget.alloc().init()
    AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.5, target, "tick:", None, True
    )

    app.run()


if __name__ == "__main__":
    run_menu_bar()
