"""Open in VS Code: only project files, with the user's own `code` command."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.server import open_in_editor


class OpenInEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    def test_opens_a_file_at_a_line_and_the_project(self):
        with patch("dikdik.server.shutil.which", return_value="C:/code.cmd"), patch("dikdik.server.subprocess.Popen") as popen:
            self.assertEqual(open_in_editor(self.root, "src/app.py", 3)["opened"], "src/app.py")
            args = popen.call_args[0][0]
            self.assertEqual(args[:3], ["C:/code.cmd", "--reuse-window", "--goto"])
            self.assertTrue(args[3].endswith("app.py:3"))
            open_in_editor(self.root, ".")
            self.assertEqual(popen.call_args[0][0], ["C:/code.cmd", str(self.root.resolve())])

    def test_refuses_paths_outside_the_project_and_missing_editor(self):
        with patch("dikdik.server.shutil.which", return_value="C:/code.cmd"), patch("dikdik.server.subprocess.Popen") as popen:
            for bad in ("../outside.py", "-x"):
                with self.assertRaises(ValueError):
                    open_in_editor(self.root, bad)
            with self.assertRaises(KeyError):
                open_in_editor(self.root, "src/missing.py")
            popen.assert_not_called()
        with patch("dikdik.server.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "not on PATH"):
            open_in_editor(self.root, "src/app.py")


if __name__ == "__main__":
    unittest.main()
