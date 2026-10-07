#!/usr/bin/env python3
"""Regressions for the mic watchdog, Whisper prompt echo, chain gate and
Tier 0.5a embedding robustness fixes."""

import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import free_voice as fv


class TestPromptEcho(unittest.TestCase):
    def test_real_echoes_detected(self):
        for t in ("Safari, Chrome, Finder, Terminal, Notes, System Settings, snap right,",
                  "Safari, Chrome, Finder, Terminal, Notes, System Settings, snap right, maximize, volume",
                  "Mac, Safari, Chrome",
                  "snap left, snap right, maximize."):
            self.assertTrue(fv.is_prompt_echo(t), t)

    def test_real_commands_pass(self):
        for t in ("open Safari and snap left", "snap left", "Safari, Chrome",
                  "open Chrome, Safari and Finder", "maximize", "volume up",
                  "left, right, jump", "open system settings"):
            self.assertFalse(fv.is_prompt_echo(t), t)

    def test_on_utterance_drops_echo(self):
        audio = np.zeros(16000, dtype=np.float32)
        with mock.patch.object(fv, "verify_speaker", return_value=(True, 1.0)), \
             mock.patch.object(fv, "is_any_game_active", return_value=False), \
             mock.patch.object(fv, "transcribe",
                               return_value="Mac, Safari, Chrome, Finder, Terminal, Notes"), \
             mock.patch.object(fv, "record_attempt"), \
             mock.patch.object(fv, "handle_command") as hc:
            fv.on_utterance(audio, quiet_miss=True, require_wake_word=True)
        hc.assert_not_called()


class TestChainGate(unittest.TestCase):
    def test_llm_chain_needs_command_gate(self):
        with mock.patch.object(fv, "is_voice_command", return_value=(False, 0.1)), \
             mock.patch.object(fv, "ollama_multi_action_plan") as plan, \
             mock.patch.object(fv, "ollama_route", return_value=None), \
             mock.patch.object(fv, "tier05_route", return_value=None), \
             mock.patch.object(fv, "say"), \
             mock.patch("time.sleep"):
            fv.handle_command("well the dog, the cat, and the weather", quiet_miss=True)
        plan.assert_not_called()


class TestMicWatchdog(unittest.TestCase):
    def test_responsive_mic_returned(self):
        with mock.patch.object(fv, "_probe_input", return_value=True):
            self.assertEqual(fv.ensure_responsive_input(3), 3)

    def test_falls_back_to_other_input_and_warns(self):
        with mock.patch.object(fv, "_probe_input", side_effect=lambda i: i == 5), \
             mock.patch.object(fv, "_other_inputs", return_value=[4, 5]), \
             mock.patch.object(fv, "_input_name", return_value="USB mic"), \
             mock.patch.object(fv, "update_state") as us, \
             mock.patch.object(fv, "notify_hud"), \
             mock.patch.object(fv, "say") as say:
            fv._last_mic_stuck_say = 0.0
            self.assertEqual(fv.ensure_responsive_input(None), 5)
        self.assertEqual(us.call_args[0][0], "mic_stuck")
        say.assert_called_once()

    def test_waits_until_replugged(self):
        results = iter([False, False, True])
        with mock.patch.object(fv, "_probe_input", side_effect=lambda i: next(results)), \
             mock.patch.object(fv, "_other_inputs", return_value=[]), \
             mock.patch.object(fv, "_input_name", return_value="USB mic"), \
             mock.patch.object(fv, "update_state"), \
             mock.patch.object(fv, "notify_hud"), \
             mock.patch.object(fv, "say"), \
             mock.patch.object(fv.time, "sleep") as sl:
            self.assertIsNone(fv.ensure_responsive_input(None))
        self.assertEqual(sl.call_count, 2)

    def test_probe_timeout_is_false(self):
        import subprocess
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("py", 1)):
            self.assertFalse(fv._probe_input(None, timeout=1))


class TestEmbedRobustness(unittest.TestCase):
    def setUp(self):
        self._saved = (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS,
                       fv._EMBED_NEURAL, fv._embed_last_try)
        fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS = None, []
        fv._EMBED_NEURAL, fv._embed_last_try = False, 0.0

    def tearDown(self):
        (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS,
         fv._EMBED_NEURAL, fv._embed_last_try) = self._saved

    def test_neural_retried_after_ollama_comes_up(self):
        n = sum(len(v) for v in fv._INTENT_EXAMPLES.values())
        neural = np.eye(n, 8, dtype=np.float32)
        with mock.patch.object(fv, "OLLAMA_EMBED_MODEL", "embeddinggemma"), \
             mock.patch.object(fv, "_ollama_embed", side_effect=[None, neural]) as oe:
            fv._init_intent_embeddings()
            self.assertFalse(fv._EMBED_NEURAL)
            fv._init_intent_embeddings()  # within retry window: no new call
            self.assertEqual(oe.call_count, 1)
            fv._embed_last_try -= fv._EMBED_RETRY_SEC + 1
            fv._init_intent_embeddings()
        self.assertTrue(fv._EMBED_NEURAL)
        self.assertEqual(fv._PRECOMPUTED_MATRIX.shape, (n, 8))

    def test_query_uses_short_timeout(self):
        n = sum(len(v) for v in fv._INTENT_EXAMPLES.values())
        with mock.patch.object(fv, "OLLAMA_EMBED_MODEL", "embeddinggemma"), \
             mock.patch.object(fv, "_ollama_embed",
                               side_effect=[np.eye(n, 8, dtype=np.float32), None]) as oe:
            fv.tier05_embed_match("open safari")
        self.assertEqual(oe.call_args.kwargs.get("timeout"), fv._EMBED_QUERY_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
