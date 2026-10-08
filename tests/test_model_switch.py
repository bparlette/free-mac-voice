"""Tier 1 model handling: pre-fill only for thinking builds; the command gate stays loaded (keep_alive) and is warmed at startup."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402


class _Resp:
    def __init__(self, payload): self._b = json.dumps(payload).encode()
    def read(self, *a): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _capture(reply):
    sent = []
    def fake(req, timeout=None):
        sent.append(json.loads(req.data.decode()))
        return _Resp(reply)
    return sent, fake


class TestAnswerPrefill(unittest.TestCase):
    def test_which_models_get_the_prefill(self):
        for m in ("qwen3-vl:8b", "qwen3-vl:4b", "qwen3.5:2b", "deepseek-r1:8b"):
            self.assertTrue(fv._needs_answer_prefill(m), m)
        for m in ("qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct", "qwen2.5:1.5b", "llama3.2:3b"):
            self.assertFalse(fv._needs_answer_prefill(m), m)

    def _route(self, model, content):
        sent, fake = _capture({"message": {"content": content}})
        with mock.patch.object(fv, "OLLAMA_MODEL", model), mock.patch.object(fv, "OLLAMA_TIER1", True), \
             mock.patch.object(fv.urllib.request, "urlopen", side_effect=fake), mock.patch.object(fv, "log", lambda *a, **k: None):
            fv._ollama_ok = None; fv._ollama_last_failure = 0.0
            return fv.ollama_route("open safari"), sent[0]

    def test_thinking_build_is_prefilled_and_prefix_restored(self):
        out, body = self._route("qwen3-vl:8b", 'open_app", "params": {"app": "Safari"}, "confidence": 0.95}')
        self.assertEqual(body["messages"][-1], {"role": "assistant", "content": '{"action": "'})
        self.assertEqual(out[0], "open_app")

    def test_instruct_build_is_not_prefilled(self):
        out, body = self._route("qwen3-vl:8b-instruct", '{"action": "open_app", "params": {"app": "Safari"}, "confidence": 0.95}')
        self.assertEqual(body["messages"][-1]["role"], "user")
        self.assertEqual(out[0], "open_app")


class TestGateStaysLoaded(unittest.TestCase):
    def test_gate_request_asks_to_stay_resident(self):
        sent, fake = _capture({"answers": {"is_command": {"choice": "none", "probabilities": {"command": 0.1}}}})
        with mock.patch.object(fv, "VOICE_COMMAND_GATE", True), mock.patch.object(fv.urllib.request, "urlopen", side_effect=fake), \
             mock.patch.object(fv, "log", lambda *a, **k: None):
            fv.is_voice_command("the quarterback dropped back and fired downfield")
        self.assertEqual(sent[0]["keep_alive"], "60m")

    def test_prewarm_also_loads_the_gate_model(self):
        sent, fake = _capture({"done": True, "message": {"content": "ok"}})
        started = []
        class T:
            def __init__(self, target=None, **k): self.t = target
            def start(self): self.t(); started.append(1)
        with mock.patch.object(fv, "OLLAMA_TIER1", True), mock.patch.object(fv, "DRY_RUN", False), mock.patch.object(fv, "VOICE_COMMAND_GATE", True), \
             mock.patch.object(fv, "VOICE_COMMAND_GATE_MODEL", "tev1:0.8b"), mock.patch.object(fv.threading, "Thread", T), \
             mock.patch.object(fv.urllib.request, "urlopen", side_effect=fake), mock.patch.object(fv, "log", lambda *a, **k: None):
            fv.prewarm_ollama()
        models = [b.get("model") for b in sent]
        self.assertIn("tev1:0.8b", models)
        self.assertEqual(models[0], fv.OLLAMA_MODEL)


if __name__ == "__main__":
    unittest.main()
