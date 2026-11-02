"""An exited shell must not leave its inherited output pipe hanging forever."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.local_exec import check_project_search, failure_ids, preexisting_failures, project_copy, run, unlink_dir


class LocalExecTests(unittest.TestCase):
    def orphan_command(self):
        # The child inherits stdout, but the original shell/process exits immediately.
        return [sys.executable, '-c',
                "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
                "print('parent exiting', flush=True)"]

    def test_timeout_kills_descendants_after_the_parent_exits(self):
        with tempfile.TemporaryDirectory() as temp, patch('dikdik.local_exec.shell_argv', return_value=(self.orphan_command(), 'python')):
            started = time.monotonic()
            result = run(Path(temp), 'orphan test', threading.Event(), timeout=.7)
        self.assertEqual(result['status'], 'timed_out')
        self.assertIn('parent exiting', result['output'])
        self.assertLess(time.monotonic() - started, 3, result)

    def test_stop_kills_descendants_after_the_parent_exits(self):
        cancel = threading.Event()
        timers = []

        def output(text):
            if 'parent exiting' in text:
                timer = threading.Timer(.2, cancel.set)
                timers.append(timer)
                timer.start()

        try:
            with tempfile.TemporaryDirectory() as temp, patch('dikdik.local_exec.shell_argv', return_value=(self.orphan_command(), 'python')):
                started = time.monotonic()
                result = run(Path(temp), 'orphan test', cancel, timeout=4, on_chunk=output)
            self.assertEqual(result['status'], 'cancelled')
            self.assertLess(time.monotonic() - started, 3, result)
        finally:
            cancel.set()
            for timer in timers:
                timer.join()

    def test_root_discovery_searches_are_rejected_with_project_guidance(self):
        for command in ('find / -name "WebsiteLinks.tsx" 2>/dev/null', 'find "/" -name x',
                        "find '/' -name x", 'cd src && find / -name x', 'find /c/ -name x',
                        'find C:/ -name x', 'find /mnt/c/ -name x', '/usr/bin/find / -name x'):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'list_files'):
                check_project_search(command)
        for command in ('find . -name "WebsiteLinks.tsx"', 'find src -name x', 'echo "find /"',
                        'find /workspace/src -name x', 'python -m pytest'):
            with self.subTest(command=command):
                check_project_search(command)


class PreexistingFailureTests(unittest.TestCase):
    def test_failure_names_from_common_runners(self):
        output = "\n".join([
            "FAIL: test_add (test_calc.T.test_add)",
            "FAILED tests/test_api.py::test_login - AssertionError",
            "   \x1b[31m×\x1b[39m renders the footer 12ms",
            "(fail) Footer > keeps links [0.41ms]",
            "test parser::rejects_empty ... FAILED",
            "src/app.tsx(10,4): error TS2322: Type 'string' is not assignable to type 'number'.",
        ])
        self.assertEqual(failure_ids(output), {
            "test_add (test_calc.T.test_add)", "tests/test_api.py::test_login", "renders the footer",
            "Footer > keeps links", "parser::rejects_empty",
            "src/app.tsx error TS2322: Type 'string' is not assignable to type 'number'."})
        # An edit that shifts lines does not make an old type error look new.
        self.assertEqual(failure_ids("src/app.tsx(10,4): error TS2322: X"), failure_ids("src/app.tsx(14,4): error TS2322: X"))

    def test_only_failures_that_existed_before_are_excused(self):
        run_ = lambda output, status="failed": {"label": "unittest", "status": status, "output": output}
        before = run_("FAIL: test_old (t.T.test_old)")
        self.assertEqual(preexisting_failures(run_("FAIL: test_old (t.T.test_old)"), before), ["test_old (t.T.test_old)"])
        self.assertIsNone(preexisting_failures(run_("FAIL: test_old (t.T.test_old)\nFAIL: test_new (t.T.test_new)"), before))
        self.assertIsNone(preexisting_failures(run_("FAIL: test_old (t.T.test_old)"), run_("", "passed")))  # it passed before
        self.assertIsNone(preexisting_failures(run_("Segmentation fault"), before))  # unidentifiable: stays failed
        self.assertIsNone(preexisting_failures(run_("FAIL: test_old (t.T.test_old)"), None))  # baseline could not run
        both = {"status": "failed", "runs": [run_("FAIL: test_old (t.T.test_old)"), {"label": "lint", "status": "passed"}]}
        self.assertEqual(preexisting_failures(both, {"status": "failed", "runs": [before]}), ["test_old (t.T.test_old)"])

    def test_project_copy_links_installs_and_cleanup_spares_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root, copy = Path(temp) / "project", Path(temp) / "copy"
            (root / "node_modules" / "pkg").mkdir(parents=True)
            (root / "node_modules" / "pkg" / "index.js").write_text("x")
            (root / "dist").mkdir()
            (root / "dist" / "bundle.js").write_text("built")
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text("app")
            links = project_copy(root, copy)
            self.assertEqual((copy / "src" / "app.ts").read_text(), "app")
            self.assertEqual((copy / "node_modules" / "pkg" / "index.js").read_text(), "x")  # shared, not copied
            self.assertFalse((copy / "dist").exists())
            for link in links:
                unlink_dir(link)
            self.assertTrue((root / "node_modules" / "pkg" / "index.js").is_file())


class CommandScopeTests(unittest.TestCase):
    def test_commands_cannot_cd_outside_the_project(self):
        from dikdik.local_exec import check_command_scope
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            (root / "src").mkdir(parents=True)
            for command in ("cd src && ls", "bun run build", f"cd {(root / 'src').as_posix()} && ls",
                            "cd /workspace 2>/dev/null || true; ls"):  # a missing folder: cd fails, command stays here
                check_command_scope(command, root)
            other = Path(temp) / "other-site"
            other.mkdir()
            windows = other.resolve().as_posix()
            git_bash = "/" + windows[0].lower() + windows[2:] if windows[1] == ":" else windows
            for command in (f"cd {git_bash} && bun run build", f"cd {windows} && ls",
                            f"cd {Path(temp).as_posix()} && ls", "echo hi; cd ~ && ls"):
                with self.subTest(command=command), self.assertRaisesRegex(ValueError, "outside it"):
                    check_command_scope(command, root)


class NoisyCheckTests(unittest.TestCase):
    def test_a_check_with_huge_output_is_judged_by_its_exit_code(self):
        script = "import sys; [print('x' * 100) for _ in range(6000)]; print('FINAL'); sys.exit(%d)"
        for code, status in ((0, "passed"), (3, "failed")):
            with self.subTest(code=code):
                result = run(Path("."), f'"{Path(sys.executable).as_posix()}" -c "{script % code}"', threading.Event(), keep_running=True)
                self.assertEqual(result["status"], status)
                self.assertIn("FINAL", result["output"])  # the end, where runners summarise failures
                self.assertIn("characters omitted", result["output"])


class LintBaselineTests(unittest.TestCase):
    def test_preexisting_lint_errors_match_even_after_lines_move(self):
        # A CRLF checkout with prettier's endOfLine=lf fails lint thousands of times before any change.
        before = {"label": "bun run lint", "status": "failed", "output":
                  "C:/proj/src/a.tsx\n  12:5  error  Delete `CR`  prettier/prettier\n  14:1  warning  Unused  no-unused-vars\n"}
        same = {"label": "bun run lint", "status": "failed", "output":
                "C:/proj/src/a.tsx\n  15:5  error  Delete `CR`  prettier/prettier\n"}
        new = {"label": "bun run lint", "status": "failed", "output":
               "C:/proj/src/a.tsx\n  15:5  error  Delete `CR`  prettier/prettier\n  30:2  error  Missing key  react/jsx-key\n"}
        self.assertEqual(preexisting_failures(same, before), ["C:/proj/src/a.tsx prettier/prettier Delete `CR`"])
        self.assertIsNone(preexisting_failures(new, before))
