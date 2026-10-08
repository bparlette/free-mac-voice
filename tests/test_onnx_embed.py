"""Tier 0.5a ONNX backend and compare mode (fake embedders; one real-model test that skips if the model is absent)."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402
import onnx_embed  # noqa: E402

N = sum(len(v) for v in fv._INTENT_EXAMPLES.values())


def _fake_matrix(seed, dim=8):
    m = np.random.RandomState(seed).randn(N, dim).astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try,
                       fv._SHADOW_MATRIX, fv._warned_onnx_missing)
        fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS = None, []
        fv._EMBED_NEURAL, fv._embed_last_try, fv._SHADOW_MATRIX, fv._warned_onnx_missing = False, 0.0, None, False
        st = fv._COMPARE_STATS
        st.update(n=0, agree=0, ollama_only=0, onnx_only=0)
        st["ollama_ms"].clear(); st["onnx_ms"].clear()
        self.logs = []
        self._log = mock.patch.object(fv, "log", side_effect=self.logs.append)
        self._log.start()

    def tearDown(self):
        self._log.stop()
        (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try,
         fv._SHADOW_MATRIX, fv._warned_onnx_missing) = self._saved


class TestBackendSelection(_Base):
    def test_default_is_onnx(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "TIER05_EMBED_BACKEND"}
        out = subprocess.run([sys.executable, "-c", "import free_voice as f; print(f.TIER05_EMBED_BACKEND)"],
                             cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), env=env,
                             capture_output=True, text=True, timeout=120).stdout.strip().splitlines()[-1]
        self.assertEqual(out, "onnx")

    def test_ollama_backend_never_uses_onnx(self):
        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "ollama"):
            self.assertFalse(fv._use_onnx())

    def test_onnx_requested_but_files_missing_falls_back_and_warns_once(self):
        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "onnx"), \
             mock.patch.object(onnx_embed, "available", return_value=False):
            self.assertFalse(fv._use_onnx())
            self.assertFalse(fv._use_onnx())
        self.assertEqual(sum("model is missing" in m for m in self.logs), 1)

    def test_onnx_backend_builds_matrix_and_queries_without_ollama(self):
        calls = []

        def fake_onnx(texts):
            calls.append(list(texts))
            return _fake_matrix(1) if len(texts) == N else np.ones((len(texts), 8), np.float32) / np.sqrt(8)

        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "onnx"), \
             mock.patch.object(onnx_embed, "available", return_value=True), \
             mock.patch.object(fv, "_onnx_embed", side_effect=fake_onnx), \
             mock.patch.object(fv, "_ollama_embed", side_effect=AssertionError("Ollama must not be used")):
            fv._init_intent_embeddings()
            self.assertTrue(fv._EMBED_NEURAL)
            self.assertEqual(fv._PRECOMPUTED_MATRIX.shape, (N, 8))
            fv.tier05_embed_match("open safari")
        self.assertEqual(len(calls), 2)  # examples once, then the query

    def test_onnx_default_without_model_files_behaves_like_ollama(self):
        n = sum(len(v) for v in fv._INTENT_EXAMPLES.values())
        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "onnx"), \
             mock.patch.object(onnx_embed, "available", return_value=False), \
             mock.patch.object(fv, "OLLAMA_EMBED_MODEL", "embeddinggemma"), \
             mock.patch.object(fv, "_onnx_embed", side_effect=AssertionError("ONNX must not run")), \
             mock.patch.object(fv, "_ollama_embed", return_value=_fake_matrix(3)) as oe:
            fv._init_intent_embeddings()
        self.assertTrue(fv._EMBED_NEURAL)
        self.assertEqual(oe.call_count, 1)

    def test_onnx_embed_adds_the_task_prefix_and_validates_shape(self):
        class Fake:
            def embed(self, texts):
                self.seen = texts
                return np.zeros((len(texts) + 1, 4), np.float32)  # wrong row count

        fake = Fake()
        with mock.patch.object(onnx_embed, "get_embedder", return_value=fake):
            self.assertIsNone(fv._onnx_embed(["a", "b"]))
        self.assertEqual(fake.seen, [fv._EMBED_PREFIX + "a", fv._EMBED_PREFIX + "b"])

    def test_onnx_embed_never_raises(self):
        with mock.patch.object(onnx_embed, "get_embedder", side_effect=RuntimeError("boom")):
            self.assertIsNone(fv._onnx_embed(["x"]))


class TestCompareMode(_Base):
    def _run(self, texts, onnx_query_sim=None, onnx_fail=False):
        """Primary (fake Ollama) matrix is _fake_matrix(1); the ONNX shadow shares it, so queries agree unless altered."""
        primary = _fake_matrix(1)

        def fake_ollama(batch, timeout=30.0):
            if len(batch) == N:
                return primary
            return primary[[0]]  # query identical to example 0

        def fake_onnx(batch):
            if onnx_fail:
                return None
            if len(batch) == N:
                return primary
            return primary[[0]] if onnx_query_sim is None else _fake_matrix(99)[[0]]

        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "compare"), \
             mock.patch.object(fv, "OLLAMA_EMBED_MODEL", "embeddinggemma"), \
             mock.patch.object(fv, "_ollama_embed", side_effect=fake_ollama), \
             mock.patch.object(fv, "_onnx_embed", side_effect=fake_onnx):
            return [fv.tier05_embed_match(t) for t in texts]

    def test_agreement_is_logged_and_routing_unchanged(self):
        res = self._run(["open safari"])
        self.assertIsNotNone(res[0])  # primary (Ollama) decided, as without compare mode
        line = [m for m in self.logs if "Tier 0.5a compare:" in m][0]
        self.assertIn("AGREE", line)
        self.assertEqual(fv.embed_compare_stats()["n"], 1)
        self.assertEqual(fv.embed_compare_stats()["agree"], 1)

    def test_disagreement_is_flagged(self):
        self._run(["open safari"], onnx_query_sim=0.0)
        self.assertTrue(any("DIFFER" in m for m in self.logs))
        self.assertEqual(fv.embed_compare_stats()["agree"], 0)

    def test_shadow_failure_never_affects_the_result(self):
        res = self._run(["open safari"], onnx_fail=True)
        self.assertIsNotNone(res[0])
        self.assertEqual(fv.embed_compare_stats()["n"], 0)

    def test_summary_logged_every_n_utterances(self):
        self._run(["open safari"] * fv._COMPARE_SUMMARY_EVERY)
        self.assertEqual(sum("compare summary" in m for m in self.logs), 1)

    def test_default_backend_never_touches_onnx(self):
        primary = _fake_matrix(1)
        with mock.patch.object(fv, "TIER05_EMBED_BACKEND", "ollama"), \
             mock.patch.object(fv, "OLLAMA_EMBED_MODEL", "embeddinggemma"), \
             mock.patch.object(fv, "_ollama_embed", side_effect=lambda b, timeout=30.0: primary if len(b) == N else primary[[0]]), \
             mock.patch.object(fv, "_onnx_embed", side_effect=AssertionError("ONNX must not run")):
            self.assertIsNotNone(fv.tier05_embed_match("open safari"))
        self.assertEqual(fv.embed_compare_stats()["n"], 0)


@unittest.skipUnless(onnx_embed.available(), "ONNX model not downloaded (scripts/fetch_onnx_embedder.py)")
class TestRealOnnxModel(unittest.TestCase):
    def test_embeddings_are_normalised_and_semantically_sensible(self):
        emb = onnx_embed.get_embedder()
        self.assertIsNotNone(emb)
        v = emb.embed([fv._EMBED_PREFIX + t for t in ("open safari", "launch safari please", "the cat sat on the mat")])
        self.assertEqual(v.shape, (3, 768))
        np.testing.assert_allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)
        self.assertGreater(float(v[0] @ v[1]), float(v[0] @ v[2]) + 0.15)

    def test_batch_and_single_agree(self):
        emb = onnx_embed.get_embedder()
        texts = [fv._EMBED_PREFIX + t for t in ("mute the volume", "snap this window to the left side")]
        batch = emb.embed(texts)
        single = np.vstack([emb.embed([t]) for t in texts])
        np.testing.assert_allclose(batch, single, atol=2e-3)


if __name__ == "__main__":
    unittest.main()
