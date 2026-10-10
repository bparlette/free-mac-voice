"""Daily tech scout: ranking, size warnings, de-duplication, tolerant parsing of the small model's reply, and the report. No network."""
import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("tech_scout", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "tech_scout.py"))
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)
TODAY = dt.date(2026, 10, 10)


def cand(i, title, desc="", stars=0, source="GitHub", **kw):
    c = {"id": i, "source": source, "title": title, "url": "https://example.com/" + i, "desc": desc, "stars": stars, "tags": []}
    c.update(kw)
    return c


class TestScoring(unittest.TestCase):
    def test_parameter_sizes(self):
        self.assertEqual(ts.params_b("Qwen3-VL-4B-Instruct"), 4.0)
        self.assertEqual(ts.params_b("a 70B model and a 7B one"), 70.0)
        self.assertEqual(ts.params_b("Qwen3.6-35B-A3B"), 35.0)
        self.assertIsNone(ts.params_b("whisper-large-v3"))
        self.assertIsNone(ts.params_b("ab12b3"))

    def test_relevant_projects_outrank_unrelated_ones_and_oversize_models_are_flagged(self):
        speech = cand("a", "org/fast-asr", "streaming speech recognition on Apple silicon with MLX", 500)
        generic = cand("b", "org/todo-app", "a nice todo list app", 5000)
        huge = cand("c", "org/giant-tts-70B", "text-to-speech model with 70B parameters", 500)
        for c in (speech, generic, huge):
            c["score"] = ts.score(c)
        self.assertGreater(speech["score"], generic["score"])
        self.assertTrue(huge.get("too_big"))
        self.assertLess(huge["score"], speech["score"])


class TestSelection(unittest.TestCase):
    def test_duplicates_and_recently_reported_ideas_are_dropped_and_skips_removed(self):
        a, b, c = cand("a", "A", score=5, verdict="benchmark"), cand("b", "B", score=9, verdict="skip"), cand("c", "C", score=4, verdict="watch")
        seen = {"c": (TODAY - dt.timedelta(days=3)).isoformat()}
        top = ts.pick_top([a, dict(a), b, c], seen, TODAY)
        self.assertEqual([x["id"] for x in top], ["a"])
        old = {"c": (TODAY - dt.timedelta(days=30)).isoformat()}
        self.assertEqual([x["id"] for x in ts.pick_top([a, c], old, TODAY)], ["a", "c"])    # verdict order: benchmark before watch


class TestModelReply(unittest.TestCase):
    def test_tolerates_fences_preamble_and_junk(self):
        reply = 'Sure!\n```json\n[{"n": 1, "what": "x", "why": "y", "verdict": "benchmark", "bench": "asr"}, {"n": "2"}, "stray"]\n```\nDone.'
        self.assertEqual([d["n"] for d in ts.parse_llm_json(reply)], [1])
        self.assertEqual(ts.parse_llm_json("no json here"), [])
        self.assertEqual(ts.parse_llm_json("[not json]"), [])
        self.assertEqual(ts.parse_llm_json(""), [])

    def test_explain_fills_fields_ignores_out_of_range_numbers_and_survives_an_offline_model(self):
        cs = [cand("a", "A"), cand("b", "B")]
        reply = {"message": {"content": '[{"n": 2, "what": "w", "why": "z", "verdict": "Benchmark", "bench": "tts"}, {"n": 9, "what": "ghost"}]'}}

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps(reply).encode()
        with mock.patch.object(ts.urllib.request, "urlopen", return_value=R()):
            self.assertTrue(ts.explain(cs, "http://x", "m"))
        self.assertEqual((cs[1]["verdict"], cs[1]["bench"], cs[1]["what"]), ("benchmark", "tts", "w"))
        self.assertNotIn("what", cs[0])
        with mock.patch.object(ts.urllib.request, "urlopen", side_effect=OSError("down")), mock.patch.object(sys, "stderr"):
            self.assertFalse(ts.explain([cand("a", "A")], "http://x", "m"))


class TestRun(unittest.TestCase):
    def test_full_run_writes_report_and_remembers_what_it_showed(self):
        items = [cand(f"gh:{i}", f"org/speech-tool-{i}", "speech recognition for macOS", 100 + i) for i in range(14)]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ts, "fetch_github", return_value=items), mock.patch.object(ts, "fetch_hf", return_value=[]), \
             mock.patch.object(ts, "fetch_ollama", return_value=[]), mock.patch.object(ts, "fetch_hn", return_value=[]):
            path, top = ts.run(no_llm=True, today=TODAY, out_dir=d)
            self.assertEqual(len(top), 10)
            with open(path) as f:
                text = f.read()
            self.assertIn("### 10.", text)
            self.assertIn("Source says:", text)
            self.assertIn("local model was not available", text)
            self.assertTrue(os.path.exists(os.path.join(d, "latest.md")))
            path2, top2 = ts.run(no_llm=True, today=TODAY + dt.timedelta(days=1), out_dir=d)      # the next day: only the 4 not yet shown
            self.assertEqual(len(top2), 4)
            self.assertFalse({c["id"] for c in top} & {c["id"] for c in top2})


if __name__ == "__main__":
    unittest.main()
