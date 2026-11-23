"""Path containment and Markdown/LaTeX parsing that stays fast on crafted text."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from dikdik import documents, omml
from dikdik.paths import contained


class ContainmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "work"
        (self.root / "data").mkdir(parents=True)
        (Path(self.temp.name) / "work-other").mkdir()

    def test_paths_inside_are_returned_resolved(self):
        self.assertEqual(contained(self.root, "data/survey.csv"), Path(os.path.realpath(self.root / "data" / "survey.csv")))
        self.assertEqual(contained(self.root, "."), Path(os.path.realpath(self.root)))

    def test_paths_that_leave_the_folder_are_refused(self):
        for escape in ("../secret.txt", "data/../../secret.txt", "../work-other/x.txt", str(Path(self.temp.name) / "x.txt")):
            with self.subTest(escape=escape), self.assertRaises(ValueError):
                contained(self.root, escape)

    def test_document_tools_still_refuse_traversal(self):
        for escape in ("../x.docx", "attachments/../../x.docx"):
            with self.subTest(escape=escape), self.assertRaises(ValueError):
                documents._safe_target(self.root, escape)


class MarkdownHelperTests(unittest.TestCase):
    def test_links_headings_images_and_separators_parse_as_before(self):
        self.assertEqual(documents._link("[WHO](https://who.int)"), ("WHO", "https://who.int"))
        self.assertIsNone(documents._link("[a b](has space)"))
        heading = documents._heading("## Results ##")
        self.assertEqual((heading.group(1), heading.group(2)), ("##", "Results"))
        self.assertIsNone(documents._heading("#NoSpace"))
        image = documents._image('![Figure 1: Scores](chart.png "Exam scores")')
        self.assertEqual((image.group(1), image.group(2)), ("Figure 1: Scores", "chart.png"))
        self.assertIsNone(documents._image("![alt](two words.png)"))
        self.assertTrue(documents._separator("| --- | :--: | ---: |"))
        self.assertTrue(documents._separator("---|---"))
        self.assertFalse(documents._separator("| a | b |"))
        self.assertTrue(documents._separator_start("  | :--- | x"))
        self.assertFalse(documents._separator_start("| a | b |"))
        item = documents._list_item("    12. Twelfth point")
        self.assertEqual((item.group(1), item.group(2), item.group(3)), ("    ", "12.", "Twelfth point"))
        self.assertEqual(documents._list_item("- ").group(3), "")
        self.assertIsNone(documents._list_item("-no space"))
        self.assertIsNone(documents._list_item("plain text"))
        self.assertEqual(documents._list_item("b) second").group(2), "b)")
        self.assertEqual(documents._list_item("\t* star").group(1), "\t")
        self.assertIsNone(documents._list_item("12 apples"))
        self.assertIsNone(documents._list_item("B. capital"))


class SlowInputTests(unittest.TestCase):
    """Inputs that made the old regular expressions take quadratic time now finish quickly."""

    def fast(self, action, limit=2.0):
        started = time.perf_counter()
        action()
        self.assertLess(time.perf_counter() - started, limit)

    def test_crafted_markdown_converts_quickly(self):
        spaces = " " * 20000
        markdown = "\n".join([
            "#" + spaces + "a" + spaces + "#",            # heading
            "|" + spaces + "x", "|" + spaces + "-",       # table and separator
            "*" + spaces,                                  # list item
            "```" + spaces + "x",                          # fence
            "![" * 5000,                                   # image
            "\\(" * 8000,                                  # inline maths opener without a closer
            "[" + "[\\" * 8000,                            # link
            "[" * 20000,                                   # many link openers
            "*\t!" + "\t\xa0" * 20000,                     # list item (CodeQL's example)
            "|" + "\t" * 20000,                            # separator start
            "0" * 40000 + "x",                             # list marker digits (CodeQL's example)
        ])
        with tempfile.TemporaryDirectory() as folder:
            self.fast(lambda: documents.build_docx(markdown, root=Path(folder)), limit=5.0)

    def test_crafted_latex_splits_quickly(self):
        self.fast(lambda: omml._lines("\\tag{" * 20000))
        self.fast(lambda: omml._lines("\\\\[" * 20000))
        self.fast(lambda: omml._matrices("\\begin{cases}" * 3000 + "x"))

    def test_matrices_still_convert(self):
        out = omml._matrices(r"\begin{pmatrix} a & b \\ c & d \end{pmatrix} = x")
        self.assertNotIn(r"\begin", out)
        self.assertIn("= x", out)


if __name__ == "__main__":
    unittest.main()
