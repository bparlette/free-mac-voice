"""Wake word: strict spellings always wake; loose spellings (Mike, Mark, Make, ...) wake only when a real command follows (benchmarks/wake_eval)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402


def route_only(known):
    return lambda text, *a, **k: ("x", None) if text.strip().lower().rstrip(".!?") in known else None


class TestWakeSpellings(unittest.TestCase):
    def setUp(self):
        self.known = {"open notes", "mute the tv", "lock the screen", "turn the volume down"}
        for patcher in (mock.patch.object(fv, "route", side_effect=route_only(self.known)),
                        mock.patch.object(fv, "tier05_embed_match", return_value=None),
                        mock.patch.object(fv, "VOICE_WAKE_LOOSE", "command")):
            patcher.start(); self.addCleanup(patcher.stop)

    def test_strict_spellings_always_wake(self):
        for t in ("Mac, anything at all", "Mack open notes", "Hey Mac, what is this", "Macs, hello", "Mac"):
            self.assertTrue(fv.parse_wake_word(t, "mac")[0], t)

    def test_loose_spelling_before_a_real_command_wakes(self):
        for t, cmd in (("Make, open notes", "open notes"), ("Mike open notes", "open notes"), ("Max, lock the screen", "lock the screen")):
            self.assertEqual(fv.parse_wake_word(t, "mac"), (True, cmd), t)

    def test_loose_spelling_in_ordinary_speech_does_not_wake(self):
        for t in ("Make sure you lock the door", "Mark my words, you'll regret this", "Match point, Federer", "Max out your credit card",
                  "Mike, I can't hear myself think", "Mike", "Hey Mike", "Make it quick"):
            is_wake, cmd = fv.parse_wake_word(t, "mac")
            self.assertFalse(is_wake, t)
            self.assertEqual(cmd, t)

    def test_narrative_comma_after_a_command_is_not_a_command(self):
        # "turn the volume down" alone is a command, but followed by a new clause it is narration
        self.assertFalse(fv.parse_wake_word("Mike, turn the volume down, I can't hear myself think", "mac")[0])

    def test_modes(self):
        with mock.patch.object(fv, "VOICE_WAKE_LOOSE", "always"):
            self.assertTrue(fv.parse_wake_word("Make sure you lock the door", "mac")[0])
        with mock.patch.object(fv, "VOICE_WAKE_LOOSE", "off"):
            self.assertFalse(fv.parse_wake_word("Mike open notes", "mac")[0])
            self.assertTrue(fv.parse_wake_word("Mack open notes", "mac")[0])

    def test_custom_wake_words_are_unaffected(self):
        self.assertEqual(fv.parse_wake_word("Computer, status", "computer"), (True, "status"))
        self.assertFalse(fv.parse_wake_word("Mike, open notes", "computer")[0])


if __name__ == "__main__":
    unittest.main()
