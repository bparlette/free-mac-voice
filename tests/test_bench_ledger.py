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

    def test_main_readme_ledger_section_is_up_to_date(self):
        ledger = update_ledger.render(open(os.path.join(ROOT, "benchmarks", "README.md")).read())
        root = open(os.path.join(ROOT, "README.md")).read()
        self.assertEqual(update_ledger.render_root(root, ledger), root,
                         "the Benchmark Ledger section of README.md is out of date: run `python benchmarks/update_ledger.py` and commit")

    def test_mirrored_links_resolve_from_the_main_readme(self):
        root = open(os.path.join(ROOT, "README.md")).read()
        start = root.index("<!-- AUTO:ledger-live:START -->"); end = root.index("<!-- AUTO:ledger-backlog:END -->")
        self.assertNotIn("](#recommendations)", root[start:end])
        self.assertIn("benchmarks/README.md#recommendations", root[start:end])

    def test_tables_have_rows(self):
        self.assertGreaterEqual(update_ledger.models_table().count("\n"), 5)
        self.assertIn("wrongly acted on", update_ledger.pipeline_table())

    def test_missing_marker_is_an_error(self):
        with self.assertRaises(SystemExit):
            update_ledger.render("no markers here")


if __name__ == "__main__":
    unittest.main()
