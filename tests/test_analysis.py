"""Tests for dikdik/analysis.py — statistical analysis of CSV/TSV/JSON/XLSX files."""
import json
import tempfile
import unittest
from pathlib import Path

from dikdik import analysis


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_csv_stats(self):
        (self.root / "data.csv").write_text("a,b,c\n1,2,3\n4,5,6\n7,8,9\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "data.csv"})
        self.assertIn("rows", result)
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["columns"], ["a", "b", "c"])
        prof = result["profiles"]["a"]
        self.assertAlmostEqual(prof["mean"], 4.0)
        self.assertAlmostEqual(prof["median"], 4.0)
        self.assertAlmostEqual(prof["std"], 2.44949, places=3)
        self.assertEqual(prof["count"], 3)
        self.assertAlmostEqual(prof["min"], 1.0)
        self.assertAlmostEqual(prof["max"], 7.0)
        self.assertIn("p25", prof)
        self.assertIn("p75", prof)

    def test_tsv_stats(self):
        (self.root / "data.tsv").write_text("x\ty\n1\t10\n2\t20\n3\t30\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "data.tsv"})
        self.assertEqual(result["rows"], 3)
        self.assertAlmostEqual(result["profiles"]["y"]["mean"], 20.0)
        self.assertEqual(result["profiles"]["x"]["count"], 3)

    def test_json_stats(self):
        (self.root / "data.json").write_text(json.dumps([
            {"name": "a", "value": 10}, {"name": "b", "value": 20}, {"name": "c", "value": 30}
        ]), encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "data.json"})
        self.assertTrue(result.get("rows", 0) >= 1)

    def test_group_by(self):
        (self.root / "group.csv").write_text("category,amount\nA,10\nA,20\nB,5\nB,15\nB,25\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "group.csv", "group_by": "category"})
        self.assertIn("group_by", result)
        groups = result["group_by"].get("groups", [])
        self.assertIsInstance(groups, list)
        # Find group A and B
        g_map = {g["value"]: g for g in groups}
        self.assertIn("A", g_map)
        self.assertIn("B", g_map)
        self.assertAlmostEqual(g_map["A"]["amount"], 15.0)
        self.assertAlmostEqual(g_map["B"]["amount"], 15.0)

    def test_correlations(self):
        # Perfect positive correlation: y = 2x + 1
        (self.root / "corr.csv").write_text("x,y\n1,3\n2,5\n3,7\n4,9\n5,11\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "corr.csv"})
        corr = result.get("correlations", {})
        self.assertIsInstance(corr, dict)
        # Check the correlation between x and y is ~1.0
        xy_corr = corr.get("x", {}).get("y", 0)
        self.assertAlmostEqual(xy_corr, 1.0, places=2)

    def test_missing_file_raises(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            analysis.analyze(self.root, {"path": "missing.csv"})

    def test_path_traversal_rejected(self):
        (self.root / "safe.csv").write_text("a\n1\n", encoding="utf-8")
        with self.assertRaises((ValueError, FileNotFoundError)):
            analysis.analyze(self.root, {"path": "../../../etc/passwd"})

    def test_quartiles(self):
        (self.root / "q.csv").write_text("v\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "q.csv"})
        prof = result["profiles"]["v"]
        self.assertLessEqual(prof["p25"], prof["p75"])

    def test_non_numeric_column(self):
        (self.root / "mixed.csv").write_text("name,score\nAlice,90\nBob,85\nCharlie,95\n", encoding="utf-8")
        result = analysis.analyze(self.root, {"path": "mixed.csv"})
        prof = result["profiles"]
        # Non-numeric columns should have numeric_share < 1.0 or no mean
        self.assertIn("name", prof)
        self.assertIn("score", prof)
        self.assertIn("mean", prof["score"])


if __name__ == "__main__":
    unittest.main()