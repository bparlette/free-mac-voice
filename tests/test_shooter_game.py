"""
tests/test_shooter_game.py
--------------------------
Unit tests for the Rogue Mech Protocol ("shooter") integration.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import free_voice
from integrations.shooter_game.intents import map_voice_to_action
from integrations.shooter_game.shooter_manager import (
    FLAG_FILE,
    VOICE_INPUT_FILE,
    GAME_HTML,
    is_shooter_active,
)


def _clean():
    for p in (FLAG_FILE, VOICE_INPUT_FILE):
        if os.path.exists(p):
            os.remove(p)


class TestShooterIntents(unittest.TestCase):
    def test_actions(self):
        cases = {
            "kick": "kick", "kick the tank": "kick", "stomp": "kick",
            "shoot": "shoot", "fire": "shoot", "shoot the jet": "shoot",
            "vent one": "vent_1", "vent 2": "vent_2", "vent three": "vent_3",
            "vent to": "vent_2", "cool core 3": "vent_3",
            "red": "red", "Blue!": "blue", "it's red": "red",
            "red no blue": "red",
            "start": "start", "try again": "start", "deploy": "start",
            "cap": "select_0", "choose blaze": "select_1", "pick hook": "select_2",
            "pilot sarge": "select_3", "patch": "select_4", "sumo": "select_5",
            "zen": "select_6", "select marshal": "select_7", "pilot 1": "select_0",
            "what's the weather": None, "": None,
        }
        for phrase, want in cases.items():
            self.assertEqual(map_voice_to_action(phrase), want, phrase)


class TestShooterRouting(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def test_start_phrases(self):
        for phrase in ["start shooter game", "play shooter game", "play shooter",
                       "launch rogue mech protocol", "open rogue mech", "start mech game"]:
            res = free_voice.route(phrase)
            self.assertIsNotNone(res, phrase)
            self.assertEqual(res[0], "start_shooter_game", phrase)

    def test_runner_still_routes(self):
        self.assertEqual(free_voice.route("start runner game")[0], "start_runner_game")

    def test_close_phrases(self):
        for phrase in ["close game", "close shooter game", "quit the shooter game"]:
            self.assertEqual(free_voice.route(phrase)[0], "close_runner_game", phrase)

    def test_game_mode_captures_speech(self):
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)
        self.assertTrue(is_shooter_active())
        cmds = ["kick", "shoot", "vent one", "red", "blue"]
        for c in cmds:
            self.assertTrue(free_voice.handle_command(c, quiet_miss=True, require_wake_word=True))
        with open(VOICE_INPUT_FILE) as f:
            self.assertEqual([l.strip() for l in f], cmds)

    @patch("free_voice.act_close_runner_game")
    def test_wake_word_close(self, mock_close):
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)
        self.assertTrue(free_voice.handle_command("Mac, close game", quiet_miss=True, require_wake_word=True))
        mock_close.assert_called_once()

    @patch("free_voice.act_open_app")
    def test_wake_word_passthrough(self, mock_open):
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)
        self.assertTrue(free_voice.handle_command("Mac, open notes", quiet_miss=True, require_wake_word=True))
        mock_open.assert_called_once_with("notes")


class TestShooterAssets(unittest.TestCase):
    def test_game_files_and_protocol(self):
        html = open(GAME_HTML).read()
        self.assertIn("<title>Rogue Mech Protocol</title>", html)
        self.assertIn("ws://localhost:8765", html)
        for a in ["kick", "shoot", "vent_1", "vent_2", "vent_3", "red", "blue"]:
            self.assertIn(a, html)
        assets = os.path.join(os.path.dirname(GAME_HTML), "assets")
        for i in range(8):
            self.assertTrue(os.path.getsize(os.path.join(assets, f"char0{i}.png")) > 1000)


if __name__ == "__main__":
    unittest.main()
