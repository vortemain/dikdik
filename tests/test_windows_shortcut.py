import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from dikdik.windows_shortcut import ensure_shortcut


@unittest.skipUnless(sys.platform == "win32", "Windows shell registration")
class WindowsShortcutTests(unittest.TestCase):
    def test_native_shortcut_contains_matching_identity_and_keeps_existing_shortcut(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": directory}):
            ensure_shortcut()
            shortcut = Path(directory) / "Microsoft/Windows/Start Menu/Programs/Dikdik/Dikdik.lnk"
            content = shortcut.read_bytes()
            self.assertEqual(content[:4], b"L\0\0\0")  # Shell Link header.
            self.assertIn("Dikdik.Desktop".encode("utf-16-le"), content)
            shortcut.write_bytes(b"existing installer shortcut")
            ensure_shortcut()
            self.assertEqual(shortcut.read_bytes(), b"existing installer shortcut")


if __name__ == "__main__":
    unittest.main()
