import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dikdik.engine import candidate_diff, change_summary


class ChangeReviewTests(unittest.TestCase):
    def test_added_modified_empty_files_and_unicode_have_correct_line_ranges(self):
        original = {"app.py": "value = 1\n"}
        changed = {"app.py": "value = 2\n", "new feature.py": "# 😀\ntext = 'a\u2028b'\n", "package/__init__.py": ""}
        diff = candidate_diff(original, changed)
        files = change_summary(original, changed, diff)
        self.assertEqual([(item["path"], item["status"]) for item in files], [("app.py", "modified"), ("new feature.py", "added"), ("package/__init__.py", "added")])
        lines = diff.split("\n")
        for item in files:
            chunk = "\n".join(lines[item["start_line"]:item["end_line"]]) + "\n"
            self.assertTrue(chunk.startswith("diff --git "))
            self.assertEqual(chunk.count("diff --git "), 1)
        self.assertIn("+# 😀", diff)
        self.assertIn("+text = 'a\u2028b'", diff)
        self.assertEqual(files[-1]["end_line"], len(lines) - 1)

    def test_generic_diff_supports_removed_files_without_exposing_a_delete_tool(self):
        original = {"old.py": "pass\n"}
        diff = candidate_diff(original, {})
        self.assertIn("deleted file mode", diff)
        self.assertEqual(change_summary(original, {}, diff)[0]["status"], "deleted")

    def test_no_change_yields_empty_diff_and_index(self):
        self.assertEqual(candidate_diff({"app.py": "pass\n"}, {"app.py": "pass\n"}), "")
        self.assertEqual(change_summary({"app.py": "pass\n"}, {"app.py": "pass\n"}, ""), [])

    @unittest.skipUnless(shutil.which("git"), "Git is optional for independent diff round-trip checking")
    def test_patch_round_trip_with_git_in_controlled_qa_workspace(self):
        original = {"app.py": "value = 1\n"}
        changed = {"app.py": "value = 2", "new feature.py": "# 😀\n", "café.py": "# café\n", "package/__init__.py": ""}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(root)], check=True, capture_output=True, timeout=10)
            for path, content in original.items():
                (root / path).write_bytes(content.encode())
            patch = candidate_diff(original, changed).encode()
            for flags in [["--check"], []]:
                result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(root), "apply", *flags, "-"], input=patch, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual({path: (root / path).read_bytes().decode() for path in changed}, changed)
