import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import gitops
from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import ApplyConflict, Engine, TERMINAL

FIX = {"objective": "Fix", "mode": "offline", "verification": "syntax", "edits": [{"path": "calc.py", "before": "a - b", "after": "a + b"}]}


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)

    def respond(self, messages, tools):
        return self.responses.pop(0)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)

    def finished(self, task):
        for _ in range(400):
            with self.engine.lock:
                busy = bool(self.engine.active)
            data = self.engine.store.get(task)
            if not busy and data["state"] in TERMINAL:
                return data
            time.sleep(0.01)
        self.fail("task did not finish")

    def test_apply_writes_the_candidate_once(self):
        task = self.engine.submit(FIX)
        self.assertEqual(self.finished(task)["result"]["base"]["calc.py"], __import__("hashlib").sha256(b"def add(a, b):\n    return a - b\n").hexdigest())
        self.assertEqual(self.engine.apply(task), {"applied": ["calc.py"], "unchanged": []})
        self.assertEqual((self.root / "calc.py").read_bytes(), b"def add(a, b):\n    return a + b\n")
        events = self.engine.store.get(task)["events"]
        self.assertEqual(events[-1]["kind"], "applied")
        self.assertEqual(self.engine.apply(task), {"applied": [], "unchanged": ["calc.py"]})

    def test_a_file_edited_since_is_a_conflict_and_nothing_is_written(self):
        task = self.engine.submit(FIX)
        self.finished(task)
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a * b  # mine\n")
        with self.assertRaises(ApplyConflict) as caught:
            self.engine.apply(task)
        self.assertEqual(caught.exception.paths, ["calc.py"])
        self.assertIn(b"# mine", (self.root / "calc.py").read_bytes())

    def test_apply_rules(self):
        task = self.engine.submit(FIX)
        self.finished(task)
        for bad in (["other.py"], [], "calc.py"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Choose files"):
                self.engine.apply(task, bad)
        with self.engine.lock:
            self.engine.active[task] = threading.Event()
        try:
            with self.assertRaisesRegex(ValueError, "Wait for the task"):
                self.engine.apply(task)
        finally:
            with self.engine.lock:
                self.engine.active.pop(task)
        failed = self.engine.submit({**FIX, "edits": [{"path": "calc.py", "before": "missing", "after": "x"}]})
        self.finished(failed)
        with self.assertRaisesRegex(ValueError, "no changes"):
            self.engine.apply(failed)

    def test_new_files_from_the_agent_are_created(self):
        model = Scripted([
            ModelResponse("", [ToolCall("c", "create_file", {"path": "notes/plan.md", "content": "# Plan\n"})]),
            ModelResponse("", [ToolCall("t", "run_tests", {})]),
            ModelResponse("", [ToolCall("f", "finish", {})]),
        ])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Plan", "mode": "agent", "verification": "syntax", "approve_external": True})
            data = self.finished(task)
        self.assertEqual(data["state"], "completed", data["result"])
        self.assertIsNone(data["result"]["base"]["notes/plan.md"])
        self.assertEqual(self.engine.apply(task)["applied"], ["notes/plan.md"])
        self.assertEqual((self.root / "notes" / "plan.md").read_bytes(), b"# Plan\n")

    def test_file_text_shows_workspace_or_candidate(self):
        task = self.engine.submit(FIX)
        self.finished(task)
        self.assertEqual(self.engine.file_text("calc.py")["version"], "workspace")
        candidate = self.engine.file_text("calc.py", task)
        self.assertEqual((candidate["version"], "a + b" in candidate["text"]), ("candidate", True))
        with self.assertRaises(KeyError):
            self.engine.file_text("missing.py")
        with self.assertRaises(ValueError):
            self.engine.file_text("")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def repo(self):
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.root / "a.py").write_text("x = 1\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "First commit")

    def test_not_a_repository(self):
        self.assertEqual(gitops.info(self.root)["repo"], False)
        with self.assertRaisesRegex(ValueError, "not a Git repository"):
            gitops.commit(self.root, "msg", ["a.py"])

    def test_status_history_and_commit_without_hooks(self):
        self.repo()
        (self.root / "a.py").write_text("x = 2\n")
        (self.root / "b.py").write_text("y = 1\n")
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho ran > hook-ran.txt\n")
        hook.chmod(0o755)
        info = gitops.info(self.root)
        self.assertEqual(info["branch"], "main")
        self.assertEqual({s["path"]: s["status"] for s in info["status"]}, {"a.py": "modified", "b.py": "untracked"})
        self.assertEqual(info["commits"][0]["subject"], "First commit")
        made = gitops.commit(self.root, "Apply Dikdik fix", ["a.py"])
        self.assertEqual(made["subject"], "Apply Dikdik fix")
        self.assertFalse((self.root / "hook-ran.txt").exists())  # repository hooks never run
        after = gitops.info(self.root)
        self.assertEqual([s["path"] for s in after["status"]], ["b.py"])  # only the chosen file was committed
        self.assertEqual(after["commits"][0]["subject"], "Apply Dikdik fix")

    def test_commit_validation(self):
        self.repo()
        for message, files in (("", ["a.py"]), ("m", []), ("m", ["--all"]), ("m", "a.py")):
            with self.subTest(message=message, files=files), self.assertRaises(ValueError):
                gitops.commit(self.root, message, files)
        with self.assertRaisesRegex(ValueError, "outside the workspace"):
            gitops.commit(self.root, "m", ["../escape.py"])

    def test_renames_are_one_entry(self):
        self.repo()
        self.git("mv", "a.py", "renamed.py")
        self.assertEqual([(s["path"], s["status"]) for s in gitops.info(self.root)["status"]], [("renamed.py", "renamed")])
