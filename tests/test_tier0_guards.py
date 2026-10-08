"""Tier 0 regex tightening (benchmarks/pipeline_eval + intent_eval): adversarial non-commands must not match, real commands must."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402

KNOWN = {"safari": "Safari", "chrome": "Google Chrome", "music": "Music", "zoom": "zoom.us", "spotify": "Spotify", "finder": "Finder",
         "notes": "Notes", "photos": "Photos"}


def fake_resolver(spoken, prefer_running=False):
    s = fv._clean_app_phrase(spoken).lower()
    s = s[4:] if s.startswith("the ") else s
    s = s[:-4] if s.endswith(" app") else s
    return KNOWN.get(s.strip())


class TestFalseTriggersAreGone(unittest.TestCase):
    NARRATION = [
        "open the door, it's me", "open your heart to the possibility", "open sesame, said the magician", "open an account in minutes",
        "close the deal before Friday or we lose them", "close but no cigar", "close the curtains, the neighbours are watching",
        "quit smoking with the help of our patch", "minimize your losses and get out", "minimize wrinkles in just two weeks",
        "minimise the risk of fire by unplugging chargers", "play it cool, they don't know anything",
        "play next to the river, kids, not in it", "hit subscribe, it really helps the show",
        "hit play on summer with spotify premium", "google says it takes forty minutes", "search no further, the perfect mattress is here",
        "look up our store hours online",
    ]

    def test_narration_does_not_match_tier0(self):
        with mock.patch.object(fv, "resolve_app", side_effect=fake_resolver):
            for t in self.NARRATION:
                self.assertIsNone(fv.route(t), t)


class TestRealCommandsStillRoute(unittest.TestCase):
    def test_app_commands_with_filler(self):
        cases = {"open safari": "open_app", "launch chrome please": "open_app", "start up zoom": "open_app", "open up the music app": "open_app",
                 "quit spotify": "quit_app", "switch to finder": "switch_app", "hide notes": "hide_app", "minimize photos": "minimize_app"}
        with mock.patch.object(fv, "resolve_app", side_effect=fake_resolver):
            for t, want in cases.items():
                r = fv.route(t)
                self.assertIsNotNone(r, t)
                self.assertEqual(r[0], want, t)

    def test_short_hit_and_search_and_play_still_work(self):
        with mock.patch.object(fv, "resolve_app", side_effect=fake_resolver):
            self.assertEqual(fv.route("hit allow")[0], "click_any")
            self.assertEqual(fv.route("search for banana bread recipe")[0], "web_search")
            self.assertEqual(fv.route("google banana bread recipe")[0], "web_search")
            self.assertEqual(fv.route("play the video about cats")[0], "play_video_on_screen")


class TestMisroutesNowFallThrough(unittest.TestCase):
    def test_tab_window_and_timer_phrases_are_not_app_commands(self):
        with mock.patch.object(fv, "resolve_app", side_effect=fake_resolver):
            for t in ("close the current tab", "close that tab mate", "close the popup", "start a five minute timer", "close this one"):
                r = fv.route(t)
                self.assertTrue(r is None or r[0] not in fv._APP_RULES, (t, r))


class TestChainStepsStayPermissive(unittest.TestCase):
    def test_unknown_app_step_still_matches_for_chains(self):
        with mock.patch.object(fv, "resolve_app", side_effect=fake_resolver):
            self.assertIsNone(fv.route("open nonexistentapp"))
            r = fv.route("open nonexistentapp", strict_apps=False)
            self.assertEqual(r[0], "open_app")


class TestAppPhraseCleaning(unittest.TestCase):
    def test_clean_phrase(self):
        self.assertEqual(fv._clean_app_phrase("up zoom"), "zoom")
        self.assertEqual(fv._clean_app_phrase("chrome please"), "chrome")
        self.assertEqual(fv._clean_app_phrase("safari for me right now"), "safari")
        self.assertEqual(fv._clean_app_phrase("the music app"), "the music app")

    def test_resolve_app_uses_cleaning_and_new_aliases(self):
        self.assertEqual(fv.resolve_app("chrome please"), "Google Chrome")
        self.assertEqual(fv.resolve_app("sys prefs"), "System Settings")
        self.assertEqual(fv.resolve_app("system prefs"), "System Settings")


if __name__ == "__main__":
    unittest.main()
