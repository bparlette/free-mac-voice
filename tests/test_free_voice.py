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

    def tearDown(self):
        fv.DRY_RUN = self._dry
        fv.say = self._say
        fv._ollama_ok = self._ollama_ok
        fv.GEMINI_API_KEY = self._gemini_key

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
        with mock.patch.object(fv, "capture_screenshot", return_value="/tmp/fake.png"), \
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

    def test_non_429_error_does_not_fall_back(self):
        fv.GEMINI_API_KEY = "test-key"
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._http_error(500)), \
             mock.patch.object(fv, "ollama_answer") as local:
            self.assertFalse(fv.gemini_answer("who won last night"))
        local.assert_not_called()


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


if __name__ == "__main__":
    unittest.main()
