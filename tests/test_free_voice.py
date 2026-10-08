#!/usr/bin/env python3
"""Unit tests for free_voice.py — run anywhere, no mic/model/Mac needed.

    python3 -m unittest discover -s tests -v

Heavy dependencies (sounddevice, pynput, AppKit, xa11y, faster-whisper) are
all imported lazily inside functions, so this suite runs on Linux with stdlib
only. macOS-only paths are exercised via mocks with DRY_RUN enabled.
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import free_voice as fv
import samsung_tv
import menu_bar


class Base(unittest.TestCase):
    def setUp(self):
        # these tests exercise the Ollama/hashing Tier 0.5a path; the ONNX backend has its own tests (test_onnx_embed.py)
        self.addCleanup(setattr, fv, "TIER05_EMBED_BACKEND", fv.TIER05_EMBED_BACKEND)
        fv.TIER05_EMBED_BACKEND = "ollama"
        self._dry = fv.DRY_RUN
        fv.DRY_RUN = True
        self.said = []
        self._say = fv.say
        fv.say = self.said.append  # capture spoken output instead of `say`
        self._ollama_ok = fv._ollama_ok
        fv._ollama_ok = None
        self._gemini_key = fv.GEMINI_API_KEY
        fv.GEMINI_API_KEY = ""
        self._last_error = fv._last_error
        fv._last_error = ""
        self._shot_ts = fv._shot_ts
        fv._shot_ts = 0.0
        self._always = getattr(fv, "ALWAYS_MODE", False)
        fv.ALWAYS_MODE = False
        self._wake_window = fv._wake_window_until
        fv._wake_window_until = 0.0
        self._wake_word = fv.VOICE_WAKE_WORD
        fv.VOICE_WAKE_WORD = "mac"
        self._decision_model = fv.OLLAMA_DECISION_MODEL
        fv.OLLAMA_DECISION_MODEL = ""
        self._decision_ok = fv._decision_ok
        fv._decision_ok = None

    def tearDown(self):
        fv.DRY_RUN = self._dry
        fv.say = self._say
        fv._ollama_ok = self._ollama_ok
        fv.GEMINI_API_KEY = self._gemini_key
        fv._last_error = self._last_error
        fv._shot_ts = self._shot_ts
        fv.ALWAYS_MODE = self._always
        fv._wake_window_until = self._wake_window
        fv.VOICE_WAKE_WORD = self._wake_word
        fv.OLLAMA_DECISION_MODEL = self._decision_model
        fv._decision_ok = self._decision_ok

    def route_name(self, text):
        r = fv.route(text, partial=False)
        self.assertIsNotNone(r, f"no Tier 0 route for {text!r}")
        return r[0], r[1]


# ---------------------------------------------------------------- Tier 0 routing
class TestTier0Routing(Base):
    def test_open_app(self):
        name, _ = self.route_name("open notes")
        self.assertEqual(name, "open_app")

    def test_set_volume(self):
        name, m = self.route_name("set volume to 30")
        self.assertEqual(name, "vol_set")
        self.assertEqual(m.group(2), "30")

    def test_volume_up_down_phrases(self):
        for phrase in ("volume up", "sound up", "turn volume up", "turn the volume up", "turn up the volume", "turn up volume", "louder"):
            name, _ = self.route_name(phrase)
            self.assertEqual(name, "vol_up", phrase)
        for phrase in ("volume down", "sound down", "turn volume down", "turn the volume down", "turn down the volume", "turn down volume", "quieter"):
            name, _ = self.route_name(phrase)
            self.assertEqual(name, "vol_down", phrase)

    def test_describe_screen(self):
        for phrase in ("what's on my screen", "describe the screen",
                       "what am i looking at"):
            name, _ = self.route_name(phrase)
            self.assertEqual(name, "describe_screen", phrase)

    def test_click_button(self):
        name, m = self.route_name("click the Reply button")
        self.assertEqual(name, "click_button")
        self.assertIn("reply", m.group(3).lower())

    def test_dictate_routes_to_type_text(self):
        name, m = self.route_name("dictate dear mom, call me")
        self.assertEqual(name, "type_text")
        self.assertEqual(m.group(1), "dear mom, call me")

    def test_read_clipboard(self):
        for phrase in ("what's on my clipboard", "what's on the clipboard",
                       "read clipboard"):
            name, _ = self.route_name(phrase)
            self.assertEqual(name, "read_clipboard", phrase)

    def test_typing_macros(self):
        self.assertEqual(self.route_name("type today's date")[0], "type_date")
        self.assertEqual(self.route_name("type the time")[0], "type_time")
        self.assertEqual(self.route_name("type my email")[0], "type_email")

    def test_incomplete_partial_does_not_fire(self):
        # "set volume to" is not terminal-complete: never fire mid-sentence
        self.assertIsNone(fv.route("set volume to", partial=True))
        # ...but the finished command routes fine
        self.assertIsNotNone(fv.route("set volume to 30", partial=False))

    def test_phase1_routing(self):
        self.assertEqual(self.route_name("next app")[0], "next_app")
        self.assertEqual(self.route_name("previous app")[0], "prev_app")
        self.assertEqual(self.route_name("next tab")[0], "next_tab")
        self.assertEqual(self.route_name("previous tab")[0], "prev_tab")
        self.assertEqual(self.route_name("type url")[0], "address_bar")
        self.assertEqual(self.route_name("address bar")[0], "address_bar")
        self.assertEqual(self.route_name("search mac")[0], "search_mac")
        self.assertEqual(self.route_name("show all windows")[0], "show_all_windows")
        self.assertEqual(self.route_name("show desktop")[0], "show_desktop")
        self.assertEqual(self.route_name("next window")[0], "next_window")
        self.assertEqual(self.route_name("hide everything else")[0], "hide_others")
        self.assertEqual(self.route_name("private window")[0], "private_window")
        self.assertEqual(self.route_name("bookmark this")[0], "bookmark_this")
        self.assertEqual(self.route_name("paste plain text")[0], "paste_plain")
        self.assertEqual(self.route_name("find next")[0], "find_next")
        self.assertEqual(self.route_name("save as")[0], "save_as")
        self.assertEqual(self.route_name("print this")[0], "print_this")
        self.assertEqual(self.route_name("record screen")[0], "record_screen")

    def test_phase1_aliases(self):
        self.assertEqual(self.route_name("lock it down")[0], "lock")
        self.assertEqual(self.route_name("dim screen")[0], "bright_down")
        self.assertEqual(self.route_name("close this")[0], "close_window")

    def test_phase1_kill_destructive_gate(self):
        name, m = self.route_name("kill safari")
        self.assertEqual(name, "kill_app")
        self.assertEqual(m.group(1), "safari")
        # Ensure kill_app requires confirmation
        called = []
        with mock.patch.object(fv, "act_force_quit_app", lambda a: called.append(a)):
            fv.execute_match("kill_app", m, allow_destructive=False, confirm_audio_fn=None)
            self.assertEqual(called, [])
            self.assertTrue(any("Not force quitting safari without confirmation" in s for s in self.said))
            # With confirmation
            fv.execute_match("kill_app", m, allow_destructive=True)
            self.assertEqual(called, ["safari"])

    def test_phase2_finder_navigation(self):
        self.assertEqual(self.route_name("go to desktop")[0], "finder_desktop")
        self.assertEqual(self.route_name("go to documents")[0], "finder_documents")
        self.assertEqual(self.route_name("go to downloads")[0], "finder_downloads")
        self.assertEqual(self.route_name("go to apps")[0], "finder_apps")
        self.assertEqual(self.route_name("new folder")[0], "new_folder")
        name, m = self.route_name("rename to project notes")
        self.assertEqual(name, "rename_to")
        self.assertEqual(m.group(1), "project notes")
        self.assertEqual(self.route_name("duplicate this")[0], "duplicate_this")
        self.assertEqual(self.route_name("get file info")[0], "get_file_info")
        self.assertEqual(self.route_name("preview this")[0], "preview_this")
        self.assertEqual(self.route_name("trash this")[0], "trash_this")
        self.assertEqual(self.route_name("list view")[0], "list_view")
        self.assertEqual(self.route_name("icon view")[0], "icon_view")
        self.assertEqual(self.route_name("column view")[0], "column_view")
        self.assertEqual(self.route_name("gallery view")[0], "gallery_view")

    def test_partial_gating_and_overlaps(self):
        # close vs close tab vs close all windows vs close this
        self.assertEqual(fv.route("close", partial=True)[0], "close_window")
        self.assertEqual(fv.route("close this", partial=True)[0], "close_window")
        self.assertEqual(fv.route("close tab", partial=True)[0], "close_tab")
        self.assertEqual(fv.route("close all windows", partial=True)[0], "close_all_windows")

        # paste vs paste plain text
        self.assertEqual(fv.route("paste", partial=True)[0], "paste_")
        self.assertEqual(fv.route("paste plain text", partial=True)[0], "paste_plain")

        # rename to is free text: NEVER partial-safe
        self.assertIsNone(fv.route("rename to draft", partial=True))
        self.assertIsNotNone(fv.route("rename to draft", partial=False))

        # kill is destructive: free-text app name, NOT partial-safe
        self.assertIsNone(fv.route("kill safari", partial=True))
        self.assertIsNotNone(fv.route("kill safari", partial=False))


# ------------------------------------------------------- compound chaining (new)
class TestChaining(Base):
    def test_chain_executes_every_part(self):
        calls = []
        with mock.patch.object(fv, "act_open_app", lambda n: calls.append(("open", n))), \
             mock.patch.object(fv, "act_snap_window", lambda s: calls.append(("snap", s))), \
             mock.patch("time.sleep"):
            fv.handle_command("open notes and snap left", quiet_miss=True)
        self.assertIn(("open", "notes"), calls)
        self.assertIn(("snap", "left"), calls)

    def test_chain_then_separator(self):
        calls = []
        with mock.patch.object(fv, "act_open_app", lambda n: calls.append(("open", n))), \
             mock.patch.object(fv, "act_snap_window", lambda s: calls.append(("snap", s))), \
             mock.patch("time.sleep"):
            fv.handle_command("open notes then snap left", quiet_miss=True)
        self.assertIn(("open", "notes"), calls)
        self.assertIn(("snap", "left"), calls)

    def test_and_inside_single_command_is_protected(self):
        searched = []
        with mock.patch.object(fv, "act_web_search", lambda q: searched.append(q)):
            fv.handle_command("search cats and dogs", quiet_miss=True)
        self.assertEqual(searched, ["cats and dogs"])

    def test_dictate_with_and_is_protected(self):
        typed = []
        with mock.patch.object(fv, "act_type_text", lambda t: typed.append(t)):
            fv.handle_command("dictate bread and butter", quiet_miss=True)
        self.assertEqual(typed, ["bread and butter"])

    def test_chain_is_all_or_nothing(self):
        opened = []
        with mock.patch.object(fv, "act_open_app", lambda n: opened.append(n)), \
             mock.patch.object(fv, "ollama_route", return_value=None), \
             mock.patch.object(fv, "ollama_multi_action_plan", return_value=None), \
             mock.patch("time.sleep"):
            # second half matches nothing -> NOTHING runs, not even part one
            fv.handle_command("open notes and zzzqqq nonsense", quiet_miss=True)
        self.assertEqual(opened, [])


# ---------------------------------------------------------------- earcons (new)
class TestEarcons(Base):
    def setUp(self):
        super().setUp()
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)

    def test_tink_on_press(self):
        with mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            fv.play_chime("Tink.aiff")
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0],
                         ["afplay", "/System/Library/Sounds/Tink.aiff"])

    def test_pop_on_release(self):
        with mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            fv.play_chime("Pop.aiff")
        self.assertIn("Pop.aiff", popen.call_args[0][0][1])

    def test_missing_sound_is_silent_no_crash(self):
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch("subprocess.Popen") as popen:
            fv.play_chime("Tink.aiff")  # must not raise
        popen.assert_not_called()


# ------------------------------------------------- clipboard & typing macros (new)
class TestClipboardMacros(Base):
    def test_read_clipboard_speaks_contents(self):
        fake = mock.Mock(stdout="grocery list: eggs")
        with mock.patch("subprocess.run", return_value=fake):
            fv.act_read_clipboard()
        self.assertTrue(any("grocery list: eggs" in s for s in self.said))

    def test_type_date(self):
        typed = []
        with mock.patch.object(fv, "act_type_text", lambda t: typed.append(t)):
            fv.act_type_date()
        self.assertEqual(typed, [datetime.now().strftime("%B %-d, %Y")])

    def test_type_time(self):
        typed = []
        with mock.patch.object(fv, "act_type_text", lambda t: typed.append(t)):
            fv.act_type_time()
        self.assertEqual(typed, [datetime.now().strftime("%-I:%M %p")])

    def test_type_email_when_set(self):
        typed = []
        with mock.patch.object(fv, "VOICE_USER_EMAIL", "test@example.com"), \
             mock.patch.object(fv, "act_type_text", lambda t: typed.append(t)):
            fv.act_type_email()
        self.assertEqual(typed, ["test@example.com"])

    def test_type_email_when_unset(self):
        with mock.patch.object(fv, "VOICE_USER_EMAIL", ""):
            fv.act_type_email()
        self.assertTrue(any("No email" in s for s in self.said))


# ------------------------------------------------------- vision click parsing
class TestVisionClick(Base):
    def _vision(self, model_reply):
        real_import = __import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **k)

        # pass-1 parsing tests run without refinement (PIL blocked)
        with mock.patch("builtins.__import__", side_effect=no_pil), \
             mock.patch.object(fv, "capture_screenshot", return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask", return_value=model_reply):
            return fv.vision_click("the Reply button")

    def test_valid_coordinates_click(self):
        self.assertTrue(self._vision("512 340"))

    def test_none_reply_means_no_click(self):
        self.assertFalse(self._vision("NONE"))

    def test_garbage_reply_means_no_click(self):
        self.assertFalse(self._vision("hello world"))

    def test_out_of_range_rejected(self):
        self.assertFalse(self._vision("1500 340"))
        self.assertFalse(self._vision("512 9999"))

    def test_failed_vision_means_no_click(self):
        self.assertFalse(self._vision(None))


# ------------------------------------------------------- mouse control (new)
class TestMouseControl(Base):
    def test_mouse_to_routing(self):
        name, m = self.route_name("move the mouse to the toggle")
        self.assertEqual(name, "mouse_to")
        self.assertEqual(m.group(3), "toggle")

    def test_mouse_move_routing(self):
        name, m = self.route_name("move mouse up")
        self.assertEqual(name, "mouse_move")
        self.assertEqual(m.group(2), "up")
        self.assertIsNone(m.group(4))
        name, m = self.route_name("move mouse left 200")
        self.assertEqual(name, "mouse_move")
        self.assertEqual(m.group(2), "left")
        self.assertEqual(m.group(4), "200")

    def test_scroll_routing(self):
        name, m = self.route_name("scroll down")
        self.assertEqual(name, "scroll")
        self.assertEqual(m.group(1), "down")
        self.assertIsNone(m.group(3))
        name, m = self.route_name("scroll up 3")
        self.assertEqual(m.group(1), "up")
        self.assertEqual(m.group(3), "3")

    def test_click_here_routing(self):
        self.assertEqual(self.route_name("click")[0], "click_here")
        # longer click commands still route elsewhere
        self.assertEqual(self.route_name("click the Reply button")[0],
                         "click_button")
        self.assertEqual(self.route_name("click the Docs link")[0],
                         "click_link")

    def test_mouse_move_dry_run(self):
        fv.act_mouse_move("up", None)
        self.assertEqual(self.said, ["Moving mouse up"])
        self.said.clear()
        fv.act_mouse_move("left", "200")
        self.assertEqual(self.said, ["Moving mouse left"])

    def test_scroll_dry_run(self):
        fv.act_scroll("down", None)
        self.assertEqual(self.said, ["Scrolling down"])

    def test_click_here_dry_run(self):
        fv.act_click_here()
        self.assertEqual(self.said, ["Clicked"])

    def test_mouse_move_relative(self):
        from contextlib import contextmanager

        @contextmanager
        def fake():
            fv.DRY_RUN = False
            self.addCleanup(setattr, fv, "DRY_RUN", True)
            mouse, button = mock.Mock(), mock.Mock()
            with mock.patch.object(fv, "_mouse",
                                   return_value=(mouse, button)):
                yield mouse
        with fake() as mouse:
            fv.act_mouse_move("up", None)     # default 100px
            mouse.move.assert_called_with(0, -100)
            fv.act_mouse_move("left", "200")
            mouse.move.assert_called_with(-200, 0)
            fv.act_mouse_move("down", "50")
            mouse.move.assert_called_with(0, 50)

    def test_scroll_steps(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mouse, _button = mock.Mock(), mock.Mock()
        with mock.patch.object(fv, "_mouse", return_value=(mouse, None)):
            fv.act_scroll("up", None)     # one base step
            mouse.scroll.assert_called_with(0, 4)
            fv.act_scroll("down", "3")    # multiplied
            mouse.scroll.assert_called_with(0, -12)
            fv.act_scroll("left", "2")
            mouse.scroll.assert_called_with(8, 0)

    def test_click_here_clicks_at_cursor(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mouse, button = mock.Mock(), mock.Mock()
        button.left = "LEFT"
        with mock.patch.object(fv, "_mouse", return_value=(mouse, button)):
            fv.act_click_here()
            mouse.click.assert_called_with("LEFT", 1)
        self.assertEqual(self.said, ["Clicked"])

    def test_mouse_to_moves_to_tree_target(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mouse = mock.Mock()

        class Cfg:  # fake pynput Controller: position is a plain attribute
            pass
        ctl = Cfg()
        with mock.patch.object(fv, "_locate_first", return_value=(100, 200)), \
             mock.patch.object(fv, "_mouse", return_value=(ctl, None)), \
             mock.patch.object(fv, "frontmost_app", return_value="Safari"):
            fv.act_mouse_to("the toggle")
            self.assertEqual(ctl.position, (100, 200))
        self.assertEqual(self.said, ["Mouse is on the toggle"])

    def test_mouse_to_falls_back_to_vision(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)

        class Cfg:
            pass
        ctl = Cfg()
        with mock.patch.object(fv, "_locate_first", return_value=None), \
             mock.patch.object(fv, "vision_locate", return_value=(7, 9)), \
             mock.patch.object(fv, "_mouse", return_value=(ctl, None)), \
             mock.patch.object(fv, "frontmost_app", return_value="Safari"):
            fv.act_mouse_to("the weird widget")
            self.assertEqual(ctl.position, (7, 9))
        self.assertEqual(self.said, ["Mouse is on the weird widget"])

    def test_mouse_to_not_found(self):
        with mock.patch.object(fv, "_locate_first", return_value=None), \
             mock.patch.object(fv, "vision_locate", return_value=None), \
             mock.patch.object(fv, "frontmost_app", return_value="Safari"):
            fv.act_mouse_to("the thing that isn't there")
        self.assertEqual(self.said,
                         ["I couldn't find the thing that isn't there on screen"])

    def test_locate_first_uses_element_center(self):
        from types import SimpleNamespace

        class Bounds:
            x, y, width, height = 10, 20, 100, 40

        class El:
            bounds = Bounds()

        class Locator:
            def elements(self):
                return [El()]

        class App:
            def locator(self, selector):
                self.seen = selector
                return Locator()

        class AppNs:
            def by_name(self, name):
                app = App()
                AppNs.last = app
                return app

        fake_xa = SimpleNamespace(App=AppNs())
        old = fv._xa11y_mod
        fv._xa11y_mod = fake_xa
        self.addCleanup(setattr, fv, "_xa11y_mod", old)
        loc = fv._locate_first("Safari", ["button", "link"], "toggle")
        self.assertEqual(loc, (60, 40))  # center of 10,20,100x40
        self.assertIn("button", AppNs.last.seen)

    def test_vision_locate_returns_pixels(self):
        real_import = __import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=no_pil), \
             mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask", return_value="512 340"):
            # 512/1000*1920=983, 340/1000*1080=367 (no AppKit on Linux)
            self.assertEqual(fv.vision_locate("the toggle"), (983, 367))
        with mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask", return_value="NONE"):
            self.assertIsNone(fv.vision_locate("nope"))


# ------------------------------------------------------- Tier 1 / Ollama
class TestOllama(Base):
    def _fake_urlopen(self, payload_text):
        body = {"message": {"content": payload_text}}
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.__enter__.return_value = resp
        captured = {}

        def fake(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return resp
        return fake, captured

    def test_default_model_is_qwen3_vl_8b(self):
        # The CODE default, independent of this machine's ~/.free-voice/.env or environment (which may select another build):
        # run in a clean subprocess with HOME pointing at an empty folder and OLLAMA_MODEL unset.
        import subprocess, sys, tempfile
        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_MODEL"}
        with tempfile.TemporaryDirectory() as home:
            env["HOME"] = home
            out = subprocess.run([sys.executable, "-c", "import free_voice as f; print(f.OLLAMA_MODEL)"],
                                 cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), env=env,
                                 capture_output=True, text=True, timeout=120).stdout.strip().splitlines()[-1]
        self.assertEqual(out, "qwen3-vl:8b")
        self.assertIn("qwen3-vl", fv.OLLAMA_MODEL)

    def test_payload_uses_think_false_and_long_keepalive(self):
        fake, captured = self._fake_urlopen('{"action": "none", "confidence": 0}')
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            fv.ollama_route("open notes please")
        body = captured["body"]
        self.assertEqual(body["think"], False)
        self.assertEqual(body["keep_alive"], "60m")
        self.assertEqual(body["model"], fv.OLLAMA_MODEL)
        self.assertEqual(body["options"]["num_predict"], 64)
        self.assertEqual(body["options"]["num_ctx"], 1024)

    def test_unreachable_ollama_returns_none(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=ConnectionRefusedError("nope")):
            self.assertIsNone(fv.ollama_route("open notes please"))
        self.assertFalse(fv._ollama_ok)

    def test_thinking_model_uses_prefill_and_reconstructs_json(self):
        # When Ollama returns the suffix following the prefill {"action": "
        fake, captured = self._fake_urlopen('open_app", "params": {"app": "Notes"}, "confidence": 0.95}')
        with mock.patch.object(fv, "OLLAMA_MODEL", "qwen3-vl:8b"), \
             mock.patch("urllib.request.urlopen", side_effect=fake):
            res = fv.ollama_route("could you please open notes")
        self.assertIsNotNone(res)
        action, params, conf = res
        self.assertEqual(action, "open_app")
        self.assertEqual(params, {"app": "Notes"})
        self.assertEqual(conf, 0.95)
        # Check that assistant prefill was sent
        msgs = captured["body"]["messages"]
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertEqual(msgs[-1]["content"], '{"action": "')

    def test_dispatch_tier1_supports_alternate_app_key(self):
        opened = []
        with mock.patch.object(fv, "act_open_app", side_effect=opened.append):
            fv.dispatch_tier1("open_app", {"app_name": "Calculator"})
        self.assertEqual(opened, ["Calculator"])


# ------------------------------------------------------- Tier 2 / Gemini
class TestGemini(Base):
    def _http_error(self, code):
        return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))

    def test_429_falls_back_to_local(self):
        fv.GEMINI_API_KEY = "test-key"
        local = []
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._http_error(429)), \
             mock.patch.object(fv, "ollama_answer",
                               side_effect=lambda p: local.append(p) or True):
            self.assertTrue(fv.gemini_answer("who won last night"))
        self.assertEqual(local, ["who won last night"])
        self.assertTrue(any("free limit" in s for s in self.said))

    def test_google_search_grounding_enabled(self):
        fv.GEMINI_API_KEY = "test-key"
        body = {"candidates": [{"content": {"parts": [{"text": "The Mets won."}]}}]}
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.__enter__.return_value = resp
        captured = {}

        def fake(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertTrue(fv.gemini_answer("who won last night"))
        self.assertEqual(captured["body"]["tools"], [{"google_search": {}}])

    def test_non_429_error_falls_back_to_local(self):
        fv.GEMINI_API_KEY = "test-key"
        local = []
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._http_error(500)), \
             mock.patch.object(fv, "ollama_answer",
                               side_effect=lambda p: local.append(p) or True):
            self.assertTrue(fv.gemini_answer("who won last night"))
        self.assertEqual(local, ["who won last night"])
        self.assertTrue(any("didn't answer" in s for s in self.said))

    def test_generic_network_error_falls_back_to_local(self):
        fv.GEMINI_API_KEY = "test-key"
        local = []
        with mock.patch("urllib.request.urlopen",
                        side_effect=ConnectionError("dns down")), \
             mock.patch.object(fv, "ollama_answer",
                               side_effect=lambda p: local.append(p) or True):
            self.assertTrue(fv.gemini_answer("who won last night"))
        self.assertEqual(local, ["who won last night"])


# ------------------------------------------------------- phonetic matching
class TestPhonetic(Base):
    def test_soundex_matches_similar_names(self):
        self.assertEqual(fv._soundex("Smith"), fv._soundex("Smyth"))

    def test_soundex_distinguishes_different_names(self):
        self.assertNotEqual(fv._soundex("Apple"), fv._soundex("Zebra"))

    def test_phonetic_key_normalizes(self):
        self.assertEqual(fv._phonetic_key("  Hello!! "), fv._phonetic_key("hello"))


# ------------------------------------------------------- safety surface (documents current behavior)
class TestSafetySurface(Base):
    def test_execute_match_accepts_destructive_gate_param(self):
        import inspect
        sig = inspect.signature(fv.execute_match)
        self.assertIn("allow_destructive", sig.parameters)

    def test_confirm_spoken_exists(self):
        self.assertTrue(callable(fv.confirm_spoken))


# ------------------------------------------------- destructive confirmation
class TestDestructiveConfirmation(Base):
    def _exec(self, text, confirm=None, allow_destructive=False):
        name, m = self.route_name(text)
        with mock.patch.object(fv, "confirm_spoken",
                               return_value=confirm) as cs, \
             mock.patch.object(fv, "act_quit_app") as qa, \
             mock.patch.object(fv, "act_close_all_windows") as caw:
            fv.execute_match(name, m, confirm_audio_fn=lambda s: b"",
                             allow_destructive=allow_destructive)
            return cs, qa, caw

    def test_quit_app_runs_directly(self):
        cs, qa, _ = self._exec("quit spotify", confirm=False)
        qa.assert_called_once()
        cs.assert_not_called()

    def test_close_all_windows_runs_without_confirmation(self):
        cs, _, caw = self._exec("close all windows", confirm=False)
        caw.assert_called_once()
        cs.assert_not_called()

    def test_close_windows_runs_without_confirmation(self):
        name, m = self.route_name("close windows")
        self.assertEqual(name, "close_window")
        with mock.patch.object(fv, "confirm_spoken") as cs, \
             mock.patch.object(fv, "act_close_window") as cw:
            fv.execute_match(name, m, confirm_audio_fn=lambda s: b"")
            cw.assert_called_once()
            cs.assert_not_called()

    def test_allow_destructive_skips_prompt(self):
        cs, qa, _ = self._exec("quit spotify", allow_destructive=True)
        qa.assert_called_once()
        cs.assert_not_called()

    def test_tier1_quit_app_runs_directly(self):
        with mock.patch.object(fv, "act_quit_app") as qa:
            fv.dispatch_tier1("quit_app", {"app": "safari"})
        qa.assert_called_once_with("safari")


# ------------------------------------------------- status / health check
class TestStatus(Base):
    def test_status_routes(self):
        for phrase in ("are you working", "are you there", "are you ok",
                       "status", "health check"):
            name, _ = self.route_name(phrase)
            self.assertEqual(name, "status", phrase)

    def test_status_speaks_mic_model_gemini(self):
        with mock.patch.object(fv, "describe_input_device",
                               return_value="iPhone"):
            fv._ollama_ok = True
            fv.act_status()
        spoken = " ".join(self.said)
        self.assertIn("Mic: iPhone", spoken)
        self.assertIn(fv.OLLAMA_MODEL, spoken)
        self.assertIn("No recent errors", spoken)

    def test_status_reports_last_error(self):
        fv._last_error = "14:22 — Ollama unreachable"
        with mock.patch.object(fv, "describe_input_device",
                               return_value="iPhone"):
            fv.act_status()
        self.assertTrue(any("Last error:" in s and "Ollama unreachable" in s
                            for s in self.said))

    def test_note_error_records(self):
        fv.note_error("boom")
        self.assertIn("boom", fv._last_error)


# ------------------------------------------------- model pre-warm
class TestPrewarm(Base):
    def _inline_thread(self):
        created = []

        class InlineThread:
            def __init__(self, target=None, daemon=None, name=None):
                created.append((target, daemon, name))
                self._target = target

            def start(self):
                self._target()

        return mock.patch.object(fv.threading, "Thread", InlineThread), created

    def test_prewarm_sends_tiny_chat_request(self):
        fv.DRY_RUN = False
        captured = {"bodies": []}
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"{}"

        def fake(req, timeout=None):
            captured["bodies"].append(json.loads(req.data.decode()))   # first request is the main model; a later one warms the command gate
            return resp

        patch_thread, created = self._inline_thread()
        try:
            with patch_thread, \
                 mock.patch("urllib.request.urlopen", side_effect=fake):
                fv.prewarm_ollama()
        finally:
            fv.DRY_RUN = True
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0][1])  # daemon
        body = captured["bodies"][0]
        self.assertEqual(body["model"], fv.OLLAMA_MODEL)
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["num_predict"], 1)
        self.assertEqual(body["keep_alive"], "60m")

    def test_prewarm_skipped_in_dry_run(self):
        with self._inline_thread()[0] as _:
            with mock.patch("urllib.request.urlopen") as uo:
                fv.prewarm_ollama()
        uo.assert_not_called()


# ------------------------------------------------- screenshot cache
class TestScreenshotCache(Base):
    def test_second_call_within_ttl_reuses_capture(self):
        fv.DRY_RUN = False
        try:
            with mock.patch.object(fv, "subprocess") as sp, \
                 mock.patch("os.path.exists", return_value=True):
                p1 = fv.capture_screenshot()
                p2 = fv.capture_screenshot()
            self.assertEqual(p1, p2)
            sp.run.assert_called_once()
        finally:
            fv.DRY_RUN = True

    def test_fresh_bypasses_cache(self):
        fv.DRY_RUN = False
        try:
            with mock.patch.object(fv, "subprocess") as sp, \
                 mock.patch("os.path.exists", return_value=True):
                fv.capture_screenshot()
                fv.capture_screenshot(fresh=True)
            self.assertEqual(sp.run.call_count, 2)
        finally:
            fv.DRY_RUN = True


# ------------------------------------------------- two-pass vision click
class TestTwoPassRefine(Base):
    def _fake_pil(self, boxes):
        class FakeCrop:
            def __init__(self, box):
                boxes.append(box)

            def save(self, path):
                pass

        class FakeImage:
            size = (1920, 1080)

            def crop(self, box):
                return FakeCrop(box)

        fake_pil = mock.MagicMock()
        fake_pil.Image.open.return_value = FakeImage()
        return fake_pil

    def test_second_pass_runs_and_maps_back(self):
        boxes = []
        fake_pil = self._fake_pil(boxes)
        replies = ["512 340", "500 500"]  # pass 1, then pass-2 inside crop
        with mock.patch.dict("sys.modules", {"PIL": fake_pil,
                                              "PIL.Image": mock.MagicMock()}), \
             mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask",
                               side_effect=replies) as va:
            self.assertTrue(fv.vision_click("the Reply button"))
        self.assertEqual(va.call_count, 2)
        # pass 1: 512/340 of 1920x1080 -> (983, 367); crop 480px around it
        self.assertEqual(boxes, [(743, 127, 1223, 607)])

    def test_failed_second_pass_keeps_first_guess(self):
        fake_pil = self._fake_pil([])
        with mock.patch.dict("sys.modules", {"PIL": fake_pil,
                                              "PIL.Image": mock.MagicMock()}), \
             mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask",
                               side_effect=["512 340", "NONE"]):
            self.assertTrue(fv.vision_click("the Reply button"))

    def test_missing_pillow_falls_back_to_single_pass(self):
        real_import = __import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=no_pil), \
             mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask",
                               return_value="512 340") as va:
            self.assertTrue(fv.vision_click("the Reply button"))
        va.assert_called_once()


# ------------------------------------------------------- browser, tabs & spaces
class TestBrowserAndSpaces(Base):
    def test_browser_and_tab_routing(self):
        cases = [
            ("new tab", "new_tab"),
            ("open a tab", "new_tab"),
            ("close tab", "close_tab"),
            ("close the tab", "close_tab"),
            ("reopen tab", "reopen_tab"),
            ("undo close tab", "reopen_tab"),
            ("refresh", "refresh_page"),
            ("reload the page", "refresh_page"),
            ("go back", "nav_back"),
            ("back", "nav_back"),
            ("go forward", "nav_forward"),
            ("forward", "nav_forward"),
            ("page down", "page_down"),
            ("page up", "page_up"),
            ("find on page", "find_in_page"),
            ("clear terminal", "clear_terminal"),
        ]
        for phrase, expected in cases:
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            self.assertEqual(r[0], expected, f"Routed {phrase} to {r[0]}, expected {expected}")

    def test_spaces_and_display_routing(self):
        cases = [
            ("next space", "next_space"),
            ("previous space", "prev_space"),
            ("prev space", "prev_space"),
            ("move to next display", "move_next_display"),
            ("move to other screen", "move_next_display"),
        ]
        for phrase, expected in cases:
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            self.assertEqual(r[0], expected, f"Routed {phrase} to {r[0]}, expected {expected}")

    def test_prepare_vision_image_calls_sips(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            opt_path = tmp.name + ".opt.jpg"
            with open(opt_path, "wb") as f:
                f.write(b"fake-jpeg")
            try:
                with mock.patch("subprocess.run") as mock_sub:
                    mock_sub.return_value = mock.MagicMock(returncode=0)
                    opt = fv.prepare_vision_image(tmp.name)
                self.assertTrue(opt.endswith(".opt.jpg"))
            finally:
                if os.path.exists(opt_path):
                    os.unlink(opt_path)

    def test_dispatch_tier1_handles_new_actions(self):
        dispatched = []
        with mock.patch.object(fv, "act_keystroke", lambda k, u="": dispatched.append((k, u))), \
             mock.patch.object(fv, "act_key_code", lambda c, u="": dispatched.append((c, u))), \
             mock.patch.object(fv, "say"):
            fv.dispatch_tier1("new_tab", {})
            fv.dispatch_tier1("refresh_page", {})
            fv.dispatch_tier1("next_space", {})
        self.assertIn(("t", "command down"), dispatched)
        self.assertIn(("r", "command down"), dispatched)
        self.assertIn((124, "control down"), dispatched)


# --------------------------------------------- non-blocking say + vision speed
class TestSayNonBlocking(Base):
    def setUp(self):
        super().setUp()
        self.real_say = self._say  # Base swapped fv.say for a list.append
        fv.DRY_RUN = False
        fv._say_proc = None
        fv.VOICE_TTS_ENGINE = "say"
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        self.addCleanup(setattr, fv, "_say_proc", None)
        self.addCleanup(setattr, fv, "VOICE_TTS_ENGINE", "kokoro")

    def test_default_is_nonblocking(self):
        with mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv.subprocess, "run") as run:
            self.real_say("hello")
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], ["say", "hello"])
            run.assert_not_called()

    def test_blocking_waits(self):
        with mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv.subprocess, "run") as run:
            self.real_say("confirm this", blocking=True)
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0], ["say", "confirm this"])
            popen.assert_not_called()

    def test_new_speech_cuts_off_old(self):
        old = mock.Mock()
        old.poll.return_value = None  # still talking
        fv._say_proc = old
        with mock.patch.object(fv.subprocess, "Popen"):
            self.real_say("next")
            old.terminate.assert_called_once()

    def test_finished_proc_not_terminated(self):
        old = mock.Mock()
        old.poll.return_value = 0  # already done
        fv._say_proc = old
        with mock.patch.object(fv.subprocess, "Popen"):
            self.real_say("next")
            old.terminate.assert_not_called()

    def test_dry_run_stays_silent(self):
        fv.DRY_RUN = True
        with mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv.subprocess, "run") as run:
            self.real_say("hello")
            popen.assert_not_called()
            run.assert_not_called()


class TestKokoroTTS(Base):
    def setUp(self):
        super().setUp()
        self.real_say = self._say
        fv.DRY_RUN = False
        fv._say_proc = None
        fv.VOICE_TTS_ENGINE = "kokoro"
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        self.addCleanup(setattr, fv, "_say_proc", None)
        self.addCleanup(setattr, fv, "VOICE_TTS_ENGINE", "kokoro")

    def test_kokoro_plays_via_afplay_nonblocking(self):
        with mock.patch.object(fv, "_synthesize_kokoro", return_value="/tmp/mock.wav") as synth, \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv.subprocess, "run") as run:
            self.real_say("hello world")
            synth.assert_called_once_with("hello world", voice=None)
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], ["afplay", "/tmp/mock.wav"])
            run.assert_not_called()

    def test_kokoro_plays_via_afplay_blocking(self):
        with mock.patch.object(fv, "_synthesize_kokoro", return_value="/tmp/mock.wav") as synth, \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv.subprocess, "run") as run:
            self.real_say("confirm this", blocking=True)
            synth.assert_called_once_with("confirm this", voice=None)
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0], ["afplay", "/tmp/mock.wav"])
            popen.assert_not_called()

    def test_kokoro_fallback_to_say_when_failed(self):
        with mock.patch.object(fv, "_synthesize_kokoro", return_value=None), \
             mock.patch.object(fv.subprocess, "Popen") as popen:
            self.real_say("fallback phrase")
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], ["say", "fallback phrase"])


class TestVoicePicker(Base):
    def test_pick_voice_triggers_and_rotates(self):
        for phrase in ("pick a voice", "choose a voice", "sample voices", "test voices", "rotate voices"):
            matched = fv.route(phrase)
            self.assertIsNotNone(matched, f"Failed to route {phrase}")
            self.assertEqual(matched[0], "pick_voice")

        spoken = []
        with mock.patch.object(fv, "_get_kokoro", return_value=mock.Mock()), \
             mock.patch.object(fv, "say", side_effect=lambda text, **kw: spoken.append((text, kw.get("voice")))):
            fv.act_pick_voice()

        self.assertTrue(len(spoken) >= 5)
        voice_samples = [s[0] for s in spoken if "This is what I sound like on your Mac." in s[0]]
        self.assertTrue(any(s.startswith("Fenrir. ") for s in voice_samples))
        self.assertTrue(any(s.startswith("Heart. ") for s in voice_samples))
        self.assertTrue(any(s.startswith("Adam. ") for s in voice_samples))

    def test_set_voice_and_persistence(self):
        for query in ("use voice adam", "set voice to heart", "change voice to sarah"):
            matched = fv.route(query)
            self.assertIsNotNone(matched, f"Failed to route {query}")
            self.assertEqual(matched[0], "set_voice")

        with tempfile.TemporaryDirectory() as td:
            fake_cfg = os.path.join(td, "config.json")
            with mock.patch.object(fv, "_CONFIG_FILE", fake_cfg), \
                 mock.patch.object(fv, "_config_cache", None), \
                 mock.patch.object(fv, "say") as mock_say:
                fv.act_set_voice("adam")
                self.assertEqual(fv.VOICE_KOKORO_VOICE, "am_adam")
                mock_say.assert_called_with("Voice set to Adam.", blocking=True, voice="am_adam")

                with open(fake_cfg) as f:
                    saved = json.load(f)
                self.assertEqual(saved.get("kokoro_voice"), "am_adam")

    def test_get_voice(self):
        for query in ("what is my voice", "what's the voice"):
            matched = fv.route(query)
            self.assertIsNotNone(matched, f"Failed to route {query}")
            self.assertEqual(matched[0], "get_voice")
        with mock.patch.object(fv, "say") as mock_say:
            fv.act_get_voice()
            mock_say.assert_called_once()


class TestVisionPrefill(Base):
    def _ask(self, **kw):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"message": {"content": "a test screen"}}).encode()

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return Resp()

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(b"fakepng")
            tmp.flush()
            with mock.patch("urllib.request.urlopen",
                            side_effect=fake_urlopen):
                out = fv.vision_ask("q?", tmp.name, **kw)
        return out, captured["body"]

    def test_prefill_adds_assistant_message(self):
        out, body = self._ask(prefill="The screen shows ")
        self.assertEqual(out, "a test screen")
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][1],
                         {"role": "assistant", "content": "The screen shows "})

    def test_no_prefill_single_message(self):
        _out, body = self._ask()
        self.assertEqual(len(body["messages"]), 1)

    def test_num_ctx_4096(self):
        _out, body = self._ask()
        self.assertEqual(body["options"]["num_ctx"], 4096)

    def test_default_downscale_is_800px(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(b"fakepng")
            tmp.flush()
            opt = tmp.name + ".opt.jpg"
            self.assertFalse(os.path.exists(opt))
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.MagicMock(returncode=1)
                # returncode != 0 -> falls back to the original path
                self.assertEqual(fv.prepare_vision_image(tmp.name), tmp.name)
            args = mock_run.call_args[0][0]
            self.assertIn("sips", args[0])
            self.assertIn("800", args)


# --------------------------------------------- adaptive vision refinement
class TestAdaptiveRefinement(Base):
    def _locate(self, reply):
        with mock.patch.object(fv, "capture_screenshot",
                               return_value="/tmp/fake.png"), \
             mock.patch.object(fv, "vision_ask",
                               return_value=reply) as va, \
             mock.patch.object(fv, "_refine_click",
                               return_value=(1, 2)) as rc:
            return fv.vision_locate("the toggle"), va, rc

    def test_large_target_skips_second_inference(self):
        # 300/1000*1920 = 576px >= 480 crop: first-pass center is enough
        loc, va, rc = self._locate("512 340 300")
        self.assertEqual(loc, (983, 367))  # 512/1000*1920, 340/1000*1080
        va.assert_called_once()
        rc.assert_not_called()

    def test_small_target_still_refines(self):
        # 40/1000*1920 = 77px: precision matters, refinement runs
        loc, va, rc = self._locate("512 340 40")
        self.assertEqual(loc, (1, 2))
        va.assert_called_once()
        rc.assert_called_once()

    def test_legacy_two_int_reply_refines(self):
        # old format carries no size: conservative, refine as before
        loc, va, rc = self._locate("512 340")
        self.assertEqual(loc, (1, 2))
        rc.assert_called_once()

    def test_boundary_size_refines(self):
        # 249/1000*1920 = 478px < 480: just under the threshold
        _loc, _va, rc = self._locate("512 340 249")
        rc.assert_called_once()

    def test_none_still_none(self):
        loc, _va, rc = self._locate("NONE")
        self.assertIsNone(loc)
        rc.assert_not_called()


# --------------------------------- dictation, macros, fun stuff
class TestDictation(Base):
    def _dictate(self, chunks):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        with mock.patch.object(fv, "record_fixed", return_value=b"x"), \
             mock.patch.object(fv, "transcribe", side_effect=chunks), \
             mock.patch.object(fv, "act_type_text") as tt:
            fv.act_dictate_start()
        return tt

    def test_chunks_typed_until_stop_phrase(self):
        tt = self._dictate(["hello world", "stop dictating"])
        tt.assert_called_once_with("hello world ")
        self.assertIn("Dictating. Say stop dictating when you're done.", self.said)
        self.assertIn("Done dictating", self.said)

    def test_stop_mid_sentence_keeps_dictating(self):
        tt = self._dictate(["i told him to stop calling", "stop"])
        self.assertEqual([c.args[0] for c in tt.call_args_list],
                         ["i told him to stop calling "])
        self.assertIn("Done dictating", self.said)

    def test_trailing_stop_phrase_types_head(self):
        tt = self._dictate(["see you tomorrow stop dictating"])
        tt.assert_called_once_with("see you tomorrow ")
        self.assertIn("Done dictating", self.said)

    def test_dry_run_says_mode_only(self):
        with mock.patch.object(fv, "record_fixed") as rf:
            fv.act_dictate_start()
        rf.assert_not_called()
        self.assertEqual(self.said, ["Dictation mode"])

    def test_routing(self):
        for text in ("start dictating", "dictate", "take notes",
                     "take notes until i say stop"):
            name, _ = self.route_name(text)
            self.assertEqual(name, "dictate_start", text)
        # one-shot "dictate X" still types
        name, m = self.route_name("dictate hello world")
        self.assertEqual(name, "type_text")
        self.assertEqual(m.group(1), "hello world")


class TestMacros(Base):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._old_file = fv._MACRO_FILE
        fv._MACRO_FILE = os.path.join(self._tmp, "macros.json")
        self._old_cache = fv._macros_cache
        fv._macros_cache = None
        self.addCleanup(setattr, fv, "_MACRO_FILE", self._old_file)
        self.addCleanup(setattr, fv, "_macros_cache", self._old_cache)

    def test_add_list_delete(self):
        fv.act_macro_add("standup", "open slack and open zoom")
        with open(fv._MACRO_FILE) as f:
            saved = json.load(f)
        self.assertEqual(saved, {"standup": ["open slack", "open zoom"]})
        self.assertIn("saved with 2 commands", self.said[-1])
        fv.act_macro_list()
        self.assertIn("standup", self.said[-1])
        fv.act_macro_delete("standup")
        self.assertEqual(fv._load_macros(), {})
        self.assertTrue(any("Deleted" in s and "standup" in s for s in self.said))
        fv.act_macro_delete("standup")
        self.assertTrue(any("No shortcut" in s or "No macro" in s for s in self.said))

    def test_add_splits_on_then_and_commas(self):
        fv.act_macro_add("morning", "open notes then open calendar, open mail")
        self.assertEqual(fv._load_macros()["morning"],
                         ["open notes", "open calendar", "open mail"])

    def test_run_macro_executes_parts(self):
        fv.act_macro_add("standup", "open notes")
        with mock.patch.object(fv, "act_open_app") as oa:
            handled = fv.handle_command("standup")
        self.assertTrue(handled)
        oa.assert_called_once_with("notes")
        self.assertTrue(any("done" in s for s in self.said))

    def test_run_macro_executes_custom_shell_command(self):
        fv.act_macro_add("backup", "bash ~/backup.sh", allow_shell=True)
        with mock.patch.object(fv, "shell") as mock_sh:
            handled = fv.handle_command("backup")
        self.assertTrue(handled)
        mock_sh.assert_called_once_with(["/bin/bash", "-c", "~/backup.sh"])

    def test_voice_macro_blocks_shell_command_for_security(self):
        fv.act_macro_add("malicious", "shell rm -rf /")
        self.assertNotIn("malicious", fv._load_macros())
        self.assertTrue(any("Voice shortcuts cannot execute shell scripts" in s for s in self.said))


    def test_builtin_beats_macro_trigger(self):
        # a macro named exactly like a built-in never hijacks it
        fv.act_macro_add("open notes", "tell me a joke")
        with mock.patch.object(fv, "act_open_app") as oa:
            fv.handle_command("open notes")
        oa.assert_called_once()
        self.assertNotIn("Shortcut done", self.said)

    def test_unknown_trigger_misses(self):
        self.assertFalse(fv.run_macro("nope"))

    def test_routing(self):
        for phrase, (exp_t, exp_c) in [
            ("macro standup runs open slack and open zoom", ("standup", "open slack and open zoom")),
            ("when I say party mode, set volume to 80 and play some jazz", ("party mode", "set volume to 80 and play some jazz")),
            ("when I say chill out do turn down the volume", ("chill out", "turn down the volume")),
            ("alias surf to open safari", ("surf", "open safari")),
            ("add shortcut bedtime runs turn off the tv", ("bedtime", "turn off the tv")),
        ]:
            name, m = self.route_name(phrase)
            self.assertEqual(name, "macro_add", f"Failed on: {phrase}")
            self.assertEqual(m.group(1).strip().lower(), exp_t.lower())

        for phrase in ("list macros", "show shortcuts", "what are my shortcuts", "my macros"):
            self.assertEqual(self.route_name(phrase)[0], "macro_list")

        for phrase in ("delete macro standup", "remove shortcut party mode", "forget shortcut surf"):
            self.assertEqual(self.route_name(phrase)[0], "macro_delete")

    def test_custom_python_extensions_hook(self):
        called = []
        fv.register_custom_command(r"^custom ping$", lambda m: called.append("pong"))
        r = fv.route("custom ping")
        self.assertIsNotNone(r)
        fv.execute_match(r[0], r[1])
        self.assertEqual(called, ["pong"])


class TestFunStuff(Base):
    def test_dice_and_coin_in_range(self):
        for _ in range(10):
            fv.act_roll_dice()
            n = int(self.said[-1].rsplit(" ", 1)[1])
            self.assertIn(n, range(1, 7))
        for _ in range(10):
            fv.act_coin_flip()
            self.assertIn(self.said[-1], ("It's heads", "It's tails"))

    def test_8ball_and_joke_come_from_lists(self):
        fv.act_8ball()
        self.assertIn(self.said[-1], fv._8BALL)
        fv.act_joke()
        self.assertIn(self.said[-1], fv._JOKES)

    def test_ascii_art_renders_and_opens(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed in current environment")
        img_path = os.path.join(tempfile.gettempdir(), "t-ascii.png")
        Image.new("RGB", (64, 32), "white").save(img_path)
        with mock.patch.object(fv, "capture_screenshot", return_value=img_path), \
             mock.patch.object(fv, "shell") as sh:
            fv.act_ascii_art()
        out_txt = os.path.join(tempfile.gettempdir(), "ascii-art.txt")
        with open(out_txt) as f:
            lines = f.read().splitlines()
        self.assertTrue(lines and all(len(l) == 120 for l in lines))

        out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
        self.assertTrue(os.path.exists(out_html))
        with open(out_html) as f:
            html_content = f.read()
        self.assertIn("<pre>", html_content)
        self.assertIn("</pre>", html_content)
        self.assertIn("ui-monospace", html_content)
        # Opened path must be the .html file, NOT the .txt file
        sh.assert_called_once_with(["open", out_html])
        self.assertNotEqual(sh.call_args[0][0], ["open", out_txt])
        self.assertIn("ASCII art", self.said[-1])

    def test_ascii_art_html_escapes_entities(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed in current environment")
        img_path = os.path.join(tempfile.gettempdir(), "t-ascii-entities.png")
        Image.new("RGB", (64, 32), "black").save(img_path)
        # Verify html.escape is applied if art contains characters like <, >, &
        with mock.patch.object(fv, "capture_screenshot", return_value=img_path), \
             mock.patch.object(fv, "_ASCII_RAMP", "<>&*+=-:. "), \
             mock.patch.object(fv, "shell") as sh:
            fv.act_ascii_art()
        out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
        with open(out_html) as f:
            html_content = f.read()
        self.assertIn("&lt;", html_content)
        self.assertNotIn("<pre><", html_content)
        sh.assert_called_once_with(["open", out_html])

    def test_ascii_art_needs_pillow(self):
        real_import = __import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=no_pil), \
             mock.patch.object(fv, "capture_screenshot", return_value="/tmp/x.png"):
            fv.act_ascii_art()
        self.assertIn("Pillow", self.said[-1])

    def test_draw_ascii_flower_routing_and_execution(self):
        # Routing tests
        name, m = self.route_name("draw ascii flower")
        self.assertEqual(name, "draw_ascii")
        self.assertEqual(m.group(m.lastindex), "flower")

        name, m = self.route_name("ascii draw flower")
        self.assertEqual(name, "draw_ascii")
        self.assertEqual(m.group(m.lastindex), "flower")

        name, m = self.route_name("draw a flower in ascii")
        self.assertEqual(name, "draw_ascii")
        self.assertEqual(m.group(m.lastindex), "flower")

        # Execution test: canonical flower
        with mock.patch.object(fv, "shell") as sh:
            fv.act_draw_ascii("flower")
        out_txt = os.path.join(tempfile.gettempdir(), "ascii-art.txt")
        self.assertTrue(os.path.exists(out_txt))
        with open(out_txt) as f:
            txt_content = f.read()
        self.assertIn("@", txt_content)
        self.assertIn("|", txt_content)

        out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
        self.assertTrue(os.path.exists(out_html))
        with open(out_html) as f:
            html_content = f.read()
        self.assertIn("<pre>", html_content)
        self.assertIn("ui-monospace", html_content)
        sh.assert_called_once_with(["open", out_html])
        self.assertIn("ASCII flower", self.said[-1])

    def test_draw_ascii_novel_subject_calls_llm(self):
        with mock.patch.object(fv, "_llm_text", return_value="```\n<dragon>\n```") as lt, \
             mock.patch.object(fv, "shell") as sh:
            fv.act_draw_ascii("dragon")
        lt.assert_called_once()
        out_html = os.path.join(tempfile.gettempdir(), "ascii-art.html")
        with open(out_html) as f:
            self.assertIn("&lt;dragon&gt;", f.read())
        sh.assert_called_once_with(["open", out_html])

    def test_draw_svg_opens_generated_svg(self):
        with mock.patch.object(fv, "_llm_text",
                               return_value="sure: <svg viewBox='0 0 400 400'></svg>"), \
             mock.patch.object(fv, "shell") as sh:
            fv.act_draw_svg("a cat")
        out = os.path.join(tempfile.gettempdir(), "drawing.svg")
        with open(out) as f:
            self.assertTrue(f.read().startswith("<svg"))
        sh.assert_called_once_with(["open", out])
        self.assertIn("Here's your a cat", self.said[-1])

    def test_draw_svg_failure_is_spoken(self):
        with mock.patch.object(fv, "_llm_text", return_value=None):
            fv.act_draw_svg("a cat")
        self.assertIn("couldn't draw", self.said[-1])

    def test_llm_text_prefers_draw_model(self):
        called_models = []
        def fake_urlopen(req, timeout=45):
            body = json.loads(req.data.decode())
            called_models.append(body["model"])
            resp = mock.MagicMock()
            resp.read.return_value = json.dumps({"message": {"content": "<svg></svg>"}}).encode()
            resp.__enter__.return_value = resp
            return resp

        with mock.patch.object(fv, "OLLAMA_DRAW_MODEL", "qwen2.5:1.5b"), \
             mock.patch.object(fv, "OLLAMA_MODEL", "qwen3-vl:8b"), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = fv._llm_text("draw a cat")
            self.assertEqual(res, "<svg></svg>")
            self.assertEqual(called_models[0], "qwen2.5:1.5b")

    def test_reminder_schedules_thread(self):
        with mock.patch("threading.Thread") as th:
            fv.act_reminder(10, "minutes", "check the oven")
        th.assert_called_once()
        self.assertTrue(th.return_value.start.called)
        self.assertIn("remind you to check the oven in 10 minutes", self.said[-1])

    def test_read_screen_speaks_description(self):
        with mock.patch.object(fv, "capture_screenshot", return_value="/tmp/x.png"), \
             mock.patch.object(fv, "vision_ask", return_value="The screen shows Notes."):
            fv.act_read_screen()
        self.assertIn("The screen shows Notes.", self.said)

    def test_play_genre(self):
        with mock.patch.object(fv, "shell", return_value="") as sh:
            fv.act_play_genre("jazz")
        self.assertIn("osascript", sh.call_args.args[0])
        self.assertIn("Playing jazz", self.said[-1])

    def test_play_genre_missing_playlist(self):
        with mock.patch.object(fv, "shell", side_effect=RuntimeError("nope")):
            fv.act_play_genre("jazz")
        self.assertIn("couldn't find a jazz playlist", self.said[-1])

    def test_routing(self):
        routes = {
            "turn my screen into ascii art": "ascii_art",
            "ascii art": "ascii_art",
            "draw a cat": "draw_svg",
            "draw me a house": "draw_svg",
            "roll a die": "roll_dice",
            "roll dice": "roll_dice",
            "flip a coin": "coin_flip",
            "ask the magic 8 ball will it rain": "eight_ball",
            "magic 8 ball": "eight_ball_bare",
            "tell me a joke": "joke",
            "remind me in 10 minutes to check the oven": "reminder",
            "read my screen to me": "read_screen",
            "read my screen": "read_screen",
            "play some jazz": "play_genre",
            "what's in this window": "describe_window_visual",
            "what color is this": "describe_window_visual",
        }
        for text, want in routes.items():
            name, _ = self.route_name(text)
            self.assertEqual(name, want, text)


# ------------------------------------------------------- Apple Vision OCR & locate fallback
class TestAppleVisionOCR(Base):
    def test_clean_target_variants(self):
        v = fv._clean_target_variants("the Reply button")
        self.assertIn("reply", v)
        self.assertIn("reply button", v)

    def test_ocr_locate_missing_framework(self):
        with mock.patch.object(fv, "_load_vision_framework", return_value=(None, None)):
            self.assertIsNone(fv.ocr_locate("Reply", "/tmp/nonexistent.png"))

    def test_ocr_locate_missing_file(self):
        with mock.patch.object(fv, "_load_vision_framework", return_value=(mock.MagicMock(), mock.MagicMock())):
            self.assertIsNone(fv.ocr_locate("Reply", "/tmp/does_not_exist_xyz.png"))

    def test_ocr_locate_exact_match(self):
        fake_cand = mock.MagicMock()
        fake_cand.string.return_value = "Reply"
        fake_cand.confidence.return_value = 1.0

        fake_bbox = mock.MagicMock()
        fake_bbox.origin.x = 0.5
        fake_bbox.origin.y = 0.5
        fake_bbox.size.width = 0.1
        fake_bbox.size.height = 0.05

        fake_obs = mock.MagicMock()
        fake_obs.topCandidates_.return_value = [fake_cand]
        fake_obs.boundingBox.return_value = fake_bbox

        fake_req = mock.MagicMock()
        fake_req.results.return_value = [fake_obs]

        fake_handler = mock.MagicMock()
        fake_handler.performRequests_error_.return_value = (True, None)

        fake_vision = mock.MagicMock()
        fake_vision.VNRecognizeTextRequest.alloc().init.return_value = fake_req
        fake_vision.VNImageRequestHandler.alloc().initWithURL_options_.return_value = fake_handler

        fake_nsurl = mock.MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp, \
             mock.patch.object(fv, "_load_vision_framework", return_value=(fake_vision, fake_nsurl)):
            res = fv.ocr_locate("the Reply button", tmp.name)
            self.assertIsNotNone(res)
            self.assertIsInstance(res, tuple)
            x, y = res
            self.assertTrue(0 <= x <= 1920)
            self.assertTrue(0 <= y <= 1080)

    def test_ocr_locate_ambiguous(self):
        def make_obs(x, y):
            cand = mock.MagicMock()
            cand.string.return_value = "Delete"
            bbox = mock.MagicMock()
            bbox.origin.x = x
            bbox.origin.y = y
            bbox.size.width = 0.05
            bbox.size.height = 0.02
            obs = mock.MagicMock()
            obs.topCandidates_.return_value = [cand]
            obs.boundingBox.return_value = bbox
            return obs

        obs1 = make_obs(0.1, 0.2)
        obs2 = make_obs(0.8, 0.8)

        fake_req = mock.MagicMock()
        fake_req.results.return_value = [obs1, obs2]
        fake_handler = mock.MagicMock()
        fake_handler.performRequests_error_.return_value = (True, None)

        fake_vision = mock.MagicMock()
        fake_vision.VNRecognizeTextRequest.alloc().init.return_value = fake_req
        fake_vision.VNImageRequestHandler.alloc().initWithURL_options_.return_value = fake_handler

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp, \
             mock.patch.object(fv, "_load_vision_framework", return_value=(fake_vision, mock.MagicMock())):
            res = fv.ocr_locate("Delete", tmp.name)
            self.assertEqual(res, "ambiguous")
            self.assertTrue(any("multiple items matching" in s for s in self.said))

    def test_ocr_locate_icon_falls_through_to_none(self):
        cand = mock.MagicMock()
        cand.string.return_value = "Dashboard Settings"
        bbox = mock.MagicMock()
        bbox.origin.x = 0.5
        bbox.origin.y = 0.5
        bbox.size.width = 0.1
        bbox.size.height = 0.05
        obs = mock.MagicMock()
        obs.topCandidates_.return_value = [cand]
        obs.boundingBox.return_value = bbox

        fake_req = mock.MagicMock()
        fake_req.results.return_value = [obs]
        fake_handler = mock.MagicMock()
        fake_handler.performRequests_error_.return_value = (True, None)

        fake_vision = mock.MagicMock()
        fake_vision.VNRecognizeTextRequest.alloc().init.return_value = fake_req
        fake_vision.VNImageRequestHandler.alloc().initWithURL_options_.return_value = fake_handler

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp, \
             mock.patch.object(fv, "_load_vision_framework", return_value=(fake_vision, mock.MagicMock())):
            res = fv.ocr_locate("red circle", tmp.name)
            self.assertIsNone(res)

    def test_press_first_hits_ocr_before_vlm(self):
        with mock.patch.object(fv, "_load_xa11y", return_value=mock.MagicMock(App=mock.MagicMock(by_name=lambda n: mock.MagicMock(locator=lambda s: mock.MagicMock(elements=lambda: []))))), \
             mock.patch.object(fv, "ocr_locate", return_value=(500, 300)) as mock_ocr, \
             mock.patch.object(fv, "vision_click") as mock_vlm:
            fv._press_first("Notes", ["button"], "Reply")
            mock_ocr.assert_called_once_with("Reply")
            mock_vlm.assert_not_called()

    def test_press_first_falls_through_to_vlm_when_ocr_misses(self):
        with mock.patch.object(fv, "_load_xa11y", return_value=mock.MagicMock(App=mock.MagicMock(by_name=lambda n: mock.MagicMock(locator=lambda s: mock.MagicMock(elements=lambda: []))))), \
             mock.patch.object(fv, "ocr_locate", return_value=None) as mock_ocr, \
             mock.patch.object(fv, "vision_click", return_value=True) as mock_vlm:
            fv._press_first("Notes", ["button"], "red circle")
            mock_ocr.assert_called_once_with("red circle")
            mock_vlm.assert_called_once_with("red circle")


# ------------------------------------------------------- Quartz screen description
class TestQuartzScreenDescription(Base):
    def test_quartz_window_summary_formatting(self):
        fake_windows = [
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Notes", "kCGWindowName": "Grocery List"},
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 1200, "Height": 800},
             "kCGWindowOwnerName": "Safari", "kCGWindowName": "Apple Developer"},
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 50, "Height": 50},
             "kCGWindowOwnerName": "Dock", "kCGWindowName": ""},
        ]
        fake_quartz = mock.MagicMock()
        fake_quartz.kCGWindowLayer = "kCGWindowLayer"
        fake_quartz.kCGWindowBounds = "kCGWindowBounds"
        fake_quartz.kCGWindowOwnerName = "kCGWindowOwnerName"
        fake_quartz.kCGWindowName = "kCGWindowName"
        fake_quartz.CGWindowListCopyWindowInfo.return_value = fake_windows

        with mock.patch.object(fv, "_load_quartz", return_value=fake_quartz):
            summary = fv.quartz_window_summary()
            self.assertIsNotNone(summary)
            self.assertIn("Notes (Grocery List)", summary)
            self.assertIn("Safari (Apple Developer)", summary)

    def test_quartz_window_summary_pluralization(self):
        # 1 front app + 3 behind apps => rem = 1 => '1 other app'
        fake_windows_3_behind = [
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Notes", "kCGWindowName": "List"},
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Safari", "kCGWindowName": "Web"},
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Terminal", "kCGWindowName": "zsh"},
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Music", "kCGWindowName": "Music"},
        ]
        # 1 front app + 4 behind apps => rem = 2 => '2 other apps'
        fake_windows_4_behind = fake_windows_3_behind + [
            {"kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 800, "Height": 600},
             "kCGWindowOwnerName": "Mail", "kCGWindowName": "Inbox"},
        ]
        fake_quartz = mock.MagicMock()
        fake_quartz.kCGWindowLayer = "kCGWindowLayer"
        fake_quartz.kCGWindowBounds = "kCGWindowBounds"
        fake_quartz.kCGWindowOwnerName = "kCGWindowOwnerName"
        fake_quartz.kCGWindowName = "kCGWindowName"

        with mock.patch.object(fv, "_load_quartz", return_value=fake_quartz):
            fake_quartz.CGWindowListCopyWindowInfo.return_value = fake_windows_3_behind
            s1 = fv.quartz_window_summary()
            self.assertIn("1 other app", s1)
            self.assertNotIn("app(s)", s1)

            fake_quartz.CGWindowListCopyWindowInfo.return_value = fake_windows_4_behind
            s2 = fv.quartz_window_summary()
            self.assertIn("2 other apps", s2)
            self.assertNotIn("app(s)", s2)

    def test_quartz_window_summary_none_when_empty(self):
        fake_quartz = mock.MagicMock()
        fake_quartz.CGWindowListCopyWindowInfo.return_value = []
        with mock.patch.object(fv, "_load_quartz", return_value=fake_quartz):
            self.assertIsNone(fv.quartz_window_summary())

    def test_quartz_window_summary_missing_framework(self):
        with mock.patch.object(fv, "_load_quartz", return_value=None):
            self.assertIsNone(fv.quartz_window_summary())

    def test_describe_screen_uses_quartz_first(self):
        with mock.patch.object(fv, "quartz_window_summary", return_value="In front is Notes.") as mock_q, \
             mock.patch.object(fv, "vision_ask") as mock_vlm:
            fv.act_describe_screen()
            mock_q.assert_called_once()
            mock_vlm.assert_not_called()
            self.assertIn("In front is Notes.", self.said)

    def test_describe_screen_falls_back_to_vlm_if_quartz_none(self):
        with mock.patch.object(fv, "quartz_window_summary", return_value=None) as mock_q, \
             mock.patch.object(fv, "capture_screenshot", return_value="/tmp/test.png"), \
             mock.patch.object(fv, "vision_ask", return_value="Terminal open.") as mock_vlm:
            fv.act_describe_screen()
            mock_q.assert_called_once()
            mock_vlm.assert_called_once()
            self.assertIn("The screen shows Terminal open.", self.said)

    def test_visual_content_bypasses_quartz(self):
        with mock.patch.object(fv, "quartz_window_summary") as mock_q, \
             mock.patch.object(fv, "capture_screenshot", return_value="/tmp/test.png"), \
             mock.patch.object(fv, "vision_ask", return_value="A red banner."):
            fv.act_describe_screen(visual_content_only=True)
            mock_q.assert_not_called()
            self.assertIn("The screen shows A red banner.", self.said)


# ------------------------------------------------------- Vision ThreadPool & 800px describe
class TestVisionThreadPoolAndSizing(Base):
    def test_always_mode_shunts_describe_to_pool(self):
        fv.ALWAYS_MODE = True
        with mock.patch.object(fv, "capture_screenshot", return_value="/tmp/test.png"), \
             mock.patch.object(fv, "vision_ask", return_value="Safari is open."):
            fv.act_describe_screen_vlm()
            self.assertIn("Looking...", self.said)

    def test_always_mode_shunts_vision_click_to_pool(self):
        fv.ALWAYS_MODE = True
        with mock.patch.object(fv, "vision_locate", return_value=(100, 200)):
            self.assertTrue(fv.vision_click("toggle"))
            self.assertIn("Looking...", self.said)

    def test_describe_uses_800px(self):
        dims = []
        def fake_ask(q, path, prefill=None, max_dimension=800):
            dims.append(max_dimension)
            return "desk"

        with mock.patch.object(fv, "capture_screenshot", return_value="/tmp/test.png"), \
             mock.patch.object(fv, "vision_ask", side_effect=fake_ask):
            fv._describe_screen_vlm_task()
            fv._read_screen_task()
        self.assertEqual(dims, [800, 800])

    def test_locate_keeps_800px(self):
        dims = []
        def fake_ask(q, path, prefill=None, max_dimension=800):
            dims.append(max_dimension)
            return "500 500 50"

        with mock.patch.object(fv, "capture_screenshot", return_value="/tmp/test.png"), \
             mock.patch.object(fv, "vision_ask", side_effect=fake_ask), \
             mock.patch.object(fv, "_refine_click", return_value=(500, 500)):
            fv.vision_locate("button")
        self.assertTrue(all(d == 800 for d in dims))
        self.assertGreaterEqual(len(dims), 1)

    def test_tier0_click_routing_and_extraction(self):
        # Buttons
        name, m = self.route_name("click on the save button")
        self.assertEqual(name, "click_button")
        self.assertEqual(m.group(3), "save")

        name, m = self.route_name("press the cancel button")
        self.assertEqual(name, "click_button")
        self.assertEqual(m.group(3), "cancel")

        name, m = self.route_name("tap the submit button")
        self.assertEqual(name, "click_button")
        self.assertEqual(m.group(3), "submit")

        name, m = self.route_name("hit the reply button")
        self.assertEqual(name, "click_button")
        self.assertEqual(m.group(3), "reply")

        # Links
        name, m = self.route_name("click on the Docs link")
        self.assertEqual(name, "click_link")
        self.assertEqual(m.group(2), "Docs")

        # Clicks anywhere / buttons / items
        name, m = self.route_name("click on save")
        self.assertEqual(name, "click_any")
        self.assertEqual(m.group(2), "save")

        name, m = self.route_name("press save")
        self.assertEqual(name, "click_any")
        self.assertEqual(m.group(2), "save")

        name, m = self.route_name("tap continue")
        self.assertEqual(name, "click_any")
        self.assertEqual(m.group(2), "continue")

        name, m = self.route_name("hit allow")
        self.assertEqual(name, "click_any")
        self.assertEqual(m.group(2), "allow")

        # Bare tap
        name, _ = self.route_name("tap")

# ------------------------------------------------------- Samsung TV
class TestSamsungTV(Base):
    def test_routing_switch_to_computer(self):
        for text in ("switch to computer", "switch to the computer",
                     "switch to mac", "switch to the mac",
                     "switch to pc", "switch to the pc",
                     "turn computer on", "turn on computer",
                     "turn on the computer", "turn the computer on",
                     "computer on"):
            name, _ = self.route_name(text)
            self.assertEqual(name, "tv_computer", text)

    def test_routing_switch_to_tv(self):
        for text in ("switch to tv", "switch to the tv",
                     "switch to television", "home", "samsung home"):
            name, _ = self.route_name(text)
            self.assertEqual(name, "tv_tv", text)

    def test_routing_not_hijacked_by_switch_app(self):
        # Generic apps must still route to switch_app, not TV
        for text, app in (("switch to Notes", "Notes"),
                          ("switch to Safari", "Safari"),
                          ("focus Terminal", "Terminal"),
                          ("bring up Chrome", "Chrome")):
            name, m = self.route_name(text)
            self.assertEqual(name, "switch_app", text)
            self.assertEqual(m.group(2), app)

    def test_routing_switch_input(self):
        cases = [
            ("switch input to HDMI 2", "HDMI 2"),
            ("switch the input to HDMI 2", "HDMI 2"),
            ("switch source to HDMI1", "HDMI1"),
            ("change input to pc", "pc"),
            ("change the source to HDMI 3", "HDMI 3"),
        ]
        for text, want_src in cases:
            name, m = self.route_name(text)
            self.assertEqual(name, "tv_input", text)
            self.assertEqual(m.group(5).strip(), want_src)

    def test_routing_power_on_and_off(self):
        for text, state in (("turn on the tv", "on"),
                            ("turn on tv", "on"),
                            ("turn the tv on", "on"),
                            ("turn tv on", "on"),
                            ("tv on", "on"),
                            ("power on the tv", "on"),
                            ("turn on the television", "on"),
                            ("turn television on", "on"),
                            ("turn off the tv", "off"),
                            ("turn off tv", "off"),
                            ("turn the tv off", "off"),
                            ("turn tv off", "off"),
                            ("tv off", "off"),
                            ("power off the tv", "off"),
                            ("turn off the television", "off"),
                            ("turn television off", "off")):
            name, m = self.route_name(text)
            self.assertEqual(name, "tv_power", text)
            self.assertEqual(m.group(1), state)

    def test_partial_safety_gating(self):
        # Fixed-phrase commands are safe for partial execution
        for text in ("switch to computer", "switch to tv",
                     "turn on the tv", "turn off the tv"):
            r = fv.route(text, partial=True)
            self.assertIsNotNone(r, f"expected partial match for {text!r}")

        # Free-text payload (tv_input) is NOT partial safe
        r = fv.route("switch input to HDMI 2", partial=True)
        self.assertIsNone(r, "tv_input must not match on partial")

    def test_act_tv_input_unconfigured_speaks_setup(self):
        with mock.patch.object(fv, "_tv_configured", return_value=False):
            fv.act_tv_input("computer", "computer")
        self.assertIn("Samsung TV isn't set up yet", self.said[-1])

    def test_act_tv_power_unconfigured_speaks_setup(self):
        with mock.patch.object(fv, "_tv_configured", return_value=False):
            fv.act_tv_power(True)
        self.assertIn("Samsung TV isn't set up yet", self.said[-1])

    def test_act_tv_input_unreachable_speaks_error(self):
        with mock.patch.object(fv, "_tv_configured", return_value=True), \
             mock.patch.object(fv, "shell", side_effect=RuntimeError("timeout")):
            fv.act_tv_input("computer", "computer")
        self.assertIn("I couldn't reach the Samsung TV", self.said[-1])

    def test_act_tv_power_unreachable_speaks_error(self):
        with mock.patch.object(fv, "_tv_configured", return_value=True), \
             mock.patch.object(fv, "shell", side_effect=RuntimeError("timeout")):
            fv.act_tv_power(True)
        self.assertIn("I couldn't reach the Samsung TV", self.said[-1])

    def test_act_tv_input_and_power_success_executes_script(self):
        with mock.patch.object(fv, "_tv_configured", return_value=True), \
             mock.patch.object(fv, "shell") as sh:
            fv.act_tv_input("computer", "computer")
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "set-input", "computer"])
            self.assertIn("Switching to computer", self.said[-1])

            fv.act_tv_power(True)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "power", "on"])
            self.assertIn("Turning the TV on", self.said[-1])

            fv.act_tv_power(False)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "power", "off"])
            self.assertIn("Turning the TV off", self.said[-1])

    def test_routing_tv_volume_and_mute(self):
        cases = [
            ("set tv volume to 25", "tv_vol_set", "25"),
            ("set the tv volume to 50", "tv_vol_set", "50"),
            ("tv volume up", "tv_vol_delta", "up"),
            ("turn tv volume up", "tv_vol_delta", "up"),
            ("turn the tv volume up", "tv_vol_delta", "up"),
            ("turn up tv volume", "tv_vol_delta", "up"),
            ("tv volume down by 5", "tv_vol_delta", "down"),
            ("turn tv volume down", "tv_vol_delta", "down"),
            ("turn the tv volume down", "tv_vol_delta", "down"),
            ("turn down tv volume", "tv_vol_delta", "down"),
            ("mute tv", "tv_mute", None),
            ("mute the tv", "tv_mute", None),
            ("unmute tv", "tv_unmute", None),
            ("pause tv", "tv_media", "pause"),
            ("play the tv", "tv_media", "play"),
            ("stop tv", "tv_media", "stop"),
            ("art mode", "tv_art", None),
            ("tv art mode", "tv_art", None),
            ("screensaver", "tv_art", None),
            ("turn on tv screensaver", "tv_art", None),
        ]
        for text, want_name, want_val in cases:
            name, m = self.route_name(text)
            self.assertEqual(name, want_name, text)
            if want_name == "tv_vol_set":
                self.assertEqual(m.group(2), want_val)
            elif want_name == "tv_vol_delta":
                self.assertEqual(m.group(1), want_val)
            elif want_name == "tv_media":
                self.assertEqual(m.group(1), want_val)

    def test_act_tv_volume_and_mute_execution(self):
        with mock.patch.object(fv, "_tv_configured", return_value=True), \
             mock.patch.object(fv, "shell") as sh:
            fv.act_tv_volume_set(30)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "set-volume", "30"])
            self.assertIn("TV volume 30", self.said[-1])

            fv.act_tv_volume_delta(5)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "volume-up", "5"])
            self.assertIn("TV volume up", self.said[-1])

            fv.act_tv_volume_delta(-2)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "volume-down", "2"])
            self.assertIn("TV volume down", self.said[-1])

            fv.act_tv_mute(True)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "mute"])
            self.assertIn("TV muted", self.said[-1])

            fv.act_tv_mute(False)
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "unmute"])
            self.assertIn("TV unmuted", self.said[-1])

            fv.act_tv_media("pause")
            sh.assert_called_with([sys.executable, fv._TV_SCRIPT, "media", "pause"])
            self.assertIn("TV pause", self.said[-1])


class TestSamsungTVScript(unittest.TestCase):
    def setUp(self):
        self._orig_comp = os.environ.pop("SAMSUNG_INPUT_COMPUTER", None)
        self._orig_tv = os.environ.pop("SAMSUNG_INPUT_TV", None)
        self._orig_standby = os.environ.pop("SAMSUNG_TV_STANDBY", None)

    def tearDown(self):
        if self._orig_comp is not None:
            os.environ["SAMSUNG_INPUT_COMPUTER"] = self._orig_comp
        else:
            os.environ.pop("SAMSUNG_INPUT_COMPUTER", None)
        if self._orig_tv is not None:
            os.environ["SAMSUNG_INPUT_TV"] = self._orig_tv
        else:
            os.environ.pop("SAMSUNG_INPUT_TV", None)
        if self._orig_standby is not None:
            os.environ["SAMSUNG_TV_STANDBY"] = self._orig_standby
        else:
            os.environ.pop("SAMSUNG_TV_STANDBY", None)

    def test_script_resolve_source_aliases(self):
        self.assertEqual(samsung_tv._resolve_source("computer"), "HDMI1")
        self.assertEqual(samsung_tv._resolve_source("mac"), "HDMI1")
        self.assertEqual(samsung_tv._resolve_source("pc"), "HDMI1")
        self.assertEqual(samsung_tv._resolve_source("tv"), "digitalTv")
        self.assertEqual(samsung_tv._resolve_source("television"), "digitalTv")
        self.assertEqual(samsung_tv._resolve_source("cable"), "digitalTv")

    def test_script_resolve_source_raw_hdmi(self):
        self.assertEqual(samsung_tv._resolve_source("HDMI 2"), "HDMI2")
        self.assertEqual(samsung_tv._resolve_source("hdmi 2"), "HDMI2")
        self.assertEqual(samsung_tv._resolve_source("hdmi1"), "HDMI1")
        self.assertEqual(samsung_tv._resolve_source("customPort"), "customPort")

    def test_script_cmd_set_input_post_shape(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            msg = samsung_tv.cmd_set_input("computer")
            self.assertEqual(msg, "TV input -> HDMI1")
            mock_req.assert_called_once_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "mediaInputSource",
                    "command": "setInputSource",
                    "arguments": ["HDMI1"]
                }]}
            )

    def test_script_cmd_power_post_shape(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.dict(os.environ, {"SAMSUNG_TV_STANDBY": "", "SAMSUNG_TV_MAC": ""}), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            msg_on = samsung_tv.cmd_power("on")
            self.assertEqual(msg_on, "TV power on")
            mock_req.assert_any_call(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "switch",
                    "command": "on",
                    "arguments": []
                }]}
            )
            mock_req.assert_any_call(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "samsungvd.remoteControl",
                    "command": "send",
                    "arguments": ["HOME"]
                }]}
            )
            mock_req.reset_mock()
            msg_off = samsung_tv.cmd_power("off")
            self.assertEqual(msg_off, "TV power off")
            mock_req.assert_called_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "switch",
                    "command": "off",
                    "arguments": []
                }]}
            )

    def _frame_req(self, apps):
        """Fake _req: status GETs return successive tvChannelName values."""
        seq = list(apps)
        calls = []

        def fake(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                app = seq.pop(0) if len(seq) > 1 else seq[0]
                return {"components": {"main": {
                    "switch": {"switch": {"value": "on"}},
                    "tvChannel": {"tvChannelName": {"value": app}}}}}
            return {}
        return fake, calls

    @staticmethod
    def _homes(calls):
        return sum(1 for m, _, pl in calls if m == "POST" and pl
                   and pl["commands"][0]["capability"] == "samsungvd.remoteControl")

    def test_power_on_leaves_frame_art_mode_with_home_retry(self):
        fake, calls = self._frame_req(["art", "com.samsung.tv.csfs"])
        with mock.patch.object(samsung_tv, "_need_device", return_value="d"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.dict(os.environ, {"SAMSUNG_TV_MAC": ""}), \
             mock.patch.object(samsung_tv, "_req", side_effect=fake):
            self.assertEqual(samsung_tv.cmd_power("on"), "TV power on")
        self.assertEqual(self._homes(calls), 2)

    def test_power_on_reports_stuck_in_art_mode(self):
        fake, calls = self._frame_req(["art"])
        with mock.patch.object(samsung_tv, "_need_device", return_value="d"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.dict(os.environ, {"SAMSUNG_TV_MAC": ""}), \
             mock.patch.object(samsung_tv, "_req", side_effect=fake):
            with self.assertRaises(samsung_tv.TVError):
                samsung_tv.cmd_power("on")
        self.assertEqual(self._homes(calls), 3)

    def test_art_mode_off_reports_when_tv_ignores_it(self):
        fake, _ = self._frame_req(["com.samsung.tv.csfs"])
        with mock.patch.object(samsung_tv, "_need_device", return_value="d"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.dict(os.environ, {"SAMSUNG_TV_STANDBY": "ambient"}), \
             mock.patch.object(samsung_tv, "_req", side_effect=fake):
            with self.assertRaises(samsung_tv.TVError):
                samsung_tv.cmd_power("off")
        fake, _ = self._frame_req(["art"])
        with mock.patch.object(samsung_tv, "_need_device", return_value="d"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.dict(os.environ, {"SAMSUNG_TV_STANDBY": "ambient"}), \
             mock.patch.object(samsung_tv, "_req", side_effect=fake):
            self.assertEqual(samsung_tv.cmd_power("off"), "TV art mode on")

    def test_script_cmd_home_post_shape(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            msg = samsung_tv.cmd_home()
            self.assertEqual(msg, "TV home")
            mock_req.assert_called_once_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "samsungvd.remoteControl",
                    "command": "send",
                    "arguments": ["HOME"]
                }]}
            )

    def test_script_cmd_volume_and_media_post_shape(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            # Set volume
            samsung_tv.cmd_set_volume(25)
            mock_req.assert_called_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "audioVolume",
                    "command": "setVolume",
                    "arguments": [25]
                }]}
            )
            # Volume delta
            samsung_tv.cmd_volume_delta(2)
            mock_req.assert_called_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [
                    {"component": "main", "capability": "audioVolume", "command": "volumeUp", "arguments": []},
                    {"component": "main", "capability": "audioVolume", "command": "volumeUp", "arguments": []}
                ]}
            )
            # Mute
            samsung_tv.cmd_mute(True)
            mock_req.assert_called_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "audioMute",
                    "command": "mute",
                    "arguments": []
                }]}
            )
            # Media
            samsung_tv.cmd_media("pause")
            mock_req.assert_called_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "mediaPlayback",
                    "command": "pause",
                    "arguments": []
                }]}
            )

    def test_script_errors_unconfigured_and_invalid(self):
        with mock.patch.object(samsung_tv, "TOKEN", ""):
            with self.assertRaises(samsung_tv.TVError):
                samsung_tv._req("GET", "/devices")
        with mock.patch.object(samsung_tv, "DEVICE_ID", ""):
            with self.assertRaises(samsung_tv.TVError):
                samsung_tv._need_device()
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"):
            with self.assertRaises(samsung_tv.TVError):
                samsung_tv.cmd_power("sleep")

    def test_main_cli_environment_overrides(self):
        env = {
            "SAMSUNG_ST_TOKEN": "test-token",
            "SAMSUNG_TV_DEVICE_ID": "test-device-id",
            "SAMSUNG_INPUT_COMPUTER": "HDMI4",
            "SAMSUNG_INPUT_TV": "dtv",
        }
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(samsung_tv, "load_env"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            code = samsung_tv.main(["samsung_tv.py", "set-input", "computer"])
            self.assertEqual(code, 0)
            mock_req.assert_called_once_with(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "mediaInputSource",
                    "command": "setInputSource",
                    "arguments": ["HDMI4"]
                }]}
            )

    def test_cmd_art_mode(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            msg = samsung_tv.cmd_art()
            self.assertEqual(msg, "TV art mode on")
            # first call is the command; a refresh + status check follow
            self.assertEqual(mock_req.call_args_list[0], mock.call(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "samsungvd.ambient",
                    "command": "setAmbientOn",
                    "arguments": []
                }]}
            ))

    def test_power_off_ambient_standby_override(self):
        with mock.patch.dict(os.environ, {"SAMSUNG_TV_STANDBY": "ambient"}), \
             mock.patch.object(samsung_tv, "_need_device", return_value="test-device-id"), \
             mock.patch.object(samsung_tv.time, "sleep"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as mock_req:
            msg = samsung_tv.cmd_power("off")
            self.assertEqual(msg, "TV art mode on")
            mock_req.assert_any_call(
                "POST",
                "/devices/test-device-id/commands",
                {"commands": [{
                    "component": "main",
                    "capability": "samsungvd.ambient",
                    "command": "setAmbientOn",
                    "arguments": []
                }]}
            )


# ------------------------------------------------------- Wake word ("Mac")
class TestWakeWord(Base):
    def test_parse_wake_word_formats(self):
        cases = [
            ("Mac open notes", "open notes"),
            ("Mac, open notes", "open notes"),
            ("Mack, open notes", "open notes"),
            ("Hey Mac, open notes", "open notes"),
            ("Hi Mac: open notes", "open notes"),
            ("OK Mac open notes", "open notes"),
            ("Okay Mack, open notes", "open notes"),
            ("Yo Mac, what time is it", "what time is it"),
            ("Mac! Open notes", "Open notes"),
            ("Mac - open notes", "open notes"),
            ("Hay Mac, open notes", "open notes"),
            ("hay mac open notes", "open notes"),
            ("And Mac, open notes", "open notes"),
            ("heymac open notes", "open notes"),
            ("haymac open notes", "open notes"),
            ("Make, open notes", "open notes"),
            ("Mike open notes", "open notes"),
        ]
        for utterance, want_cmd in cases:
            is_wake, cmd = fv.parse_wake_word(utterance, "mac")
            self.assertTrue(is_wake, f"failed to match wake word in {utterance!r}")
            self.assertEqual(cmd, want_cmd, f"mismatched stripped command for {utterance!r}")

    def test_parse_wake_word_standalone(self):
        for utterance in ("Mac", "Hey Mac", "Mack", "OK Mac!", "Yo Mac:", "hay mac", "Hay Mac", "And Mac", "heymac", "haymac", "A Mac", "An Mac"):
            is_wake, cmd = fv.parse_wake_word(utterance, "mac")
            self.assertTrue(is_wake, f"failed for standalone {utterance!r}")
            self.assertEqual(cmd, "", f"expected empty command for {utterance!r}")

    def test_vad_reset_and_recovery(self):
        import numpy as np
        vad = fv.VoiceActivityDetector(sensitivity=1.8, start_ms=90, end_ms=200)
        # Simulate loud noise that puts VAD into speech
        loud = (np.random.randn(int(16000 * 0.03)) * 1200).astype(np.int16)
        states = [vad.update(loud) for _ in range(10)]
        self.assertTrue(vad.in_speech)

        # Calling reset should immediately clear in_speech and frame counters
        vad.reset(new_floor=1200.0)
        self.assertFalse(vad.in_speech)
        self.assertEqual(vad._speech_frames, 0)
        self.assertEqual(vad._silence_frames, 0)
        self.assertGreaterEqual(vad.floor, 1200.0)

        # Baseline noise at 1200 should now be treated as silence, not speech
        st = vad.update(loud)
        self.assertEqual(st, "silence")

    def test_parse_wake_word_non_matches(self):
        non_matches = [
            "open notes",
            "Macbook pro",
            "Machine learning",
            "I told Mac to do it",
            "turn off the tv",
            "what time is it",
        ]
        for utterance in non_matches:
            is_wake, cmd = fv.parse_wake_word(utterance, "mac")
            self.assertFalse(is_wake, f"falsely matched wake word in {utterance!r}")
            self.assertEqual(cmd, utterance)

    def test_parse_wake_word_custom(self):
        is_wake, cmd = fv.parse_wake_word("Computer, status", "computer")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "status")

        is_wake, cmd = fv.parse_wake_word("Hey computer: status", "computer")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "status")

        is_wake, cmd = fv.parse_wake_word("Mac, status", "computer")
        self.assertFalse(is_wake)
        self.assertEqual(cmd, "Mac, status")

    def test_handle_command_requires_wake_word_ignores_ambient_speech(self):
        with mock.patch.object(fv, "act_tv_power") as mock_power, \
             mock.patch.object(fv, "act_open_app") as mock_open:
            handled1 = fv.handle_command("turn off the tv", require_wake_word=True)
            self.assertFalse(handled1)
            mock_power.assert_not_called()

            handled2 = fv.handle_command("open notes and snap left", require_wake_word=True)
            self.assertFalse(handled2)
            mock_open.assert_not_called()
            self.assertEqual(len(self.said), 0)

    def test_handle_command_executes_with_wake_word_prefix(self):
        with mock.patch.object(fv, "act_open_app") as mock_open:
            handled = fv.handle_command("Mac, open notes", require_wake_word=True)
            self.assertTrue(handled)
            mock_open.assert_called_once_with("notes")

        with mock.patch.object(fv, "act_tv_input") as mock_tv:
            handled = fv.handle_command("Hey Mac: switch to TV", require_wake_word=True)
            self.assertTrue(handled)
            mock_tv.assert_called_once_with("tv", "TV")

    def test_handle_command_two_stage_wake_window(self):
        # Stage 1: say "Mac" alone
        handled = fv.handle_command("Mac", require_wake_word=True)
        self.assertTrue(handled)
        self.assertIn(fv.VOICE_WAKE_PHRASE, self.said)
        self.assertGreater(fv._wake_window_until, 0.0)

        # Stage 2: say command within wake window without repeating "Mac"
        with mock.patch.object(fv, "act_open_app") as mock_open:
            handled_followup = fv.handle_command("open notes", require_wake_word=True)
            self.assertTrue(handled_followup)
            mock_open.assert_called_once_with("notes")
            # Window stays open for chained commands!
            self.assertGreater(fv._wake_window_until, 0.0)

        # Stage 3: say second command in same active window
        with mock.patch.object(fv, "act_snap_window") as mock_snap:
            handled_chained = fv.handle_command("snap left", require_wake_word=True)
            self.assertTrue(handled_chained)
            mock_snap.assert_called_once_with("left")
            self.assertGreater(fv._wake_window_until, 0.0)

        # Stage 4: dismiss cleanly closes wake window
        handled_dismiss = fv.handle_command("that's all", require_wake_word=True)
        self.assertTrue(handled_dismiss)
        self.assertEqual(fv._wake_window_until, 0.0)

        # Stage 5: command after window expired is ignored
        fv._wake_window_until = 1.0  # long expired
        with mock.patch.object(fv, "act_open_app") as mock_open:
            handled_expired = fv.handle_command("open notes", require_wake_word=True)
            self.assertFalse(handled_expired)
            mock_open.assert_not_called()

    def test_push_to_talk_strips_wake_word_if_spoken(self):
        with mock.patch.object(fv, "act_open_app") as mock_open:
            # Without wake word
            self.assertTrue(fv.handle_command("open notes", require_wake_word=False))
            mock_open.assert_called_with("notes")

            # With wake word spoken into push-to-talk
            self.assertTrue(fv.handle_command("Mac, open notes", require_wake_word=False))
            mock_open.assert_called_with("notes")

    def test_disabled_wake_word(self):
        fv.VOICE_WAKE_WORD = ""
        with mock.patch.object(fv, "act_open_app") as mock_open:
            handled = fv.handle_command("open notes", require_wake_word=True)
            self.assertTrue(handled)
            mock_open.assert_called_once_with("notes")

    def test_wake_feedback_styles(self):
        with mock.patch.object(fv, "play_chime") as mock_chime:
            # "both" (default)
            fv.VOICE_WAKE_FEEDBACK = "both"
            fv.acknowledge_wake()
            mock_chime.assert_called_with("Tink.aiff")
            self.assertIn(fv.VOICE_WAKE_PHRASE, self.said)

            # "chime" only
            self.said.clear()
            mock_chime.reset_mock()
            fv.VOICE_WAKE_FEEDBACK = "chime"
            fv.acknowledge_wake()
            mock_chime.assert_called_with("Tink.aiff")
            self.assertEqual(len(self.said), 0)

            # "voice" only
            self.said.clear()
            mock_chime.reset_mock()
            fv.VOICE_WAKE_FEEDBACK = "voice"
            fv.acknowledge_wake()
            mock_chime.assert_not_called()
            self.assertIn(fv.VOICE_WAKE_PHRASE, self.said)

            # "silent"
            self.said.clear()
            mock_chime.reset_mock()
            fv.VOICE_WAKE_FEEDBACK = "silent"
            fv.acknowledge_wake()
            mock_chime.assert_not_called()
            self.assertEqual(len(self.said), 0)


# ------------------------------------------------------- Menu bar & state tracking
class TestMenuBarAndState(Base):
    def test_state_publishing_and_reading(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with mock.patch.object(fv, "_STATE_FILE", tmp_path), \
                 mock.patch.object(menu_bar, "STATE_FILE", tmp_path):
                # By default DRY_RUN is True in Base, so set it False to write state
                fv.DRY_RUN = False
                fv.update_state("listening", command="open notes")
                st = menu_bar.read_state()
                self.assertEqual(st.get("state"), "listening")
                self.assertEqual(st.get("command"), "open notes")
                self.assertEqual(st.get("wake_word"), "mac")
                self.assertIn("ts", st)
        finally:
            fv.DRY_RUN = True
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_menu_bar_read_empty_on_missing_file(self):
        with mock.patch.object(menu_bar, "STATE_FILE", "/tmp/nonexistent-state-12345.json"):
            self.assertEqual(menu_bar.read_state(), {})


# ------------------------------------------------------- Streaming Whisper STT
class TestStreamingWhisper(Base):
    def test_stream_process_line_with_wake_word_fires_command(self):
        fired = []
        session = fv.PartialSession(lambda name, m: fired.append((name, m.group(0))))
        raw_line = "[00:00:00.000 --> 00:00:02.000] Mac open notes\n"
        res = fv.stream_process_line(raw_line, session, require_wake=True)
        self.assertTrue(res)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("open_app", "open notes"))

    def test_stream_process_line_ignores_non_wake_words(self):
        fired = []
        session = fv.PartialSession(lambda name, m: fired.append((name, m.group(0))))
        raw_line = "[00:00:00.000 --> 00:00:02.000] open notes\n"
        res = fv.stream_process_line(raw_line, session, require_wake=True)
        self.assertFalse(res)
        self.assertEqual(len(fired), 0)

    def test_stream_process_line_two_stage_streaming(self):
        fired = []
        session = fv.PartialSession(lambda name, m: fired.append((name, m.group(0))))
        # Utterance 1: "Mac" alone
        res1 = fv.stream_process_line("Mac\n", session, require_wake=True)
        self.assertTrue(res1)
        self.assertEqual(len(fired), 0)
        self.assertGreater(fv._wake_window_until, 0.0)

        # Utterance 2: "open notes" within active wake window
        res2 = fv.stream_process_line("open notes\n", session, require_wake=True)
        self.assertTrue(res2)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("open_app", "open notes"))

    def test_stream_process_line_no_wake_required(self):
        fired = []
        session = fv.PartialSession(lambda name, m: fired.append((name, m.group(0))))
        raw_line = "open notes\n"
        res = fv.stream_process_line(raw_line, session, require_wake=False)
        self.assertTrue(res)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("open_app", "open notes"))


# ------------------------------------------------------- Wake Word Hardening (Punctuation & Stutter)
class TestWakeWordHardening(Base):
    def test_comma_after_greeting(self):
        is_wake, cmd = fv.parse_wake_word("Hey, Mac", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "")

        is_wake, cmd = fv.parse_wake_word("Hey, Mac, open notes", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "open notes")

        is_wake, cmd = fv.parse_wake_word("Hello, Mac, switch to TV", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "switch to TV")

        is_wake, cmd = fv.parse_wake_word("Okay, Mac", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "")

    def test_stutter_and_repeated_wake_word(self):
        is_wake, cmd = fv.parse_wake_word("Mac, Mac open notes", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "open notes")

    def test_leading_punctuation(self):
        is_wake, cmd = fv.parse_wake_word("...Mac open notes", "mac")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "open notes")


# ------------------------------------------------------- Fuzzy App Matching
class TestFuzzyAppMatching(Base):
    def test_conversational_noise_stripped(self):
        with mock.patch("free_voice.installed_apps", return_value=["Notes", "Safari", "Mail", "Calculator"]):
            self.assertEqual(fv.resolve_app("my notes"), "Notes")
            self.assertEqual(fv.resolve_app("the safari app"), "Safari")
            self.assertEqual(fv.resolve_app("the calculator"), "Calculator")

    def test_developer_and_productivity_aliases(self):
        with mock.patch("free_voice.installed_apps", return_value=["Visual Studio Code", "Sublime Text", "Microsoft Word", "Activity Monitor"]):
            self.assertEqual(fv.resolve_app("code"), "Visual Studio Code")
            self.assertEqual(fv.resolve_app("vscode"), "Visual Studio Code")
            self.assertEqual(fv.resolve_app("vsc"), "Visual Studio Code")
            self.assertEqual(fv.resolve_app("sublime"), "Sublime Text")
            self.assertEqual(fv.resolve_app("word"), "Microsoft Word")
            self.assertEqual(fv.resolve_app("task manager"), "Activity Monitor")

    def test_token_set_matching(self):
        with mock.patch("free_voice.installed_apps", return_value=["Visual Studio Code"]):
            self.assertEqual(fv.resolve_app("visual studio"), "Visual Studio Code")

    def test_prefer_running_priority(self):
        # Even if multiple apps match, running app should win when prefer_running is True
        with mock.patch("free_voice.running_apps", return_value=["Google Chrome"]), \
             mock.patch("free_voice.installed_apps", return_value=["Google Chrome", "Chromium"]):
            self.assertEqual(fv.resolve_app("chrome", prefer_running=True), "Google Chrome")


# ------------------------------------------------------- Decision Router (SystemOne)
class TestDecisionRouter(Base):
    def test_decision_route_volume(self):
        fv.OLLAMA_DECISION_MODEL = "tev1:0.8b"
        fake_resp = {
            "answers": {
                "action": {
                    "choice": "set_volume",
                    "probabilities": {"set_volume": 0.98},
                }
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            res = fv.ollama_decision_route("could you please turn the volume down a bit")
            self.assertIsNotNone(res)
            action, params, prob = res
            self.assertEqual(action, "set_volume")
            self.assertEqual(params.get("direction"), "down")
            self.assertAlmostEqual(prob, 0.98)

    def test_decision_route_media(self):
        fv.OLLAMA_DECISION_MODEL = "tev1:0.8b"
        fake_resp = {
            "answers": {
                "action": {
                    "choice": "media",
                    "probabilities": {"media": 0.89},
                }
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            res = fv.ollama_decision_route("skip to the next track please")
            self.assertIsNotNone(res)
            action, params, prob = res
            self.assertEqual(action, "media")
            self.assertEqual(params.get("op"), "next")

    def test_decision_route_falls_back_on_unknown(self):
        fv.OLLAMA_DECISION_MODEL = "tev1:0.8b"
        fake_resp = {
            "answers": {
                "action": {
                    "choice": "unknown",
                    "probabilities": {"unknown": 0.55},
                }
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            res = fv.ollama_decision_route("what is the capital of France")
            self.assertIsNone(res)

    def test_is_voice_command_fast_path(self):
        self.assertTrue(fv.is_voice_command("open safari")[0])
        self.assertTrue(fv.is_voice_command("please turn up the volume")[0])
        self.assertTrue(fv.is_voice_command("hey mac snap left")[0])

    def test_is_voice_command_gate_rejects_chatter(self):
        fake_gate_resp = {
            "answers": {
                "is_command": {
                    "choice": "none",
                    "probabilities": {"command": 0.25, "none": 0.75},
                }
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_gate_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            is_cmd, prob = fv.is_voice_command("did you see that news story yesterday")
            self.assertFalse(is_cmd)
            self.assertAlmostEqual(prob, 0.25)

    def test_decision_route_rejects_below_threshold(self):
        fv.OLLAMA_DECISION_MODEL = "tev1:0.8b"
        fv.DECISION_MIN_CONFIDENCE = 0.7
        fake_resp = {
            "answers": {
                "action": {
                    "choice": "web_search",
                    "probabilities": {"web_search": 0.55},
                }
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            # 0.55 is below 0.7 threshold -> should reject and return None
            res = fv.ollama_decision_route("search for something weird")
            self.assertIsNone(res)


# ------------------------------------------------------- Apple TV / Couch Media
class TestAppleTVFeatures(Base):
    def test_media_seek_forward(self):
        for phrase in ("skip", "fast forward", "forward 30 seconds", "skip 15 seconds"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "media_seek_fwd")

        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("skip 20 seconds")
            self.assertEqual(mock_kc.call_count, 2)
            self.assertEqual(mock_kc.call_args[0][0], 124)

    def test_media_seek_backward(self):
        for phrase in ("rewind", "rewind 20 seconds", "skip back 30 seconds", "go back 15 seconds"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "media_seek_back")

        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("rewind 10 seconds")
            self.assertEqual(mock_kc.call_count, 1)
            self.assertEqual(mock_kc.call_args[0][0], 123)

    def test_what_did_they_say(self):
        for phrase in ("what did they say", "what did she say?", "what did he say"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "media_what_did_they_say")

        with mock.patch.object(fv, "act_key_code") as mock_kc, \
             mock.patch.object(fv, "act_keystroke") as mock_ks:
            fv.handle_command("what did they say")
            self.assertEqual(mock_kc.call_count, 2)
            mock_ks.assert_called_with("c")

    def test_subtitles_toggle(self):
        for phrase in ("turn subtitles on", "subtitles off", "toggle subtitles", "closed captions", "captions"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "media_subtitles")

        with mock.patch.object(fv, "act_keystroke") as mock_ks:
            fv.handle_command("toggle subtitles")
            mock_ks.assert_called_with("c")

    def test_next_episode(self):
        r = fv.route("next episode")
        self.assertIsNotNone(r)
        name, m = r
        self.assertEqual(name, "media_next_episode")

        with mock.patch.object(fv, "act_keystroke") as mock_ks:
            fv.handle_command("next episode")
            mock_ks.assert_called_with("n", "shift down")

    def test_watch_stream_services(self):
        cases = {
            "watch youtube": "https://www.youtube.com",
            "open netflix": "https://www.netflix.com",
            "watch disney plus": "https://www.disneyplus.com",
            "open hulu": "https://www.hulu.com",
            "watch prime video": "https://www.amazon.com/gp/video/storefront",
            "open max": "https://www.max.com",
        }
        for phrase, expected_url in cases.items():
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "watch_stream")
            with mock.patch.object(fv, "shell") as mock_sh:
                fv.handle_command(phrase)
                mock_sh.assert_called_with(["open", expected_url])

    def test_watch_apple_tv(self):
        r = fv.route("watch apple tv")
        self.assertIsNotNone(r)
        name, m = r
        self.assertEqual(name, "watch_stream")
        with mock.patch.object(fv, "act_open_app") as mock_app:
            fv.handle_command("watch apple tv")
            mock_app.assert_called_with("TV")

    def test_search_youtube(self):
        r = fv.route("search youtube for interstellar soundtrack")
        self.assertIsNotNone(r)
        name, m = r
        self.assertEqual(name, "search_youtube")
        with mock.patch.object(fv, "shell") as mock_sh:
            fv.handle_command("search youtube for interstellar soundtrack")
            self.assertTrue(mock_sh.called)
            cmd = mock_sh.call_args[0][0]
            self.assertEqual(cmd[0], "open")
            self.assertIn("youtube.com/results?search_query=interstellar", cmd[1])

    def test_airplay_settings(self):
        for phrase in ("airplay", "open airplay settings", "enable airplay receiver", "connect airplay", "screen mirroring"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "airplay_settings")

        with mock.patch.object(fv, "shell") as mock_sh:
            fv.handle_command("open airplay settings")
            mock_sh.assert_called_with(["open", "x-apple.systempreferences:com.apple.AirPlay-Settings.extension"])

    def test_fullscreen_and_theater_mode(self):
        for phrase in ("fullscreen", "full screen", "toggle full screen", "enter full screen", "exit full screen", "theater mode", "theatre mode"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "fullscreen")

        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("theater mode")
            mock_kc.assert_called_with(3, "control down, command down")

    def test_media_seek_step_calculations(self):
        # 60s -> 6 steps
        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("skip 60 seconds")
            self.assertEqual(mock_kc.call_count, 6)
            self.assertEqual(mock_kc.call_args_list[0][0][0], 124)

        # 5s -> max(1, round(0.5)) -> 1 step
        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("skip 5 seconds")
            self.assertEqual(mock_kc.call_count, 1)

        # rewind 50s -> 5 steps of 123
        with mock.patch.object(fv, "act_key_code") as mock_kc:
            fv.handle_command("rewind 50 seconds")
            self.assertEqual(mock_kc.call_count, 5)
            self.assertEqual(mock_kc.call_args_list[0][0][0], 123)

    def test_apple_tv_chained_commands(self):
        calls = []
        with mock.patch.object(fv, "act_watch_stream", lambda s, query="": calls.append(("watch", s))), \
             mock.patch.object(fv, "act_snap_window", lambda s: calls.append(("snap", s))), \
             mock.patch("time.sleep"):
            fv.handle_command("watch netflix and snap left")
        self.assertIn(("watch", "netflix"), calls)
        self.assertIn(("snap", "left"), calls)

    def test_streaming_query_escaping(self):
        r = fv.route("search youtube for rick & morty: season 1")
        self.assertIsNotNone(r)
        with mock.patch.object(fv, "shell") as mock_sh:
            fv.handle_command("search youtube for rick & morty: season 1")
            cmd = mock_sh.call_args[0][0]
            self.assertIn("search_query=rick%20%26%20morty%3A%20season%201", cmd[1])

    def test_unknown_streaming_service_fallback(self):
        # Gracefully handle unexpected service strings without throwing
        with mock.patch.object(fv, "say") as mock_say:
            fv.act_watch_stream("vimeo")
            mock_say.assert_called_with("Opening vimeo")

    def test_wake_word_with_apple_tv_commands(self):
        fv.VOICE_WAKE_WORD = "mac"
        # Without wake word -> ignored
        with mock.patch.object(fv, "act_keystroke") as mock_ks:
            handled = fv.handle_command("turn subtitles on", require_wake_word=True)
            self.assertFalse(handled)
            self.assertFalse(mock_ks.called)

        # With wake word -> handled
        with mock.patch.object(fv, "act_keystroke") as mock_ks:
            handled = fv.handle_command("Mac, turn subtitles on", require_wake_word=True)
            self.assertTrue(handled)
            mock_ks.assert_called_with("c")

        # Wake word + what did they say
        with mock.patch.object(fv, "act_key_code") as mock_kc, \
             mock.patch.object(fv, "act_keystroke") as mock_ks:
            handled = fv.handle_command("Mac, what did they say", require_wake_word=True)
            self.assertTrue(handled)
            self.assertEqual(mock_kc.call_count, 2)
            mock_ks.assert_called_with("c")

    def test_streaming_partial_safety(self):
        # Incomplete enum must not fire on partial
        self.assertIsNone(fv.route("watch you", partial=True))
        # Complete enum fires on partial
        r = fv.route("watch youtube", partial=True)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "watch_stream")
        # Free-text search must NOT fire on partial
        self.assertIsNone(fv.route("search youtube for lo-fi beats", partial=True))
        # Free-text search DOES fire on final transcript
        r_final = fv.route("search youtube for lo-fi beats", partial=False)
        self.assertIsNotNone(r_final)
        self.assertEqual(r_final[0], "search_youtube")


class TestDaemonAndService(Base):
    def test_scripts_exist_and_executable(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        service_sh = os.path.join(repo_dir, "service.sh")
        runner_py = os.path.join(repo_dir, "daemon_runner.py")
        self.assertTrue(os.path.isfile(service_sh))
        self.assertTrue(os.access(service_sh, os.X_OK))
        self.assertTrue(os.path.isfile(runner_py))
        self.assertTrue(os.access(runner_py, os.X_OK))

    def test_service_status_command(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        service_sh = os.path.join(repo_dir, "service.sh")
        res = fv.subprocess.run([service_sh, "status"], capture_output=True, text=True, check=False)
class TestTier05Router(Base):
    def setUp(self):
        super().setUp()
        # These tests exercise the built-in hashing matcher (threshold 0.75). Pin it so they do not depend on whether an embedding
        # model happens to be warm in Ollama or an ONNX file is installed on this machine.
        saved = (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try, fv.OLLAMA_EMBED_MODEL)
        self.addCleanup(lambda: (setattr(fv, "_PRECOMPUTED_MATRIX", saved[0]), setattr(fv, "_PRECOMPUTED_INTENTS", saved[1]),
                                 setattr(fv, "_EMBED_NEURAL", saved[2]), setattr(fv, "_embed_last_try", saved[3]), setattr(fv, "OLLAMA_EMBED_MODEL", saved[4])))
        fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try = None, [], False, 0.0
        fv.OLLAMA_EMBED_MODEL = ""

    def test_tier05_embed_speed_and_threshold(self):
        # Embedding and matching must run well under 50ms budget (typically <1ms)
        t0 = fv.time.time()
        res = fv.tier05_embed_match("call an ascii picture of a heart", threshold=0.75)
        dt = (fv.time.time() - t0) * 1000
        self.assertLess(dt, 50.0, f"Embedding match took {dt:.2f}ms, exceeds 50ms budget")
        self.assertIsNotNone(res)
        action, params, score = res
        self.assertEqual(action, "draw_ascii")
        self.assertEqual(params.get("subject"), "heart")
        self.assertGreaterEqual(score, 0.75)

    def test_whisper_mishearing_call_an_ascii_routes_via_tier05(self):
        phrase = "call an ascii picture of a heart"
        # 1. Must NOT match Tier 0 regex
        self.assertIsNone(fv.route(phrase, partial=False))
        # 2. Must route and execute draw_ascii with subject="heart"
        with mock.patch.object(fv, "act_draw_ascii") as mock_draw:
            handled = fv.handle_command(phrase)
            self.assertTrue(handled)
            mock_draw.assert_called_once_with("heart")

    def test_whisper_mishearing_john_askey_routes_via_tier05b(self):
        phrase = "john askey picture of a heart"
        # 1. Must NOT match Tier 0 regex
        self.assertIsNone(fv.route(phrase, partial=False))
        # 2. Cosine match is below 0.75 threshold
        self.assertIsNone(fv.tier05_embed_match(phrase, threshold=0.75))
        # 3. Fast decision model classifies it in JSON mode
        fake_chat_resp = {
            "message": {
                "content": json.dumps({
                    "action": "draw_ascii",
                    "params": {"subject": "heart"},
                    "confidence": 0.95,
                })
            }
        }
        with mock.patch("urllib.request.urlopen") as mock_url, \
             mock.patch.object(fv, "act_draw_ascii") as mock_draw:
            resp_mock = mock.MagicMock()
            resp_mock.read.return_value = json.dumps(fake_chat_resp).encode()
            resp_mock.__enter__.return_value = resp_mock
            mock_url.return_value = resp_mock

            fv.OLLAMA_DECISION_MODEL = "qwen2.5:1.5b"
            handled = fv.handle_command(phrase)
            self.assertTrue(handled)
            mock_draw.assert_called_once_with("heart")

    def test_destructive_safety_gate_blocks_in_tier05(self):
        # Destructive actions cannot execute via fuzzy Tier 0.5 without Tier 0 regex
        destructive_phrases = [
            "shut down computer",
            "restart computer",
            "log out user",
            "empty the trash",
        ]
        for phrase in destructive_phrases:
            res = fv.tier05_route(phrase)
            self.assertIsNotNone(res)
            self.assertEqual(res[0], "blocked", f"Tier 0.5 failed to block destructive intent: {phrase}")

    def test_close_all_windows_not_blocked_in_tier05(self):
        res = fv.tier05_route("close all windows")
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "close_all_windows")
    def test_close_the_browser_window_routes_to_window_close_not_quit(self):
        # Must route to close_window_named and execute act_keystroke('w', 'command down'), NOT quit_app
        match = fv.route("close the browser window", partial=False)
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "close_window_named")
        with mock.patch.object(fv, "act_close_window") as mock_close:
            fv.execute_match(*match)
            mock_close.assert_called_once_with("browser")

    @unittest.skipUnless(sys.platform == "darwin", "needs pynput/Quartz (macOS only)")
    def test_act_keystroke_in_process_no_osascript(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mock_kb = mock.Mock()
        with mock.patch.object(fv, "_keyboard", return_value=mock_kb), \
             mock.patch.object(fv, "applescript") as mock_as:
            fv.act_keystroke("w", "command down")
            # Must send key via pynput keyboard controller and NEVER spawn osascript
            self.assertTrue(mock_kb.press.called)
            self.assertTrue(mock_kb.release.called)
            mock_as.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "needs pynput/Quartz (macOS only)")
    def test_act_key_code_in_process_no_osascript(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mock_kb = mock.Mock()
        with mock.patch.object(fv, "_keyboard", return_value=mock_kb), \
             mock.patch.object(fv, "applescript") as mock_as:
            fv.act_key_code(124, "control down")
            self.assertTrue(mock_kb.press.called)
            self.assertTrue(mock_kb.release.called)
            mock_as.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "needs pynput/Quartz (macOS only)")
    def test_act_type_text_in_process(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        mock_kb = mock.Mock()
        with mock.patch.object(fv, "_keyboard", return_value=mock_kb), \
             mock.patch.object(fv, "applescript") as mock_as:
            fv.act_type_text("hello123")
            self.assertEqual(mock_kb.type.call_count, 8)
            mock_as.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "needs pynput/Quartz (macOS only)")
    def test_warp_mouse_called_on_mouse_to(self):
        fv.DRY_RUN = False
        self.addCleanup(setattr, fv, "DRY_RUN", True)
        with mock.patch.object(fv, "_warp_mouse") as mock_warp, \
             mock.patch.object(fv, "_screen_dimensions", return_value=(1920, 1080)):
            fv.act_mouse_to("bottom")
            mock_warp.assert_called_once_with(960, 1030)
            self.assertIn("Mouse is on bottom", self.said)


class TestMultiActionChainingAndMLXWhisper(Base):
    def test_multi_action_chaining_conversational(self):
        calls = []
        with mock.patch("free_voice.act_open_app", side_effect=lambda a: calls.append(("open_app", a))), \
             mock.patch("free_voice.act_web_search", side_effect=lambda q: calls.append(("web_search", q))), \
             mock.patch("free_voice.act_snap_window", side_effect=lambda s: calls.append(("snap_window", s))), \
             mock.patch("time.sleep", return_value=None):
            handled = fv.handle_command("Open Safari, find Apple's latest 10-K, and snap it to the left half of my screen.")
            self.assertTrue(handled)
            self.assertEqual(calls, [
                ("open_app", "Safari"),
                ("web_search", "Apple's latest 10-K"),
                ("snap_window", "left"),
            ])

    def test_multi_action_chaining_then(self):
        calls = []
        with mock.patch("free_voice.act_open_app", side_effect=lambda a: calls.append(("open_app", a))), \
             mock.patch("free_voice.act_web_search", side_effect=lambda q: calls.append(("web_search", q))), \
             mock.patch("free_voice.act_snap_window", side_effect=lambda s: calls.append(("snap_window", s))), \
             mock.patch("time.sleep", return_value=None):
            handled = fv.handle_command("open safari then search for weather and then snap right")
            self.assertTrue(handled)
            self.assertEqual(calls, [
                ("open_app", "safari"),
                ("web_search", "weather"),
                ("snap_window", "right"),
            ])

    def test_snap_window_top_bottom(self):
        calls = []
        with mock.patch("free_voice.act_snap_window", side_effect=lambda s: calls.append(s)):
            fv.handle_command("snap to top")
            fv.handle_command("snap to bottom")
            self.assertEqual(calls, ["top", "bottom"])

    def test_mlx_whisper_repo_resolution(self):
        self.assertEqual(fv._resolve_mlx_whisper_repo("base.en"), "mlx-community/whisper-base-mlx")
        self.assertEqual(fv._resolve_mlx_whisper_repo("tiny"), "mlx-community/whisper-tiny-mlx")
        self.assertEqual(fv._resolve_mlx_whisper_repo("mlx-community/custom-model"), "mlx-community/custom-model")

    def test_ollama_multi_action_plan_mock(self):
        """Verify the LLM planner parses Ollama JSON response into action list."""
        fake_response = json.dumps({
            "message": {"content": json.dumps({"actions": [
                {"action": "open_app", "params": {"app": "Safari"}},
                {"action": "web_search", "params": {"query": "test"}},
            ]})}
        }).encode()

        class FakeResp:
            def read(self): return fake_response
            def __enter__(self): return self
            def __exit__(self, *a): pass

        fv.OLLAMA_DECISION_MODEL = "qwen2.5:1.5b"
        try:
            with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
                result = fv.ollama_multi_action_plan("open safari and search for test")
        finally:
            fv.OLLAMA_DECISION_MODEL = ""

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["action"], "open_app")
        self.assertEqual(result[1]["action"], "web_search")

    def test_find_next_not_confused_with_web_search(self):
        """'find next' must route to find_next, not web_search."""
        name, _ = self.route_name("find next")
        self.assertEqual(name, "find_next")
        # Also ensure web_search doesn't claim 'find next'
        ws_name, _ = self.route_name("find me cats")
        self.assertEqual(ws_name, "web_search")


if __name__ == "__main__":
    unittest.main()

# ------------------------------------------------------------------------
# Review batch 2 regression tests. Appended after the __main__ guard so they
# never overlap other in-flight edits; `python -m unittest discover tests`
# picks them up.
# ------------------------------------------------------------------------
class TestReviewB8MediaSeekDispatch(Base):
    def test_media_seek_back_dispatch_uses_keywords(self):
        with mock.patch.object(fv, "act_media_seek") as seek:
            fv.dispatch_tier1("media_seek", {"seconds": 30, "direction": "back"})
            seek.assert_called_once_with(seconds=30, forward=False)

    def test_media_seek_fwd_default(self):
        with mock.patch.object(fv, "act_media_seek") as seek:
            fv.dispatch_tier1("media_seek", {"seconds": 20})
            seek.assert_called_once_with(seconds=20, forward=True)

    def test_media_seek_extract_then_dispatch_no_typeerror(self):
        params = fv._extract_intent_params("media_seek", "rewind 30 seconds")
        with mock.patch.object(fv, "act_key_code"):
            fv.dispatch_tier1("media_seek", params)
        self.assertIn("Rewound 30 seconds", self.said)


class TestReviewB9RelativeVolume(Base):
    def test_direction_down_without_level_is_relative(self):
        with mock.patch.object(fv, "act_volume_delta") as delta, \
             mock.patch.object(fv, "act_set_volume") as setv:
            fv.dispatch_tier1("set_volume", {"direction": "down"})
            delta.assert_called_once_with(-10)
            setv.assert_not_called()

    def test_direction_up_without_level_is_relative(self):
        with mock.patch.object(fv, "act_volume_delta") as delta:
            fv.dispatch_tier1("set_volume", {"direction": "up"})
            delta.assert_called_once_with(10)

    def test_explicit_level_still_absolute(self):
        with mock.patch.object(fv, "act_volume_delta") as delta, \
             mock.patch.object(fv, "act_set_volume") as setv:
            fv.dispatch_tier1("set_volume", {"level": 30, "direction": "down"})
            setv.assert_called_once_with(30)
            delta.assert_not_called()

    def test_extracted_turn_volume_down(self):
        params = fv._extract_intent_params("set_volume", "turn the volume down")
        with mock.patch.object(fv, "act_volume_delta") as delta:
            fv.dispatch_tier1("set_volume", params)
            delta.assert_called_once_with(-10)

    def test_level_as_string_up_or_down(self):
        with mock.patch.object(fv, "act_volume_delta") as delta:
            fv.dispatch_tier1("set_volume", {"level": "up"})
            delta.assert_called_once_with(10)
        with mock.patch.object(fv, "act_volume_delta") as delta:
            fv.dispatch_tier1("set_volume", {"level": "down"})
            delta.assert_called_once_with(-10)

    def test_tier1_tv_actions_dispatch(self):
        with mock.patch.object(fv, "act_tv_power") as pwr:
            fv.dispatch_tier1("tv_power", {"on": False})
            pwr.assert_called_once_with(False)
        with mock.patch.object(fv, "act_tv_volume_delta") as vold:
            fv.dispatch_tier1("tv_volume", {"direction": "up"})
            vold.assert_called_once_with(1)
        with mock.patch.object(fv, "act_tv_volume_set") as vols:
            fv.dispatch_tier1("tv_volume", {"level": 35})
            vols.assert_called_once_with(35)
        with mock.patch.object(fv, "act_tv_input") as inpt:
            fv.dispatch_tier1("tv_input", {"source": "HDMI 2"})
            inpt.assert_called_once_with("HDMI 2", "HDMI 2")
        with mock.patch.object(fv, "act_tv_mute") as mt:
            fv.dispatch_tier1("tv_mute", {"mute": True})
            mt.assert_called_once_with(True)


class TestReviewB10TimerUnits(Base):
    def test_normalize_all_units(self):
        for raw, want in [
            ("s", "second"), ("sec", "second"), ("secs", "second"), ("second", "second"), ("seconds", "second"),
            ("min", "minute"), ("mins", "minute"), ("minute", "minute"), ("Minutes", "minute"),
            ("hr", "hour"), ("hrs", "hour"), ("hour", "hour"), ("hours", "hour"),
        ]:
            self.assertEqual(fv._normalize_time_unit(raw), want, raw)
        self.assertEqual(fv._normalize_time_unit("fortnight"), "minute")
        self.assertEqual(fv._normalize_time_unit(None), "minute")

    def test_thirty_seconds_is_seconds_not_minutes(self):
        params = fv._extract_intent_params("timer", "set a timer for 30 seconds")
        with mock.patch.object(fv, "act_timer") as t:
            fv.dispatch_tier1("timer", params)
            t.assert_called_once_with(30, "second")

    def test_dispatch_hours(self):
        with mock.patch.object(fv, "act_timer") as t:
            fv.dispatch_tier1("timer", {"amount": 2, "unit": "hrs"})
            t.assert_called_once_with(2, "hour")


class TestReviewB11MicFallback(Base):
    def test_falls_back_to_default_when_voice_mic_missing(self):
        calls = []

        def fake_usable(want=None):
            calls.append(want)
            return (True, None) if want == "" else (False, None)

        logs = []
        with mock.patch.object(fv, "VOICE_MIC", "iPhone"), \
             mock.patch.object(fv, "_usable_input", side_effect=fake_usable), \
             mock.patch.object(fv, "log", side_effect=logs.append), \
             mock.patch.object(fv.time, "sleep", return_value=None):
            idx = fv.wait_for_input_device(poll_s=0, fallback_after_s=0)
        self.assertIsNone(idx)
        self.assertTrue(any("falling back to the default input" in m for m in logs))

    def test_prefers_voice_mic_when_present(self):
        with mock.patch.object(fv, "VOICE_MIC", "iPhone"), \
             mock.patch.object(fv, "_usable_input", return_value=(True, 3)):
            self.assertEqual(fv.wait_for_input_device(poll_s=0, fallback_after_s=0), 3)

    def test_prime_permissions_only_pins_iphone_when_found(self):
        path = os.path.join(os.path.dirname(__file__), "..", "prime_permissions.sh")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count('upsert_env VOICE_MIC "iPhone"'), 1)


class TestReviewB12WifiDevice(Base):
    PORTS = (
        "\nHardware Port: Ethernet\nDevice: en0\nEthernet Address: aa:bb\n\n"
        "Hardware Port: Wi-Fi\nDevice: en1\nEthernet Address: cc:dd\n\n"
        "Hardware Port: Thunderbolt Bridge\nDevice: bridge0\n"
    )

    def test_parses_wifi_device(self):
        with mock.patch.object(fv, "shell", return_value=self.PORTS):
            self.assertEqual(fv._wifi_device(), "en1")

    def test_falls_back_to_en0(self):
        with mock.patch.object(fv, "shell", side_effect=RuntimeError("nope")):
            self.assertEqual(fv._wifi_device(), "en0")
        with mock.patch.object(fv, "shell", return_value="Hardware Port: Ethernet\nDevice: en0\n"):
            self.assertEqual(fv._wifi_device(), "en0")

    def test_act_wifi_uses_detected_device(self):
        cmds = []

        def fake_shell(cmd):
            cmds.append(cmd)
            return self.PORTS if "-listallhardwareports" in cmd else ""

        with mock.patch.object(fv, "shell", side_effect=fake_shell):
            fv.act_wifi(False)
        self.assertIn(["networksetup", "-setairportpower", "en1", "off"], cmds)


class TestReviewB14VadPreroll(Base):
    def test_speech_onset_is_not_clipped(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        frame_len = int(16000 * 0.03)
        quiet = np.zeros(frame_len, dtype=np.int16) + 5
        loud = np.full(frame_len, 4000, dtype=np.int16)
        n_loud = 20
        frames = [quiet] * 12 + [quiet] * 30 + [loud] * n_loud + [quiet] * 40

        class FakeStream:
            def __init__(self, *a, **k):
                self._it = iter(frames)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                try:
                    return next(self._it).tobytes(), False
                except StopIteration:
                    raise KeyboardInterrupt

        fake_sd = mock.Mock()
        fake_sd.RawInputStream = FakeStream
        got = []
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}), \
             mock.patch.object(fv, "wait_for_input_device", return_value=None), \
             mock.patch.object(fv, "describe_input_device", return_value="fake"), \
             mock.patch.object(fv, "update_state"), \
             mock.patch.object(fv, "play_chime"), \
             mock.patch.object(fv, "is_speaking", return_value=False), \
             mock.patch.object(fv.time, "sleep", return_value=None):
            fv.always_listen_loop(got.append)
        self.assertEqual(len(got), 1)
        loud_samples = int((np.abs(got[0] * 32768.0 - 4000) < 1).sum())
        self.assertEqual(loud_samples, n_loud * frame_len)


def _import_critic_with_stubs(testcase):
    """Import masterpiece_critic on any OS by stubbing AppKit/Foundation/objc.
    The module is dropped from sys.modules again on cleanup."""
    import importlib
    import types

    class _NSView:
        def initWithFrame_(self, frame):
            return self

        def setNeedsDisplay_(self, flag):
            pass

    class _NSObject:
        def init(self):
            return self

        @classmethod
        def alloc(cls):
            return cls.__new__(cls)

    appkit = types.ModuleType("AppKit")
    appkit.NSView = _NSView
    appkit.__getattr__ = lambda name: mock.MagicMock(name=f"AppKit.{name}")
    foundation = types.ModuleType("Foundation")
    foundation.NSObject = _NSObject
    foundation.NSTimer = mock.MagicMock()
    objc_mod = types.ModuleType("objc")
    objc_mod.super = super
    patcher = mock.patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation, "objc": objc_mod})
    patcher.start()
    testcase.addCleanup(patcher.stop)
    sys.modules.pop("masterpiece_critic", None)
    testcase.addCleanup(sys.modules.pop, "masterpiece_critic", None)
    return importlib.import_module("masterpiece_critic")


class TestReviewB25ThemeLayerReset(unittest.TestCase):
    def test_switching_theme_clears_couch_layers(self):
        mc = _import_critic_with_stubs(self)
        view = mc.CompanionView.__new__(mc.CompanionView)
        view.idle_bare_image = object()
        view.idle_chars_image = object()
        view.leo_fg_image = object()
        view.cat_walk_frames = [object()]
        view.pose_images = {"idle": object()}
        view.base_image = object()
        with mock.patch.object(mc.os.path, "exists", return_value=False):
            view.load_theme("wine_girls")
        self.assertIsNone(view.idle_bare_image)
        self.assertIsNone(view.idle_chars_image)
        self.assertIsNone(view.leo_fg_image)
        self.assertEqual(view.cat_walk_frames, [])
        self.assertEqual(view.pose_images, {})
        self.assertIsNone(view.base_image)
        self.assertEqual(view.theme_key, "wine_girls")


class TestReviewS9CriticThemeWhitelist(unittest.TestCase):
    def _load(self, cfg):
        mc = _import_critic_with_stubs(self)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "critic_config.json")
            with open(path, "w") as f:
                json.dump(cfg, f)
            with mock.patch.object(mc, "CONFIG_FILE", path):
                ctl = mc.CriticOverlayController.alloc().init()
                ctl.load_config()
        return ctl

    def test_unknown_theme_falls_back(self):
        ctl = self._load({"theme": "../../<script>x", "position": "bottom_left"})
        self.assertEqual(ctl.active_theme, "couch_duo")

    def test_known_theme_kept(self):
        ctl = self._load({"theme": "byte_orbit"})
        self.assertEqual(ctl.active_theme, "byte_orbit")


class TestReviewB36PidFiles(Base):
    def _pidfile(self, pid):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = os.path.join(d, "critic.pid")
        with open(path, "w") as f:
            f.write(str(pid))
        return path

    def test_reused_pid_is_not_trusted_and_file_removed(self):
        path = self._pidfile(4242)
        ps = mock.Mock(stdout="/usr/bin/some-other-process --flag\n")
        with mock.patch.object(fv.os, "kill"), \
             mock.patch.object(fv.subprocess, "run", return_value=ps):
            self.assertFalse(fv._critic_running(path))
        self.assertFalse(os.path.exists(path))

    def test_real_critic_pid_is_trusted(self):
        path = self._pidfile(4242)
        ps = mock.Mock(stdout="/usr/bin/python3 /x/masterpiece_critic.py\n")
        with mock.patch.object(fv.os, "kill"), \
             mock.patch.object(fv.subprocess, "run", return_value=ps):
            self.assertTrue(fv._critic_running(path))

    def test_dead_pid(self):
        path = self._pidfile(4242)
        with mock.patch.object(fv.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(fv._critic_running(path))
        self.assertFalse(fv._critic_running(path + ".missing"))

    def test_critic_removes_only_its_own_pid_file(self):
        mc = _import_critic_with_stubs(self)
        path = self._pidfile(os.getpid())
        with mock.patch.object(mc, "PID_FILE", path):
            mc._remove_pid_file()
        self.assertFalse(os.path.exists(path))
        other = self._pidfile(os.getpid() + 100000)
        with mock.patch.object(mc, "PID_FILE", other):
            mc._remove_pid_file()
        self.assertTrue(os.path.exists(other))


class TestReviewB31MacroRecursion(Base):
    def setUp(self):
        super().setUp()
        self._old_cache = fv._macros_cache
        self.addCleanup(setattr, fv, "_macros_cache", self._old_cache)
        self._sleep = mock.patch.object(fv.time, "sleep", return_value=None)
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    def test_self_referencing_macro_terminates(self):
        fv._macros_cache = {"loop": ["loop"]}
        self.assertTrue(fv.run_macro("loop"))
        self.assertIn("That shortcut calls itself, so I stopped it", self.said)

    def test_mutual_recursion_terminates(self):
        fv._macros_cache = {"ping": ["pong"], "pong": ["ping"]}
        self.assertTrue(fv.run_macro("ping"))
        self.assertIn("That shortcut calls itself, so I stopped it", self.said)

    def test_nested_non_recursive_macro_still_runs(self):
        fv._macros_cache = {"outer": ["inner"], "inner": ["volume up"]}
        with mock.patch.object(fv, "act_volume_delta") as delta:
            self.assertTrue(fv.run_macro("outer"))
        delta.assert_called()
        self.assertNotIn("That shortcut calls itself, so I stopped it", self.said)


class TestReviewB32DecisionCriteria(Base):
    def test_close_app_mapped_to_gated_quit_app(self):
        self.assertNotIn("close_app", fv._DECISION_CRITERIA)
        self.assertIn("quit_app", fv._DECISION_CRITERIA)
        self.assertNotIn("quit_app", fv._DESTRUCTIVE_ACTIONS)

    def test_every_criterion_is_dispatchable(self):
        for action in fv._DECISION_CRITERIA:
            if action == "unknown":
                continue
            with mock.patch.object(fv, "act_open_app"), mock.patch.object(fv, "act_switch_app"), \
                 mock.patch.object(fv, "act_quit_app"), mock.patch.object(fv, "act_set_volume"), \
                 mock.patch.object(fv, "act_volume_delta"), mock.patch.object(fv, "act_media"), \
                 mock.patch.object(fv, "act_timer"), mock.patch.object(fv, "act_web_search"):
                self.said.clear()
                fv.dispatch_tier1(action, {"app": "Notes", "query": "x", "level": 10})
                self.assertNotIn("I couldn't map that to an action", self.said, action)


class TestReviewB33CalculateExponent(Base):
    def test_double_star_rejected(self):
        with mock.patch("builtins.eval") as ev:
            fv.act_calculate("9**999999999")
            ev.assert_not_called()
        self.assertIn("Calculation is too large", self.said)

    def test_caret_with_parenthesised_exponent_rejected(self):
        with mock.patch("builtins.eval") as ev:
            fv.act_calculate("9^(99999999)")
            ev.assert_not_called()
        self.assertIn("Exponent is too large", self.said)

    def test_caret_with_huge_exponent_rejected(self):
        fv.act_calculate("9 ^ 999999")
        self.assertIn("Exponent is too large", self.said)

    def test_small_exponent_still_works(self):
        fv.act_calculate("2^10")
        self.assertIn("1024", self.said)
        fv.act_calculate("3 * (4 + 5)")
        self.assertIn("27", self.said)


class TestReviewB34NotificationScreenWidth(Base):
    def _run(self, screen_w, banner_x):
        close_btn = mock.Mock(role="button")
        close_btn.name = "Close"
        group = mock.Mock(bounds=mock.Mock(x=banner_x, y=40, width=344))
        group.name = "Banner"
        group.children.return_value = [close_btn]
        app = mock.Mock()
        app.locator.return_value.elements.side_effect = [[group], []]
        xa = mock.Mock()
        xa.App.by_name.return_value = app
        with mock.patch.object(fv, "_load_xa11y", return_value=xa), \
             mock.patch.object(fv, "_mouse", return_value=(None, None)), \
             mock.patch.object(fv, "_warp_mouse"), \
             mock.patch.object(fv, "_screen_dimensions", return_value=(screen_w, 800)), \
             mock.patch.object(fv.time, "sleep", return_value=None):
            fv.act_close_notifications()
        return close_btn.press.called

    def test_small_display_banner_is_closed(self):
        # 1280-wide display: banner at x=920 was ignored by the old x > 1000 check
        self.assertTrue(self._run(1280, 920))

    def test_left_side_group_ignored(self):
        self.assertFalse(self._run(2560, 300))


class TestReviewS11TtsCache(Base):
    def setUp(self):
        super().setUp()
        self._dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self._dir, True)
        fake_sf = mock.Mock()
        fake_sf.write.side_effect = lambda path, s, sr: open(path, "wb").write(b"RIFF")
        kokoro = mock.Mock()
        kokoro.create.return_value = ([0.0], 24000)
        for p in (mock.patch.dict(sys.modules, {"soundfile": fake_sf}),
                  mock.patch.object(fv, "_tts_cache_dir", self._dir),
                  mock.patch.object(fv, "_get_kokoro", return_value=kokoro),
                  mock.patch.object(fv, "_last_uncached_wav", None)):
            p.start()
            self.addCleanup(p.stop)

    def _cached(self):
        return [f for f in os.listdir(self._dir) if not f.startswith("uncached-")]

    def test_short_phrase_cached(self):
        p1 = fv._synthesize_kokoro("Volume 30")
        p2 = fv._synthesize_kokoro("Volume 30")
        self.assertEqual(p1, p2)
        self.assertEqual(len(self._cached()), 1)

    def test_long_text_not_cached_and_previous_removed(self):
        long1 = "Here is your clipboard: my secret note that should never persist"
        a = fv._synthesize_kokoro(long1)
        self.assertTrue(os.path.exists(a))
        self.assertEqual(self._cached(), [])
        b = fv._synthesize_kokoro(long1 + " again")
        self.assertNotEqual(a, b)
        self.assertFalse(os.path.exists(a))
        self.assertEqual(len(os.listdir(self._dir)), 1)

    def test_cache_is_capped(self):
        with mock.patch.object(fv, "_TTS_CACHE_MAX_FILES", 3):
            for i in range(6):
                fv._synthesize_kokoro(f"phrase {i}")
        self.assertEqual(len(self._cached()), 3)


class TestReviewS12GeminiKeyHeader(Base):
    def _fake_urlopen(self, captured):
        payload = json.dumps({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}).encode()

        def fake(req, timeout=None):
            captured.append(req)
            return io.BytesIO(payload)
        return fake

    def test_gemini_answer_sends_key_in_header(self):
        fv.GEMINI_API_KEY = "test-key-123"
        captured = []
        with mock.patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            self.assertTrue(fv.gemini_answer("what time is it in Tokyo"))
        req = captured[0]
        self.assertNotIn("key=", req.full_url)
        self.assertNotIn("test-key-123", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "test-key-123")

    def test_llm_text_gemini_fallback_sends_key_in_header(self):
        fv.GEMINI_API_KEY = "test-key-123"
        fv._ollama_ok = False
        captured = []
        with mock.patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            out = fv._llm_text("draw a cat")
        gem = [r for r in captured if "generativelanguage" in r.full_url]
        self.assertTrue(gem, captured)
        self.assertNotIn("test-key-123", gem[0].full_url)
        self.assertEqual(gem[0].get_header("X-goog-api-key"), "test-key-123")
        self.assertEqual(out, "hi")


class TestReviewS13PartDownloads(Base):
    def setUp(self):
        super().setUp()
        self._home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self._home, True)
        p = mock.patch.dict(os.environ, {"HOME": self._home})
        p.start()
        self.addCleanup(p.stop)
        self._models = os.path.join(self._home, ".free-voice", "models")
        real_exists = os.path.exists
        # ignore any Homebrew whisper.cpp fixture on a dev Mac (that's B6's concern)
        ex = mock.patch.object(fv.os.path, "exists",
                               side_effect=lambda p: "/Cellar/" not in p and real_exists(p))
        ex.start()
        self.addCleanup(ex.stop)

    def test_download_goes_through_part_file(self):
        seen = []

        def fake_retrieve(url, path):
            seen.append(path)
            with open(path, "wb") as f:
                f.write(b"x" * 10)

        with mock.patch("urllib.request.urlretrieve", side_effect=fake_retrieve):
            target = fv.ensure_ggml_model("tiny.en")
        self.assertTrue(seen[0].endswith(".part"))
        self.assertTrue(os.path.isfile(target))
        self.assertFalse(os.path.exists(target + ".part"))

    def test_failed_download_leaves_nothing(self):
        def fake_retrieve(url, path):
            with open(path, "wb") as f:
                f.write(b"<html>error")
            raise OSError("boom")

        with mock.patch("urllib.request.urlretrieve", side_effect=fake_retrieve):
            self.assertIsNone(fv.ensure_ggml_model("tiny.en"))
        self.assertEqual(os.listdir(self._models), [])

    def test_install_sh_uses_fail_fast_curl_and_part_files(self):
        path = os.path.join(os.path.dirname(__file__), "..", "install.sh")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("curl -sSL -o", src)
        self.assertEqual(src.count("curl -fSL --retry 3"), 2)
        self.assertEqual(src.count('.part"'), 6)


class TestReviewP6Gallery(unittest.TestCase):
    def setUp(self):
        import gallery_server
        self.gs = gallery_server
        self._dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self._dir, True)
        p = mock.patch.object(gallery_server, "CLIPS_DIR", self._dir)
        p.start()
        self.addCleanup(p.stop)

    def test_run_gallery_uses_threading_server_and_cleans_at_startup(self):
        server = mock.Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch.object(self.gs, "ThreadingHTTPServer", return_value=server) as srv, \
             mock.patch.object(self.gs, "cleanup_old_clips") as clean, \
             mock.patch.object(self.gs.threading, "Thread") as thr:
            self.gs.run_gallery()
        srv.assert_called_once_with(("127.0.0.1", self.gs.PORT), self.gs.GalleryHandler)
        clean.assert_called_once()
        thr.return_value.start.assert_called_once()

    def test_requests_do_not_trigger_cleanup(self):
        import threading
        import urllib.request as ur
        httpd = self.gs.ThreadingHTTPServer(("127.0.0.1", 0), self.gs.GalleryHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with mock.patch.object(self.gs, "cleanup_old_clips") as clean, \
             mock.patch.object(self.gs.SimpleHTTPRequestHandler, "log_message"):
            with ur.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/clips", timeout=5) as r:
                self.assertEqual(r.status, 200)
        clean.assert_not_called()


class TestReviewP7AppResolutionCaches(Base):
    def test_phonetic_map_cached_per_app_list(self):
        apps = ["Safari", "Spotify", "Notes"]
        m1 = fv._phonetic_map(apps)
        self.assertIs(fv._phonetic_map(apps), m1)
        self.assertEqual(m1[fv._phonetic_key("Spotify")], "Spotify")
        apps2 = ["Safari", "Spotify", "Notes", "Keynote"]
        self.assertIsNot(fv._phonetic_map(apps2), m1)
        self.assertIn(fv._phonetic_key("Keynote"), fv._phonetic_map(apps2))

    def test_clean_aliases_cached(self):
        self.assertIs(fv._clean_aliases(), fv._clean_aliases())
        self.assertEqual(fv._clean_aliases()["vscode"], "Visual Studio Code")

    def test_resolve_app_phonetic_does_not_recompute(self):
        apps = ["Spotify", "Safari", "Notes"]
        with mock.patch.object(fv, "installed_apps", return_value=apps):
            fv.resolve_app("spotifi")
            with mock.patch.object(fv, "_phonetic_key", wraps=fv._phonetic_key) as pk:
                fv.resolve_app("spotifi")
                # only the spoken variant(s) are keyed, not every installed app
                self.assertLessEqual(pk.call_count, 2)


class TestReviewQ2NoHardcodedUserPaths(unittest.TestCase):
    def test_no_committed_plist_hardcodes_a_home_directory(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for name in os.listdir(root):
            if name.endswith(".plist") and not name.startswith("._"):
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                    self.assertNotIn("/Users/", f.read(), name)

    def test_service_sh_generates_radar_plist(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "service.sh"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("radar-install)", src)
        self.assertIn("${REPO_DIR}/scripts/tech_radar.py", src)


class TestReviewB29ServiceStatusExactLabel(unittest.TestCase):
    def _run_status(self, listing):
        import shutil
        import subprocess
        if not shutil.which("bash"):
            self.skipTest("bash not available")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = os.path.join(tmp, "launchctl")
        with open(fake, "w") as f:
            f.write('#!/bin/bash\n[[ "$1" == list ]] && printf "%b" "' + listing + '"\nexit 0\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, HOME=tmp, PATH=tmp + os.pathsep + os.environ.get("PATH", ""))
        script = os.path.join(os.path.dirname(__file__), "..", "service.sh")
        return subprocess.run(["bash", script, "status"], env=env, capture_output=True,
                              text=True, timeout=20).stdout

    def test_menubar_label_does_not_count_as_main_service(self):
        out = self._run_status("PID\\tStatus\\tLabel\\n4321\\t0\\tcom.free-mac-voice.menubar\\n")
        self.assertIn("NOT INSTALLED", out)

    def test_exact_label_reports_pid(self):
        out = self._run_status("PID\\tStatus\\tLabel\\n4321\\t0\\tcom.free-mac-voice.menubar\\n"
                               "999\\t0\\tcom.free-mac-voice\\n")
        self.assertIn("RUNNING (PID 999)", out)


class TestReviewQ3ToolPaths(unittest.TestCase):
    def test_critic_uses_ffmpeg_from_path(self):
        mc = _import_critic_with_stubs(self)
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)

        class SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._t = target

            def start(self):
                self._t()

        with mock.patch.object(mc, "CLIPS_DIR", tmp), \
             mock.patch.object(mc.threading, "Thread", SyncThread), \
             mock.patch.object(mc.shutil, "which", return_value="/usr/local/bin/ffmpeg"), \
             mock.patch.object(mc.os.path, "exists", return_value=True), \
             mock.patch.object(mc.os, "remove"), \
             mock.patch.object(mc.subprocess, "run") as run, \
             mock.patch("builtins.print"):
            mc.record_clip_async("Leo", "couch_duo", duration=1, force=True)
        cmds = [c.args[0] for c in run.call_args_list]
        self.assertTrue(any(c[0] == "/usr/local/bin/ffmpeg" for c in cmds), cmds)

    def test_whisper_stream_missing_falls_back_to_vad_loop(self):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch.object(fv.os.path, "exists", return_value=False), \
             mock.patch.object(fv, "always_listen_loop") as loop, \
             mock.patch.object(fv, "log"):
            fv.stream_whisper_loop(lambda n, m: None)
        loop.assert_called_once()


class TestReviewQ12SamsungVolumeSteps(unittest.TestCase):
    def test_reports_capped_steps(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="dev"), \
             mock.patch.object(samsung_tv, "_req", return_value={}) as req, \
             mock.patch("builtins.print"):
            msg = samsung_tv.cmd_volume_delta(25)
        self.assertEqual(len(req.call_args.args[2]["commands"]), 10)
        self.assertEqual(msg, "TV volume up by 10")

    def test_small_delta_unchanged(self):
        with mock.patch.object(samsung_tv, "_need_device", return_value="dev"), \
             mock.patch.object(samsung_tv, "_req", return_value={}), \
             mock.patch("builtins.print"):
            self.assertEqual(samsung_tv.cmd_volume_delta(-3), "TV volume down by 3")


class TestReviewB28StateHeartbeat(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(fv, "_last_state", ("", {}))
        p.start()
        self.addCleanup(p.stop)

    def test_processing_returns_to_listening(self):
        fv.update_state("processing", command="open notes")
        fv.heartbeat_state("mac")
        self.assertEqual(fv._last_state[0], "listening")

    def test_wake_window_kept_until_expiry(self):
        fv._wake_window_until = fv.time.time() + 30
        fv.update_state("wake_heard", msg="listening for command")
        fv.heartbeat_state("mac")
        self.assertEqual(fv._last_state, ("wake_heard", {"msg": "listening for command"}))
        fv._wake_window_until = 0.0
        fv.heartbeat_state("mac")
        self.assertEqual(fv._last_state[0], "listening")

    def test_heartbeat_refreshes_timestamp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        path = os.path.join(tmp, "state.json")
        fv.DRY_RUN = False
        with mock.patch.object(fv, "_STATE_FILE", path), \
             mock.patch.object(fv.time, "time", side_effect=[1000.0, 1050.0]):
            fv.update_state("listening", wake_word="mac")
            fv.heartbeat_state("mac")
        with open(path) as f:
            st = json.load(f)
        self.assertEqual(st["state"], "listening")
        self.assertEqual(st["ts"], 1050.0)


class TestReviewQ4CriticOllamaEnvVars(unittest.TestCase):
    def test_critic_reads_ollama_env_vars(self):
        mc = _import_critic_with_stubs(self)
        with mock.patch.dict(os.environ, {
            "OLLAMA_HOST": "http://192.168.1.50:11434/",
            "OLLAMA_MODEL": "my-custom-vision:latest",
            "OLLAMA_DECISION_MODEL": "my-fast-decision:latest",
        }):
            import importlib
            importlib.reload(mc)
            self.assertEqual(mc.OLLAMA_HOST, "http://192.168.1.50:11434")
            self.assertEqual(mc.OLLAMA_MODEL, "my-custom-vision:latest")
            self.assertEqual(mc.OLLAMA_DECISION_MODEL, "my-fast-decision:latest")

    def test_critic_riff_uses_configured_ollama_host_and_models(self):
        mc = _import_critic_with_stubs(self)
        with mock.patch.object(mc, "OLLAMA_HOST", "http://test-ollama:11434"), \
             mock.patch.object(mc, "OLLAMA_MODEL", "custom-vl:8b"), \
             mock.patch.object(mc, "OLLAMA_DECISION_MODEL", "custom-fast:1.5b"), \
             mock.patch("urllib.request.urlopen") as mock_urlopen:
            fake_resp = mock.MagicMock()
            fake_resp.read.return_value = json.dumps({"response": "Nice shot!"}).encode("utf-8")
            fake_resp.__enter__.return_value = fake_resp
            mock_urlopen.return_value = fake_resp

            # Background riff (no user speech) -> should use OLLAMA_DECISION_MODEL
            mc.generate_critic_riff("couch_duo", "Safari", "YouTube")
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "http://test-ollama:11434/api/generate")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["model"], "custom-fast:1.5b")

            # User speech directed riff -> should use OLLAMA_MODEL
            mc.generate_critic_riff("couch_duo", "Safari", "YouTube", user_speech="Hey Leo")
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "http://test-ollama:11434/api/generate")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["model"], "custom-vl:8b")


class TestReviewU9FindMyAndSpotlight(Base):
    def test_route_find_my_phrases(self):
        for phrase in ("find my iPhone", "find my iphone", "find my phone",
                       "find my iPad", "find my mac", "find my device",
                       "find my watch", "find my airpods", "find my keys", "find my"):
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "find_my", f"Expected find_my for '{phrase}', got '{name}'")

    def test_handle_find_my_opens_app(self):
        with mock.patch.object(fv, "act_open_app") as mock_open:
            fv.handle_command("find my iPhone")
            mock_open.assert_called_with("Find My")

    def test_resolve_app_find_my(self):
        self.assertEqual(fv.resolve_app("find my"), "Find My")
        self.assertEqual(fv.resolve_app("find my iphone"), "Find My")

    def test_route_find_file_phrases(self):
        cases = [
            ("find file resume.pdf", "resume.pdf"),
            ("find file budget", "budget"),
            ("find the file taxes.xlsx", "taxes.xlsx"),
            ("search for file report", "report"),
            ("search file presentation", "presentation"),
            ("spotlight notes", "notes"),
            ("spotlight search invoice", "invoice"),
        ]
        for phrase, expected_query in cases:
            r = fv.route(phrase)
            self.assertIsNotNone(r, f"Failed to route: {phrase}")
            name, m = r
            self.assertEqual(name, "find_file", f"Expected find_file for '{phrase}', got '{name}'")
            self.assertEqual(m.group(1).strip(), expected_query)

    def test_handle_find_file_invokes_spotlight(self):
        with mock.patch.object(fv, "act_spotlight_search") as mock_spotlight:
            fv.handle_command("find file resume.pdf")
            mock_spotlight.assert_called_with("resume.pdf")

    def test_act_spotlight_search_keys_and_types(self):
        with mock.patch.object(fv, "act_key_code") as mock_kc, \
             mock.patch.object(fv, "act_type_text") as mock_type, \
             mock.patch.object(fv, "say") as mock_say, \
             mock.patch("time.sleep"):
            fv.act_spotlight_search("resume.pdf")
            mock_kc.assert_called_with(49, "command down")
            mock_type.assert_called_with("resume.pdf")
            mock_say.assert_called_with("Searching for resume.pdf")

    def test_web_search_still_handles_general_queries(self):
        r = fv.route("find recipes for pizza")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "web_search")


class TestReviewDocstringSTTEngine(unittest.TestCase):
    def test_docstring_mentions_mlx_whisper(self):
        self.assertIn("mlx-whisper", fv.__doc__)


class TestNativeWindowAndHUD(unittest.TestCase):
    def test_act_snap_window_uses_native_axuielement(self):
        with mock.patch("free_voice._native_set_front_window_bounds", return_value=True) as mock_native, \
             mock.patch("free_voice.applescript") as mock_as, \
             mock.patch("free_voice.say") as mock_say:
            fv.act_snap_window("left")
            mock_native.assert_called_once()
            mock_as.assert_not_called()
            mock_say.assert_called_once_with("Snapped left")

    def test_act_snap_window_falls_back_to_applescript(self):
        with mock.patch("free_voice._native_set_front_window_bounds", return_value=False) as mock_native, \
             mock.patch("free_voice.applescript") as mock_as, \
             mock.patch("free_voice.say") as mock_say:
            fv.act_snap_window("right")
            mock_native.assert_called_once()
            mock_as.assert_called_once()
            mock_say.assert_called_once_with("Snapped right")

    def test_act_move_next_display_uses_hammerspoon(self):
        with mock.patch("free_voice._notify_hammerspoon_display_next", return_value=True) as mock_hs, \
             mock.patch("free_voice.applescript") as mock_as, \
             mock.patch("free_voice.say") as mock_say:
            fv.act_move_next_display()
            mock_hs.assert_called_once()
            mock_as.assert_not_called()
            mock_say.assert_called_once_with("Moved to next display")

    def test_notify_hud_safe_when_offline(self):
        # Should not raise under any circumstances
        try:
            fv.notify_hud("Test message", "action")
        except Exception as e:
            self.fail(f"notify_hud raised unexpectedly: {e}")


class TestOpusFindingsFixes(unittest.TestCase):
    def test_is_voice_command_fails_closed_on_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=RuntimeError("connection refused")):
            is_cmd, prob = fv.is_voice_command("so anyway what did you think about that")
            self.assertFalse(is_cmd)
            self.assertEqual(prob, 0.0)

    def test_wake_window_followup_blocked_by_command_gate(self):
        # Simulate active wake window
        now = fv.time.time()
        fv._wake_window_until = now + 15.0
        fv._wake_window_opened_at = now

        with mock.patch("free_voice.is_voice_command", return_value=(False, 0.2)) as mock_gate:
            res = fv.handle_command("I was just talking to someone on the phone", require_wake_word=True)
            self.assertFalse(res)
            mock_gate.assert_called_once()

    def test_ask_before_acting_confirmed(self):
        calls = []
        with mock.patch("free_voice.tier05_route", return_value=("open_app", {"app": "Safari"}, 0.60)), \
             mock.patch("free_voice.confirm_spoken", return_value=True) as mock_conf, \
             mock.patch("free_voice.dispatch_tier1", side_effect=lambda a, p, d: calls.append((a, p))):
            fake_audio_fn = lambda d: b""
            handled = fv.handle_command("maybe look into safari", confirm_audio_fn=fake_audio_fn)
            self.assertTrue(handled)
            mock_conf.assert_called_once_with("open Safari", fake_audio_fn)
            self.assertEqual(calls, [("open_app", {"app": "Safari"})])

    def test_ask_before_acting_cancelled(self):
        calls = []
        with mock.patch("free_voice.tier05_route", return_value=("open_app", {"app": "Safari"}, 0.60)), \
             mock.patch("free_voice.confirm_spoken", return_value=False) as mock_conf, \
             mock.patch("free_voice.say") as mock_say, \
             mock.patch("free_voice.dispatch_tier1", side_effect=lambda a, p, d: calls.append((a, p))):
            fake_audio_fn = lambda d: b""
            handled = fv.handle_command("maybe look into safari", confirm_audio_fn=fake_audio_fn)
            self.assertTrue(handled)
            mock_conf.assert_called_once()
            mock_say.assert_called_with("Action cancelled")
            self.assertEqual(calls, [])


class TestSpeakerVerification(unittest.TestCase):
    def test_on_utterance_drops_mismatched_speaker(self):
        import numpy as np
        fake_audio = np.zeros(16000, dtype=np.float32)
        with mock.patch("free_voice.verify_speaker", return_value=(False, 0.20)) as mock_verify, \
             mock.patch("free_voice.transcribe") as mock_transcribe, \
             mock.patch("free_voice.handle_command") as mock_handle:
            fv.on_utterance(fake_audio)
            mock_verify.assert_called_once()
            mock_transcribe.assert_not_called()
            mock_handle.assert_not_called()

    def test_on_utterance_allows_matched_speaker(self):
        import numpy as np
        fake_audio = np.zeros(16000, dtype=np.float32)
        with mock.patch("free_voice.verify_speaker", return_value=(True, 0.85)) as mock_verify, \
             mock.patch("free_voice.transcribe", return_value="open safari") as mock_transcribe, \
             mock.patch("free_voice.handle_command", return_value=True) as mock_handle:
            fv.on_utterance(fake_audio)
            mock_verify.assert_called_once()
            mock_transcribe.assert_called_once()
            mock_handle.assert_called_once_with("open safari", confirm_audio_fn=fv.record_fixed, quiet_miss=False, require_wake_word=False)

    def test_cmd_speaker_status_runs_without_raising(self):
        try:
            fv.cmd_speaker_status()
        except Exception as e:
            self.fail(f"cmd_speaker_status raised unexpectedly: {e}")


class TestVideoPlaybackOnScreen(Base):
    def test_play_video_routes(self):
        r1 = fv.route("play the first video")
        self.assertEqual(r1[0], "play_ordinal_video")
        self.assertEqual(r1[1].group(1), "first")

        r2 = fv.route("play video 3")
        self.assertEqual(r2[0], "play_ordinal_video")
        self.assertEqual(r2[1].group(1), "3")

        r3 = fv.route("choose the second video")
        self.assertEqual(r3[0], "play_ordinal_video")

        r4 = fv.route("play the video about quantum physics")
        self.assertEqual(r4[0], "play_video_on_screen")
        self.assertEqual(r4[1].group(1), "quantum physics")

        r5 = fv.route("play Marques Brownlee")
        self.assertEqual(r5[0], "play_video_on_screen")
        self.assertEqual(r5[1].group(1), "Marques Brownlee")

    def test_play_video_on_screen_executes_dry_run(self):
        with mock.patch.object(fv, "say") as mock_say:
            fv.handle_command("play Marques Brownlee")
            mock_say.assert_called_with("Playing Marques Brownlee")

    def test_play_ordinal_video_executes_dry_run(self):
        with mock.patch.object(fv, "say") as mock_say:
            fv.handle_command("play the first video")
            mock_say.assert_called_with("Playing video 1")





