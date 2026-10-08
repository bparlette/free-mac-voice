"""benchmarks/README.md is the benchmark ledger; its headline tables are generated from the saved results. This fails if someone adds or
changes results without running `python benchmarks/update_ledger.py` (so the ledger cannot go stale)."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
import update_ledger  # noqa: E402


class TestLedger(unittest.TestCase):
    def test_readme_is_up_to_date_with_results(self):
        text = open(os.path.join(ROOT, "benchmarks", "README.md")).read()
        self.assertEqual(update_ledger.render(text), text,
                         "benchmarks/README.md is out of date: run `python benchmarks/update_ledger.py` and commit")

    def test_tables_have_rows(self):
        self.assertGreaterEqual(update_ledger.models_table().count("\n"), 5)
        self.assertIn("wrongly acted on", update_ledger.pipeline_table())

    def test_missing_marker_is_an_error(self):
        with self.assertRaises(SystemExit):
            update_ledger.render("no markers here")


if __name__ == "__main__":
    unittest.main()
