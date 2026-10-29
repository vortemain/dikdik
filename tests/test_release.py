"""Release checks must reject private material and mismatched tags before publishing."""

from pathlib import Path
import unittest
from scripts.check_release import check_release, expected_dependencies, normalize_dependency, safe_names


class ReleaseTests(unittest.TestCase):
    def test_declared_desktop_extra_is_separate_from_required_dependencies(self):
        project = {"dependencies": ['tzdata>=2026.5; sys_platform == "win32"'],
                   "optional-dependencies": {"desktop": ["pywebview>=6,<7"]}}
        self.assertEqual(expected_dependencies(project), {
            'tzdata>=2026.5;sys_platform=="win32"',
            'pywebview<7,>=6;extra=="desktop"',
        })
        self.assertEqual(normalize_dependency('pywebview<7,>=6; extra == "desktop"'),
                         normalize_dependency("pywebview>=6,<7; extra == 'desktop'"))

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
