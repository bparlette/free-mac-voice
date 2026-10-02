import unittest
from unittest import mock
import re
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import free_voice as fv
from masterpiece_critic import CompanionVoiceListener
import gallery_server


class TestAuditRegressions(unittest.TestCase):
    """Permanent regression guards for the 11 critical issues caught during audit."""

    def setUp(self):
        self._dry = fv.DRY_RUN
        fv.DRY_RUN = True
        self.said = []
        self._say = fv.say
        fv.say = lambda text, *a, **kw: self.said.append(text)


    def tearDown(self):
        fv.DRY_RUN = self._dry
        fv.say = self._say
        mock.patch.stopall()

    # 1. Negative Confirmation Guard
    def test_confirmation_rejects_negatives(self):
        fake_audio = lambda s: None
        with mock.patch("free_voice.transcribe", return_value="no, don't do it"):
            self.assertFalse(fv.confirm_spoken("shutdown", fake_audio))
        with mock.patch("free_voice.transcribe", return_value="cancel that"):
            self.assertFalse(fv.confirm_spoken("shutdown", fake_audio))
        with mock.patch("free_voice.transcribe", return_value="my eyes"):
            self.assertFalse(fv.confirm_spoken("shutdown", fake_audio))
        with mock.patch("free_voice.transcribe", return_value="yes please"):
            self.assertTrue(fv.confirm_spoken("shutdown", fake_audio))

    # 2. Voice Macro Shell Injection Guard
    def test_voice_macro_cannot_save_shell(self):
        fv.act_macro_add("hack", "bash ~/dangerous.sh")
        self.assertNotIn("hack", fv._load_macros())
        self.assertTrue(any("cannot execute shell" in s for s in self.said))

    # 3. Calculator vs. General Q&A Routing
    def test_calculator_does_not_hijack_general_questions(self):
        # Plain arithmetic should route to calculate
        self.assertEqual(fv.route("calculate 5 * 8")[0], "calculate")
        self.assertEqual(fv.route("what is 10 + 20")[0], "calculate")
        self.assertEqual(fv.route("what's 50 / 2")[0], "calculate")

        # General questions starting with 'what is' must NOT route to calculate
        self.assertIsNone(fv.route("what is the capital of France"))
        self.assertIsNone(fv.route("what is the weather like in Seattle"))
        self.assertIsNone(fv.route("what's your favorite color"))

    # 4. Snap Window Left / Tile Window Right Regex Fix
    def test_snap_window_variations(self):
        self.assertEqual(fv.route("snap left")[0], "snap_left")
        self.assertEqual(fv.route("snap window left")[0], "snap_left")
        self.assertEqual(fv.route("tile window right")[0], "snap_right")
        self.assertEqual(fv.route("snap this window to the top half")[0], "snap_top")
        self.assertEqual(fv.route("tile window bottom")[0], "snap_bottom")

    # 5. Critic Hot-Word Boundary & False Positives
    def test_critic_hotword_word_boundaries(self):
        listener = CompanionVoiceListener(None, lambda: False)
        # Valid names & greetings
        self.assertTrue(listener._is_hot_word("leo"))
        self.assertTrue(listener._is_hot_word("hey leo"))
        self.assertTrue(listener._is_hot_word("cleo, what do you think"))
        self.assertTrue(listener._is_hot_word("hi toby"))

        # False positives must NOT trigger hot-word bypass
        self.assertFalse(listener._is_hot_word("leopard"))
        self.assertFalse(listener._is_hot_word("orbiting the earth"))
        self.assertFalse(listener._is_hot_word("cleopatra"))

    # 6. Multi-Action Chain Aborts On Failure
    def test_chain_aborts_on_step_failure(self):
        calls = []
        def fail_step(*args, **kwargs):
            raise RuntimeError("Window not found")

        with mock.patch("free_voice.act_open_app", side_effect=fail_step), \
             mock.patch("free_voice.act_snap_window", side_effect=lambda s: calls.append("snap")):
            handled = fv.handle_command("open nonexistentapp and snap left")
            self.assertTrue(handled)
            # The second step must NEVER execute if step 1 failed
            self.assertEqual(calls, [])
            self.assertTrue(any("stopping chain" in s for s in self.said))

    # 7. Gallery Server Binds To Localhost Only
    def test_gallery_server_default_host(self):
        orig_env = os.environ.get("GALLERY_HOST")
        try:
            if "GALLERY_HOST" in os.environ:
                del os.environ["GALLERY_HOST"]
            with mock.patch("gallery_server.ThreadingHTTPServer") as mock_server:
                gallery_server.run_gallery()
                mock_server.assert_called_once()
                addr, port = mock_server.call_args[0][0]
                self.assertEqual(addr, "127.0.0.1")
                self.assertEqual(port, 8765)
        finally:
            if orig_env is not None:
                os.environ["GALLERY_HOST"] = orig_env


if __name__ == "__main__":
    unittest.main()
