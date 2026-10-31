import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path

from dikdik.repository import approved_root, apply_edits, inventory, metadata, syntax_check, workspace_metadata, workspace_text, MAX_PREVIEW_BYTES


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_inventory_filters_ignored_hidden_secrets_and_binary(self):
        for name in ["app.py", ".env", "secrets.json", "credentials.txt", "private.key", "node_modules/lib.js", "cache/a.py", "nested/drop.py", "nested/keep.py", "image.png"]:
            self.write(name, "x = 1")
        self.write(".gitignore", "cache/\n!cache/a.py\n")
        self.write("nested/.gitignore", "drop.py\n")
        self.assertEqual(list(inventory(self.root)), ["app.py", "nested/keep.py"])

    def test_oversized_files_do_not_block_or_enter_model_context_as_partial_content(self):
        self.write("huge.py", "x" * 256001)
        svg = '<svg xmlns="http://www.w3.org/2000/svg">' + " " * 300000 + '</svg>'
        self.write("client/public/Cover.svg", svg)
        self.write("app.py", "x = 1")
        self.assertEqual(inventory(self.root), {"app.py": "x = 1"})
        listing = workspace_metadata(self.root)
        self.assertEqual([entry["path"] for entry in listing["large_files"]], ["client/public/Cover.svg", "huge.py"])
        preview = workspace_text(self.root, "client/public/Cover.svg")
        self.assertEqual(preview["text"], svg)
        self.assertEqual((preview["indexed"], preview["truncated"]), (False, False))
        self.assertEqual((self.root / "client/public/Cover.svg").read_text(), svg)

    def test_oversized_native_metadata_falls_back_to_the_same_bounded_inventory(self):
        self.write("large.svg", "x" * 256001)
        self.write("app.py", "pass")
        native = Mock()
        native.scan.return_value = [{"path": "large.svg", "bytes": 256001, "text": None}]
        with patch("dikdik.repository._rust_index", native):
            self.assertEqual(inventory(self.root), {"app.py": "pass"})

    def test_large_previews_are_bounded_and_preserve_utf8_character_boundaries(self):
        self.write("large.svg", "x" * (MAX_PREVIEW_BYTES - 1) + "é" + "tail")
        preview = workspace_text(self.root, "large.svg")
        self.assertTrue(preview["truncated"])
        self.assertEqual(preview["text"], "x" * (MAX_PREVIEW_BYTES - 1))
        self.assertGreater(preview["bytes"], MAX_PREVIEW_BYTES)

    def test_large_preview_keeps_ignore_secret_and_path_policies(self):
        self.write(".gitignore", "ignored/\n")
        for path in ("ignored/large.svg", "node_modules/large.svg", "private-token.txt", ".hidden.svg"):
            self.write(path, "x" * 256001)
            with self.subTest(path=path), self.assertRaises(KeyError):
                workspace_text(self.root, path)
        self.assertEqual(workspace_metadata(self.root)["large_files"], [])
        for path in ("../outside.svg", "/absolute.svg", "ignored\\large.svg"):
            with self.subTest(path=path), self.assertRaises(KeyError):
                workspace_text(self.root, path)

    def test_large_nontext_previews_are_rejected_without_breaking_project_opening(self):
        self.write("binary.svg", "x" * 256001 + "\0")
        self.assertEqual(inventory(self.root), {})
        with self.assertRaisesRegex(ValueError, "binary data"):
            workspace_text(self.root, "binary.svg")

    def test_initial_cache_limit_does_not_make_other_files_undiscoverable(self):
        from dikdik.workspace_files import WorkspaceFiles
        for index in range(1002):
            self.write(f'file-{index}.txt', 'content')
        cached = inventory(self.root)
        self.assertLessEqual(len(cached), 1000)
        data = workspace_metadata(self.root)
        self.assertEqual(len(data['files']) + len(data['large_files']), 1002)
        files = WorkspaceFiles(self.root)
        outside_cache = next(path for path in files.paths() if path not in cached)
        self.assertEqual(files.read(outside_cache, cached)['lines'], ['content'])

    def test_languages_and_extensionless_build_files_are_visible_and_creatable(self):
        from dikdik.repository import can_create_file
        names = ["query.sql", "main.go", "App.java", "main.kt", "app.php", "run.ps1", "app.dart", "script.lua",
                 "main.exs", "main.swift", "main.cpp", "app.vue", "build.gradle", "main.tf", "app.nix", "Dockerfile", "Makefile"]
        for name in names:
            self.assertTrue(can_create_file(self.root, name), name)
            self.write(name, "text\n")
        self.assertEqual(set(inventory(self.root)), set(names))

    def test_dikdikignore_skips_large_generated_assets_without_changing_git_rules(self):
        self.write(".dikdikignore", "generated/\n!generated/big.js\n")
        self.write("generated/big.js", "x" * 256001)
        self.write("main.sql", "SELECT 1;")
        self.assertEqual(list(inventory(self.root)), ["main.sql"])

    def test_symlink_is_not_followed(self):
        self.write("app.py", "x = 1")
        try:
            (self.root / "linked.py").symlink_to(self.root / "app.py")
        except OSError:
            self.skipTest("OS does not permit unprivileged symlink creation")
        self.assertEqual(list(inventory(self.root)), ["app.py"])
        with self.assertRaises(ValueError):
            approved_root(str(self.root / "linked.py"))

    def test_root_requires_real_project_directory(self):
        with self.assertRaises(ValueError):
            approved_root(self.root.anchor)
        self.write("file.py", "pass")
        with self.assertRaises(ValueError):
            approved_root(str(self.root / "file.py"))

    def test_metadata_hash_tracks_content(self):
        first = metadata({"app.py": "x=1", "test_app.py": "pass", "pyproject.toml": ""})
        self.assertEqual(first["python_tests"], ["test_app.py"])
        self.assertEqual(first["manifests"], ["pyproject.toml"])
        self.assertNotEqual(first["files"][0]["sha256"], metadata({"app.py": "x=2"})["files"][0]["sha256"])

    def test_exact_replacement_preserves_original(self):
        original = {"app.py": "return a - b\n"}
        new = apply_edits(original, [{"path": "app.py", "before": "a - b", "after": "a + b"}])
        self.assertEqual(new["app.py"], "return a + b\n")
        self.assertEqual(original["app.py"], "return a - b\n")

    def test_edits_reject_traversal_missing_ambiguous_and_noop(self):
        for edit in [
            {"path": "../app.py", "before": "x", "after": "y"},
            {"path": "absent.py", "before": "x", "after": "y"},
            {"path": "app.py", "before": "x", "after": "y"},
            {"path": "app.py", "before": "xx", "after": "xx"},
            {"path": "app.py", "before": "", "after": "y"},
            {"path": "app.py", "before": 1, "after": "y"},
        ]:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                apply_edits({"app.py": "xx"}, [edit])

    def test_test_named_files_are_protected(self):
        for name in ["test_app.py", "tests/helper.py", "src/app.spec.js", "src/app.test.ts", "conftest.py"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "protected"):
                apply_edits({name: "assert False"}, [{"path": name, "before": "assert False", "after": "pass"}])

    def test_syntax_is_safe_parsing_not_execution(self):
        result = syntax_check({"app.py": "raise RuntimeError('must not execute')\n"})
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["files_checked"], 1)
        self.assertEqual(syntax_check({"app.py": "def ("})["status"], "failed")
        # TS/JS files are now brace-balance checked (not executed).
        self.assertEqual(syntax_check({"app.ts": "const x = 1"})["status"], "passed")
        self.assertEqual(syntax_check({"app.ts": "function f() {"})["status"], "failed")

    def test_lf_edit_matches_crlf_file_and_keeps_its_line_endings(self):
        # read_file shows lines without "\r", so models always send LF text.
        files = {"Footer.tsx": "export function Footer() {\r\n  return (\r\n    <footer>old</footer>\r\n  );\r\n}\r\n"}
        edited = apply_edits(files, [{"path": "Footer.tsx", "before": "  return (\n    <footer>old</footer>\n",
                                      "after": "  return (\n    <footer>\n      new\n    </footer>\n"}])
        self.assertEqual(edited["Footer.tsx"], "export function Footer() {\r\n  return (\r\n    <footer>\r\n      new\r\n    </footer>\r\n  );\r\n}\r\n")
        # A file already mixing endings still matches, and LF files are untouched by the conversion.
        mixed = {"a.ts": "one\ntwo\r\nthree\n"}
        self.assertEqual(apply_edits(mixed, [{"path": "a.ts", "before": "one\ntwo\nthree", "after": "1\n2"}])["a.ts"], "1\n2\n")
        self.assertEqual(apply_edits({"b.ts": "x\ny\n"}, [{"path": "b.ts", "before": "x\ny", "after": "z\r\nw"}])["b.ts"], "z\nw\n")
        with self.assertRaisesRegex(ValueError, "2 times"):
            apply_edits({"c.ts": "a\r\nb\r\na\r\nb\r\n"}, [{"path": "c.ts", "before": "a\nb", "after": "c"}])

    def test_syntax_check_judges_only_what_the_change_touched(self):
        prose = "export const Page = () => <p>Kenya's groundwater, the company's rigs</p>;\n"
        self.assertEqual(syntax_check({"page.tsx": prose})["status"], "passed")  # apostrophes in JSX text
        baseline = {"old.tsx": "const s = /'/;\n", "app.py": "x = 1\n", "lib.ts": "export const a = 1;\n"}
        changed = {**baseline, "lib.ts": "export const a = 2;\n"}
        result = syntax_check(changed, baseline)
        self.assertEqual((result["status"], result["files_checked"]), ("passed", 1))  # old.tsx's quirk is not blamed
        broken = syntax_check({**baseline, "lib.ts": "export function f() {\n"}, baseline)
        self.assertEqual(broken["status"], "failed")
        self.assertIn("lib.ts", broken["errors"][0])
        self.assertEqual(syntax_check(baseline, baseline)["status"], "not_run")

    def test_non_utf8_is_not_indexed(self):
        (self.root / "binary.py").write_bytes(b"\xff\xfe")
        self.assertEqual(inventory(self.root), {})

    def test_bom_python_source_is_valid_and_preserved(self):
        raw = b"\xef\xbb\xbfvalue = 1\r\n"
        (self.root / "app.py").write_bytes(raw)
        files = inventory(self.root)
        self.assertEqual(syntax_check(files)["status"], "passed")
        self.assertEqual(files["app.py"].encode("utf-8"), raw)

    @unittest.skipUnless(__import__("dikdik.repository").repository.INDEXER == "rust",
                        "Rust indexer not built; run python scripts/build_native.py")
    def test_rust_inventory_matches_python_inventory_on_a_representative_tree(self):
        for name in ["app.py", "src/deep/mod.ts", "docs/notes.md", "café.py", "package/__init__.py", "data.json"]:
            self.write(name, "content for " + name + "\n")
        self.write(".gitignore", "cache/\n!cache/a.py\n")  # negation must NOT re-include
        self.write(".env", "SECRET=x\n")
        self.write("node_modules/lib.js", "x")
        self.write("cache/a.py", "x")  # excluded despite the negation
        with open(self.root / "binary.py", "wb") as handle:
            handle.write(b"\xff\xfe")  # not valid UTF-8 -> filtered by both
        rust = inventory(self.root)
        # Force the Python path for comparison by temporarily hiding the extension.
        import dikdik.repository as repo
        saved = repo._rust_index
        repo._rust_index = None
        try:
            python = inventory(self.root)
        finally:
            repo._rust_index = saved
        self.assertEqual(rust, python, "Rust and Python inventories diverged")
