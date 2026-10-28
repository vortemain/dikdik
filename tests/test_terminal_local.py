"""The manual terminal uses real local processes only after explicit project trust."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine


class LocalTerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "project"
        root.mkdir()
        self.engine = Engine(root, Path(self.temp.name) / "data")
        self.addCleanup(self.engine.close)

    def session(self):
        self.engine.set_trusted(True)
        return self.engine.bash.session({"approve_execution": True})["id"]

    def py(self, source):
        return '"' + Path(sys.executable).as_posix() + '" -u -c "' + source + '"'

    def test_session_requires_trust_and_explicit_execution_consent(self):
        with self.assertRaises(PermissionError):
            self.engine.bash.session({"approve_execution": True})
        self.engine.set_trusted(True)
        for body in ({}, {"approve_execution": 1}, None):
            with self.assertRaises(PermissionError):
                self.engine.bash.session(body)
        task = self.session()
        self.assertEqual(self.engine.store.get(task)["state"], "completed")

    def test_real_command_runs_in_chosen_folder_and_preserves_unicode(self):
        task = self.session()
        (self.engine.root / "src").mkdir()
        result = self.engine.bash.execute(task, {
            "command": self.py("from pathlib import Path; import sys; Path('made.txt').write_text('ok'); print('hello', flush=True); sys.stdout.buffer.write(bytes.fromhex('f09f998220e4bda0e5a5bd'))"),
            "cwd": "src", "timeout": 10,
        })
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual((self.engine.root / "src/made.txt").read_text(), "ok")
        self.assertIn("hello", result["output"])
        self.assertIn("🙂 你好", result["output"])
        self.assertEqual(self.engine.bash.search(task, {})["items"][0]["runtime"], "local")

    def test_command_streams_before_exit_and_can_be_stopped(self):
        task = self.session()
        command = self.engine.bash.start(task, {"command": self.py("import time; print('ready', flush=True); time.sleep(30)"), "timeout": 35})
        import time
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            items = self.engine.bash.search(task, {})["items"]
            if any("ready" in item.get("output", "") for item in items):
                break
            time.sleep(.05)
        else:
            self.fail("No output arrived while the command was running")
        self.assertFalse(any(item.get("final") for item in items))
        self.engine.bash.stop(task, command["id"])
        self.assertTrue(self.engine.bash._done[task + ":" + command["id"]].wait(10))
        self.assertEqual(self.engine.bash.search(task, {})["items"][-1]["status"], "cancelled")

    def test_cwd_cannot_leave_project_or_follow_an_escaping_link(self):
        task = self.session()
        for cwd in ("../outside", "/tmp", "missing", "C:/Windows"):
            with self.assertRaises(ValueError):
                self.engine.bash.start(task, {"command": "echo no", "cwd": cwd})
        outside = self.engine.root.parent / "outside"
        outside.mkdir()
        try:
            (self.engine.root / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            return  # Windows developer mode may not allow unprivileged symlinks.
        with self.assertRaises(ValueError):
            self.engine.bash.start(task, {"command": "echo no", "cwd": "link"})

    def test_revoked_trust_refuses_new_local_commands(self):
        task = self.session()
        self.engine.set_trusted(False)
        with self.assertRaises(PermissionError):
            self.engine.bash.start(task, {"command": "echo no"})
        self.assertEqual(self.engine.bash.search(task, {})["items"], [])

    def test_failed_launch_output_is_not_duplicated_and_exit_is_reported(self):
        task = self.session()
        with patch("dikdik.bash_service.local_exec.run", return_value={"status": "failed", "exit_code": None, "output": "Could not start shell"}):
            result = self.engine.bash.execute(task, {"command": "echo hello"})
        self.assertEqual(result["output"], "Could not start shell")
        self.assertEqual(result["status"], "failed")

    def test_restart_marks_unfinished_manual_commands_interrupted(self):
        task = self.session()
        self.engine.bash._insert(task, "b" * 32, 0, "command", {"command": "old command"})
        from dikdik.bash_service import BashService
        restored = BashService(self.engine)
        self.addCleanup(restored.close)
        last = restored.search(task, {})["items"][-1]
        self.assertTrue(last["final"])
        self.assertEqual(last["status"], "interrupted")
