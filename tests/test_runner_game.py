"""
tests/test_runner_game.py
-------------------------
Unit and integration tests for the 3D Voice-Reactive Vector Runner integration in free-mac-voice.
"""

import unittest
import os
import sys
import json
from unittest.mock import patch, MagicMock

# Add repo root to path
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import free_voice
from integrations.runner_game.runner_manager import (
    FLAG_FILE,
    VOICE_INPUT_FILE,
    is_runner_active,
    pipe_voice_to_runner,
)


class TestRunnerGame(unittest.TestCase):
    def setUp(self):
        # Clean test flag and input files
        if os.path.exists(FLAG_FILE):
            os.remove(FLAG_FILE)
        if os.path.exists(VOICE_INPUT_FILE):
            os.remove(VOICE_INPUT_FILE)

    def tearDown(self):
        if os.path.exists(FLAG_FILE):
            os.remove(FLAG_FILE)
        if os.path.exists(VOICE_INPUT_FILE):
            os.remove(VOICE_INPUT_FILE)

    def test_tier0_regex_patterns(self):
        """Verify that launch and close phrases match start_runner_game and close_runner_game."""
        start_phrases = [
            "start runner game",
            "play runner game",
            "open runner game",
            "launch vector runner",
            "start runner",
            "play vector runner game"
        ]
        for phrase in start_phrases:
            res = free_voice.route(phrase)
            self.assertIsNotNone(res, f"Failed to route '{phrase}'")
            name, _ = res
            self.assertEqual(name, "start_runner_game", f"'{phrase}' did not route to start_runner_game (got {name})")

        close_phrases = [
            "close game",
            "quit game",
            "stop runner game",
            "exit game",
            "close runner game"
        ]
        for phrase in close_phrases:
            res = free_voice.route(phrase)
            self.assertIsNotNone(res, f"Failed to route '{phrase}'")
            name, _ = res
            self.assertEqual(name, "close_runner_game", f"'{phrase}' did not route to close_runner_game (got {name})")

    def test_game_mode_inactive(self):
        """When the game is inactive, is_runner_active returns False."""
        self.assertFalse(is_runner_active())

    def test_game_mode_active_exclusive_voice_capture(self):
        """When game is active, in-game speech is routed exclusively to the pipe and skips macOS actions."""
        # Create active flag
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)

        self.assertTrue(is_runner_active())

        game_commands = ["left", "right", "jump", "faster", "slower", "shoot", "change world to neon matrix"]
        for cmd in game_commands:
            handled = free_voice.handle_command(cmd, quiet_miss=True, require_wake_word=True)
            self.assertTrue(handled, f"Game command '{cmd}' was not handled")

        # Verify all game commands were captured in the pipe
        self.assertTrue(os.path.exists(VOICE_INPUT_FILE))
        with open(VOICE_INPUT_FILE, "r") as f:
            lines = [l.strip() for l in f.readlines()]
        self.assertEqual(lines, game_commands)

    @patch("free_voice.act_close_runner_game")
    def test_wake_word_close_game(self, mock_close):
        """When game is active, saying 'Mac, close game' or 'Hey Mac, quit game' calls act_close_runner_game."""
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)

        handled = free_voice.handle_command("Mac, close game", quiet_miss=True, require_wake_word=True)
        self.assertTrue(handled)
        mock_close.assert_called_once()

        mock_close.reset_mock()
        handled2 = free_voice.handle_command("Hey Mac, quit game", quiet_miss=True, require_wake_word=True)
        self.assertTrue(handled2)
        mock_close.assert_called_once()

    @patch("free_voice.act_open_app")
    def test_wake_word_mac_command_passthrough(self, mock_open_app):
        """When game is active, saying 'Mac, open notes' bypasses the game and executes on macOS."""
        with open(FLAG_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "port": 8765}, f)

        handled = free_voice.handle_command("Mac, open notes", quiet_miss=True, require_wake_word=True)
        self.assertTrue(handled)
        mock_open_app.assert_called_once_with("notes")

        # Game pipe should NOT have received 'open notes'
        if os.path.exists(VOICE_INPUT_FILE):
            with open(VOICE_INPUT_FILE, "r") as f:
                lines = [l.strip() for l in f.readlines()]
            self.assertNotIn("open notes", lines)
            self.assertNotIn("notes", lines)


if __name__ == "__main__":
    unittest.main()
