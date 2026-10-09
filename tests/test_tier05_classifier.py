"""Tier 0.5a classifier: trained at startup from the example phrases + tier05_negatives.txt, replaces nearest-example matching."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402
import tier05_classifier as clf  # noqa: E402

D = 16


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


class TestClassifierModule(unittest.TestCase):
    def test_learns_separable_classes_and_predicts_probabilities(self):
        rng = np.random.default_rng(0)
        centers = np.eye(D, dtype=np.float32)[:3]
        X = np.vstack([unit(c + 0.1 * rng.standard_normal(D)) for c in centers for _ in range(10)])
        y = np.repeat([0, 1, 2], 10)
        W, b = clf.train(X, y, 3)
        P = clf.predict(W, b, X)
        self.assertEqual(P.shape, (30, 3))
        self.assertTrue(np.allclose(P.sum(1), 1, atol=1e-5))
        self.assertGreaterEqual((P.argmax(1) == y).mean(), 0.95)

    def test_negatives_file_ships_with_the_repo_and_is_plain_text(self):
        neg = clf.read_negatives(os.path.join(os.path.dirname(fv.__file__), "tier05_negatives.txt"))
        self.assertGreater(len(neg), 50)
        self.assertEqual(clf.read_negatives("/nonexistent/path.txt"), [])


class TestLiveIntegration(unittest.TestCase):
    """A fake embedder gives every example and phrase a known vector, so the classifier's decisions are predictable."""

    def setUp(self):
        self.vec = {}
        actions = list(fv._INTENT_EXAMPLES)[:2]
        self.a0, self.a1 = actions
        self.vec["EX0"], self.vec["EX1"], self.vec["NEG"] = unit(np.eye(D)[0]), unit(np.eye(D)[1]), unit(np.eye(D)[2])
        examples = {self.a0: ["EX0"], self.a1: ["EX1"]}
        for patcher in (mock.patch.object(fv, "_INTENT_EXAMPLES", examples), mock.patch.object(fv, "TIER05_CLASSIFIER", True),
                        mock.patch.object(fv, "TIER05_CLASSIFIER_MIN_CONF", 0.5), mock.patch.object(fv, "TIER05_CLASSIFIER_MIN_COS", 0.7),
                        mock.patch.object(fv, "_onnx_embed", side_effect=self._embed), mock.patch.object(fv, "_use_onnx", return_value=True),
                        mock.patch.object(fv, "log", lambda *a, **k: None),
                        mock.patch.object(fv, "_extract_intent_params", lambda a, t: {"app": "X"} if a in ("open_app",) else {})):
            patcher.start(); self.addCleanup(patcher.stop)
        negfile = mock.patch("tier05_classifier.read_negatives", return_value=["NEG"] * 6)
        negfile.start(); self.addCleanup(negfile.stop)
        saved = (fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try, fv._T05_CLF)
        self.addCleanup(lambda: (setattr(fv, "_PRECOMPUTED_MATRIX", saved[0]), setattr(fv, "_PRECOMPUTED_INTENTS", saved[1]),
                                 setattr(fv, "_EMBED_NEURAL", saved[2]), setattr(fv, "_embed_last_try", saved[3]), setattr(fv, "_T05_CLF", saved[4])))
        fv._PRECOMPUTED_MATRIX, fv._PRECOMPUTED_INTENTS, fv._EMBED_NEURAL, fv._embed_last_try, fv._T05_CLF = None, [], False, 0.0, None

    def _embed(self, texts):
        return np.vstack([self.vec.get(t, unit(np.ones(D))) for t in texts])

    def test_trains_after_the_neural_upgrade(self):
        fv._init_intent_embeddings()
        self.assertTrue(fv._EMBED_NEURAL)
        self.assertIsNotNone(fv._T05_CLF)
        self.assertEqual(fv._T05_CLF[2], sorted([self.a0, self.a1]) + ["none"])

    def test_confident_close_phrase_routes_to_its_action(self):
        self.vec["say it"] = unit(np.eye(D)[0] + 0.1 * np.eye(D)[5])
        fv._init_intent_embeddings()
        hit = fv.tier05_embed_match("say it")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], self.a0)

    def test_non_command_phrase_is_rejected(self):
        self.vec["chat"] = unit(np.eye(D)[2] + 0.05 * np.eye(D)[6])
        fv._init_intent_embeddings()
        self.assertIsNone(fv.tier05_embed_match("chat"))

    def test_far_from_every_example_is_rejected_by_the_cosine_floor(self):
        self.vec["odd"] = unit(np.eye(D)[0] + 3.0 * np.eye(D)[7])      # leans toward action 0, but cosine to its example is ~0.3
        fv._init_intent_embeddings()
        self.assertIsNone(fv.tier05_embed_match("odd"))

    def test_switch_off_falls_back_to_nearest_example(self):
        self.vec["say it"] = unit(np.eye(D)[0] + 0.1 * np.eye(D)[5])
        with mock.patch.object(fv, "TIER05_CLASSIFIER", False):
            fv._init_intent_embeddings()
            self.assertIsNone(fv._T05_CLF)
            hit = fv.tier05_embed_match("say it")
        self.assertEqual(hit[0], self.a0)

    def test_training_failure_keeps_nearest_example_matching(self):
        with mock.patch("tier05_classifier.train", side_effect=RuntimeError("boom")):
            fv._init_intent_embeddings()
        self.assertIsNone(fv._T05_CLF)
        self.assertTrue(fv._EMBED_NEURAL)


if __name__ == "__main__":
    unittest.main()
