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


class Base(unittest.TestCase):
    def setUp(self):
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

    def tearDown(self):
        fv.DRY_RUN = self._dry
        fv.say = self._say
        fv._ollama_ok = self._ollama_ok
        fv.GEMINI_API_KEY = self._gemini_key
        fv._last_error = self._last_error
        fv._shot_ts = self._shot_ts

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
             mock.patch("time.sleep"):
            # second half matches nothing -> NOTHING runs, not even part one
            fv.handle_command("open notes and zzzqqq nonsense", quiet_miss=True)
        self.assertEqual(opened, [])


# ---------------------------------------------------------------- earcons (new)
class TestEarcons(Base):
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
        # code default when OLLAMA_MODEL is unset in the environment
        self.assertEqual(os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b"),
                         "qwen3-vl:8b")
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

    def test_unreachable_ollama_returns_none(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=ConnectionRefusedError("nope")):
            self.assertIsNone(fv.ollama_route("open notes please"))
        self.assertFalse(fv._ollama_ok)


# ------------------------------------------------------- Tier 2 / Gemini
class TestGemini(Base):
    def _http_error(self, code):
        return urllib.error.HTTPError("http://x", code, "err", {}, None)

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

    def test_quit_app_confirmed_runs(self):
        _, qa, _ = self._exec("quit spotify", confirm=True)
        qa.assert_called_once()

    def test_quit_app_declined_skips(self):
        cs, qa, _ = self._exec("quit spotify", confirm=False)
        qa.assert_not_called()
        cs.assert_called_once()
        self.assertTrue(any("without confirmation" in s for s in self.said))

    def test_close_all_windows_confirmed_runs(self):
        _, _, caw = self._exec("close all windows", confirm=True)
        caw.assert_called_once()

    def test_close_all_windows_declined_skips(self):
        _, _, caw = self._exec("close all windows", confirm=False)
        caw.assert_not_called()
        self.assertTrue(any("without confirmation" in s for s in self.said))

    def test_allow_destructive_skips_prompt(self):
        cs, qa, _ = self._exec("quit spotify", allow_destructive=True)
        qa.assert_called_once()
        cs.assert_not_called()

    def test_tier1_quit_app_allow_destructive(self):
        with mock.patch.object(fv, "act_quit_app") as qa:
            fv.dispatch_tier1("quit_app", {"app": "safari"},
                              allow_destructive=True)
        qa.assert_called_once_with("safari")

    def test_tier1_quit_app_declines_without_mic(self):
        fv.DRY_RUN = False
        try:
            with mock.patch.object(fv, "record_fixed",
                                   side_effect=RuntimeError("no mic")), \
                 mock.patch.object(fv, "act_quit_app") as qa:
                fv.dispatch_tier1("quit_app", {"app": "safari"})
            qa.assert_not_called()
            self.assertTrue(any("without confirmation" in s for s in self.said))
        finally:
            fv.DRY_RUN = True


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
        captured = {}
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"{}"

        def fake(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
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
        body = captured["body"]
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


if __name__ == "__main__":
    unittest.main()
