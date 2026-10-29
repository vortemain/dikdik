"""Release checks must reject private material and mismatched tags before publishing."""

from pathlib import Path
import unittest
from scripts.check_release import check_release, safe_names


class ReleaseTests(unittest.TestCase):
    def test_rejects_archive_traversal_private_state_and_native_binaries(self):
        for name in ("../LICENSE", "/etc/passwd", "C:/secret", "dikdik\\file.py",
                     "dikdik/.env", ".git/config", "upstream/source.py",
                     "node_modules/a.js", "dikdik/__pycache__/x.pyc",
                     "dikdik/dikdik_index.pyd", "dikdik/dikdik_index.abi3.so"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_names([name])

    def test_accepts_required_runtime_and_license_paths(self):
        safe_names(["dikdik/server.py", "dikdik/skills/planning/SKILL.md",
                    "dikdik/web/assets/LICENSE-highlight.js.txt",
                    "dikdik-0.1.0.dist-info/licenses/LICENSE"])

    def test_mismatched_tag_is_rejected_before_archive_inspection(self):
        with self.assertRaisesRegex(ValueError, "tag must match"):
            check_release(Path("nonexistent-dist"), "v999.0.0")
