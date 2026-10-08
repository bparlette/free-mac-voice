"""Polite phrasing ('can you launch chrome please') must still resolve the app for Tier 0.5a app commands."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402


class TestStripPoliteness(unittest.TestCase):
    def test_examples(self):
        cases = {
            "can you launch chrome please": "launch chrome",
            "could you please open safari for me": "open safari",
            "hey mac can you quit spotify": "quit spotify",
            "would you switch to notes right now": "switch to notes",
            "please close slack.": "close slack",
            "i'd like you to open the terminal": "open the terminal",
            "open safari": "open safari",
        }
        for src, want in cases.items():
            self.assertEqual(fv._strip_politeness(src), want, src)

    def test_never_returns_empty(self):
        self.assertEqual(fv._strip_politeness("please"), "please")


class TestAppParamsPolite(unittest.TestCase):
    @staticmethod
    def _resolver(known):
        return lambda spoken, prefer_running=False: known.get(spoken.strip().lower())

    def test_polite_open_resolves_app(self):
        with mock.patch.object(fv, "resolve_app", side_effect=self._resolver({"chrome": "Google Chrome"})):
            p = fv._extract_intent_params("open_app", "can you launch chrome please")
        self.assertEqual(p["app"], "Google Chrome")

    def test_polite_quit_and_switch(self):
        res = self._resolver({"spotify": "Spotify", "notes": "Notes"})
        with mock.patch.object(fv, "resolve_app", side_effect=res):
            self.assertEqual(fv._extract_intent_params("quit_app", "hey mac could you quit spotify for me")["app"], "Spotify")
            self.assertEqual(fv._extract_intent_params("switch_app", "would you switch to notes")["app"], "Notes")

    def test_phrasal_verbs(self):
        res = self._resolver({"zoom": "zoom.us", "safari": "Safari"})
        with mock.patch.object(fv, "resolve_app", side_effect=res):
            self.assertEqual(fv._extract_intent_params("open_app", "start up zoom")["app"], "zoom.us")
            self.assertEqual(fv._extract_intent_params("open_app", "fire up safari")["app"], "Safari")

    def test_plain_command_unchanged_and_unknown_app_still_empty(self):
        with mock.patch.object(fv, "resolve_app", side_effect=self._resolver({"chrome": "Google Chrome"})):
            self.assertEqual(fv._extract_intent_params("open_app", "open chrome")["app"], "Google Chrome")
            self.assertEqual(fv._extract_intent_params("open_app", "can you open the thingamajig please")["app"], "")


if __name__ == "__main__":
    unittest.main()
