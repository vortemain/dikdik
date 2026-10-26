import tempfile
import os
import unittest
from pathlib import Path

from dikdik.repository import apply_edits, can_create_file, create_file


class FileCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_nested_candidate_file_and_empty_package_leave_original_unchanged(self):
        original = {"app.py": "value = 1\n"}
        candidate = create_file(original, "feature/service.py", "value = 2\n")
        candidate = create_file(candidate, "feature/__init__.py", "")
        self.assertEqual(original, {"app.py": "value = 1\n"})
        self.assertEqual(candidate["feature/__init__.py"], "")
        self.assertEqual(candidate["feature/service.py"], "value = 2\n")

    def test_reserved_secret_ignored_and_noncanonical_paths_are_rejected(self):
        for path in ["../out.py", "/out.py", "C:/out.py", "src\\out.py", "src//out.py", "src/./out.py", ".env", ".git/config.py", "node_modules/out.js", "build/out.py", "credentials.json", "keys/private.key", "CON.py", "CON .py", "LPT1 .py", "NUL .py", "IGNORE~1/new.py", "aux/out.py", "src/out.py ", "src/out.exe", "src/out\n.py"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                create_file({}, path, "text")

    def test_overwrite_case_alias_and_file_directory_collisions_rejected(self):
        for files, path in [({"app.py": "x"}, "app.py"), ({"app.py": "x"}, "APP.PY"), ({"app.py": "x"}, "app.py/child.py"), ({"app.py/child.py": "x"}, "app.py")]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                create_file(files, path, "text")

    def test_snapshot_file_and_byte_limits_apply_to_creation_and_edits(self):
        for files, path, content in [({}, "large.py", "x" * 256001), ({f"file_{i}.py": "" for i in range(1000)}, "extra.py", ""), ({f"file_{i}.py": "x" * 256000 for i in range(31)}, "extra.py", "x" * 64001), ({}, "bad.py", "a\x00b")]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                create_file(files, path, content)
        files = {f"file_{i}.py": "x" * 256000 for i in range(31)}
        files["small.py"] = "small"
        with self.assertRaises(ValueError):
            apply_edits(files, [{"path": "small.py", "before": "small", "after": "x" * 64001}])

    def test_workspace_policy_respects_inherited_ignores_and_existing_unindexed_files(self):
        (self.root / ".gitignore").write_text("generated/\n!generated/allowed.py\n")
        (self.root / "src").mkdir()
        (self.root / "src" / ".gitignore").write_text("skip.py\n")
        (self.root / "binary.py").write_bytes(b"\xff")
        self.assertTrue(can_create_file(self.root, "src/new.py"))
        for path in ["generated/new.py", "generated/allowed.py", "src/skip.py", "binary.py", "../out.py"]:
            with self.subTest(path=path):
                self.assertFalse(can_create_file(self.root, path))
        self.assertFalse((self.root / "src" / "new.py").exists())

    def test_workspace_policy_rejects_symlink_ancestor_and_existing_directory(self):
        (self.root / "directory.py").mkdir()
        self.assertFalse(can_create_file(self.root, "directory.py"))
        try:
            (self.root / "linked").symlink_to(self.root / "directory.py", target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit unprivileged symlinks")
        self.assertFalse(can_create_file(self.root, "linked/new.py"))

    def test_baseline_tests_stay_protected_but_new_tests_can_be_repaired(self):
        baseline = {"test_original.py": "assert True\n"}
        candidate = create_file(baseline, "test_new.py", "assert (True\n")
        repaired = apply_edits(candidate, [{"path": "test_new.py", "before": "(True", "after": "(True)"}], protected_paths=set(baseline))
        self.assertEqual(repaired["test_new.py"], "assert (True)\n")
        with self.assertRaisesRegex(ValueError, "protected"):
            apply_edits(repaired, [{"path": "test_original.py", "before": "True", "after": "False"}], protected_paths=set(baseline))

    def test_case_aliases_cannot_split_one_snapshot_directory(self):
        for files, path in [({"src/base.py": "pass"}, "SRC/new.py"), ({"feature/helper.py": "pass"}, "FEATURE/other.py")]:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "case alias"):
                create_file(files, path, "pass\n")
        (self.root / "src").mkdir()
        if os.name == "nt":
            self.assertFalse(can_create_file(self.root, "SRC/new.py"))
        self.assertTrue(can_create_file(self.root, "src/new.py"))

    @unittest.skipUnless(os.name == "nt", "NTFS short aliases are Windows-specific")
    def test_ntfs_short_alias_does_not_bypass_project_ignore_rules(self):
        import ctypes
        directory = self.root / "ignored content"
        directory.mkdir()
        (self.root / ".gitignore").write_text("ignored content/\n")
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(directory), buffer, len(buffer)):
            self.skipTest("Filesystem does not expose short paths")
        alias = Path(buffer.value).name
        if alias == directory.name:
            self.skipTest("8.3 aliases are disabled on this filesystem")
        self.assertFalse(can_create_file(self.root, alias + "/new.py"))
        self.assertFalse(can_create_file(self.root, "ignored content/new.py"))
