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
        self.addCleanup(setattr, fv, "TIER05_EMBED_BACKEND", fv.TIER05_EMBED_BACKEND)
        fv.TIER05_EMBED_BACKEND = "ollama"  # these tests are about the Ollama path
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


class TestSpeakerBackends(unittest.TestCase):
    """CAM++ / legacy backend selection, enrollment and verification (no real models)."""

    def setUp(self):
        import tempfile
        import speaker_id as sid
        self.sid = sid
        self.tmp = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(sid, "CAMPLUS_PROFILE_PATH", os.path.join(self.tmp, "campplus.npy")),
            mock.patch.object(sid, "DEFAULT_PROFILE_PATH", os.path.join(self.tmp, "legacy.npy")),
            mock.patch.object(sid, "SPEAKER_BACKEND", "auto"),
            mock.patch.object(sid, "_have_fbank", return_value=True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        sid._profile_cache.clear()

    def _fake_embed(self, vec):
        return mock.patch.object(self.sid, "extract_embedding",
                                 side_effect=lambda a, **kw: np.asarray(vec, dtype=np.float32))

    def test_legacy_until_campplus_profile_exists(self):
        self.assertEqual(self.sid.active_backend(), "pyannote")
        np.save(self.sid.CAMPLUS_PROFILE_PATH, np.ones(512, dtype=np.float32))
        self.assertEqual(self.sid.active_backend(), "campplus")
        self.assertEqual(self.sid.get_threshold(), self.sid.CAMPLUS_THRESHOLD)

    def test_no_fbank_stays_legacy(self):
        np.save(self.sid.CAMPLUS_PROFILE_PATH, np.ones(512, dtype=np.float32))
        with mock.patch.object(self.sid, "_have_fbank", return_value=False):
            self.assertEqual(self.sid.active_backend(), "pyannote")

    def test_enroll_writes_both_profiles_and_switches(self):
        audio = [np.random.RandomState(0).randn(16000 * 5).astype(np.float32) * 0.1]
        with self._fake_embed(np.eye(512)[0]):
            c = self.sid.enroll_speaker(audio)
        self.assertAlmostEqual(c, 1.0, places=3)
        self.assertTrue(os.path.isfile(self.sid.CAMPLUS_PROFILE_PATH))
        self.assertTrue(os.path.isfile(self.sid.DEFAULT_PROFILE_PATH))
        self.assertEqual(self.sid.active_backend(), "campplus")

    def test_verify_accepts_and_rejects_by_threshold(self):
        np.save(self.sid.CAMPLUS_PROFILE_PATH, np.eye(512, dtype=np.float32)[0])
        v = np.zeros(512, dtype=np.float32)
        v[0], v[1] = 0.8, 0.6
        with self._fake_embed(v):
            ok, sim = self.sid.verify_speaker(np.zeros(16000, dtype=np.float32))
        self.assertTrue(ok)
        self.assertAlmostEqual(sim, 0.8, places=3)
        w = np.zeros(512, dtype=np.float32)
        w[0], w[1] = 0.1, 0.99
        with self._fake_embed(w):
            ok, sim = self.sid.verify_speaker(np.zeros(16000, dtype=np.float32))
        self.assertFalse(ok)

    def test_mismatched_profile_dim_fails_open_not_crash(self):
        np.save(self.sid.CAMPLUS_PROFILE_PATH, np.ones(64, dtype=np.float32))
        with self._fake_embed(np.ones(512)):
            self.assertEqual(self.sid.verify_speaker(np.zeros(16000, dtype=np.float32)), (True, 1.0))

    def test_reset_removes_both(self):
        np.save(self.sid.CAMPLUS_PROFILE_PATH, np.ones(512, dtype=np.float32))
        np.save(self.sid.DEFAULT_PROFILE_PATH, np.ones(512, dtype=np.float32))
        self.assertTrue(self.sid.reset_speaker())
        self.assertFalse(os.path.exists(self.sid.CAMPLUS_PROFILE_PATH))
        self.assertFalse(os.path.exists(self.sid.DEFAULT_PROFILE_PATH))


class TestCampplusRealModel(unittest.TestCase):
    """Runs only where the CAM++ model is already downloaded (e.g. the live Mac)."""

    def test_same_speaker_scores_above_different(self):
        import speaker_id as sid
        if not (os.path.isfile(sid.CAMPLUS_MODEL_PATH) and sid._have_fbank()):
            self.skipTest("CAM++ model / kaldi-native-fbank not installed")
        import soundfile as sf
        import scipy.signal as ss
        root = os.path.join(os.path.dirname(__file__), "..", "docs", "audio_samples")

        def emb(name, part):
            a, sr = sf.read(os.path.join(root, f"{name}_sample.wav"), dtype="float32")
            a = a if a.ndim == 1 else a.mean(1)
            a = ss.resample_poly(a, 16000, sr).astype(np.float32) if sr != 16000 else a
            h = len(a) // 2
            return sid.extract_embedding(a[:h] if part == 0 else a[h:], backend="campplus")

        n0, n1, m0 = emb("nicole", 0), emb("nicole", 1), emb("michael", 0)
        self.assertGreater(float(n0 @ n1), float(n0 @ m0) + 0.15)
