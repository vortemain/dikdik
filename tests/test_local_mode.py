"""Local mode: the agent edits the real folder and runs commands on this computer, with undo."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL

ENV = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}
LOCAL = {"mode": "agent", "verification": "local", "approve_external": True, "approve_execution": True}
PY = Path(sys.executable).as_posix()


class Script:
    def __init__(self, replies, hook=None):
        self.replies, self.hook, self.seen = list(replies), hook, 0

    def respond(self, messages, tools):
        self.seen += 1
        if self.hook:
            self.hook(self.seen, messages)
        return self.replies.pop(0)


def call(i, name, **args):
    return ModelResponse("", [ToolCall(f"c{i}", name, args)])


FIX = {"path": "calc.py", "before": "a - b", "after": "a + b"}
TEST = "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"


class LocalModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        (self.root / "test_calc.py").write_text(TEST, encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def run_task(self, script, **extra):
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            task = self.engine.submit({"objective": "Fix add", **LOCAL, **extra})
            return task, self.idle(task)

    def idle(self, task, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.engine.store.get(task)
            if state["state"] in TERMINAL and not self.engine.busy(task):
                return state
            time.sleep(0.05)
        self.fail("task did not finish")

    def calc(self):
        return (self.root / "calc.py").read_text(encoding="utf-8")

    def test_edits_land_in_the_folder_and_undo_puts_them_back(self):
        task, state = self.run_task(Script([call(1, "edit_file", **FIX), call(2, "run_tests"), call(3, "finish")]))
        self.assertEqual(state["state"], "completed", state["result"])
        self.assertIn("a + b", self.calc())
        result = state["result"]
        self.assertTrue(result["direct"])
        self.assertTrue(result["tests_verified"])  # the real unittest ran on this computer and passed
        self.assertEqual(result["checks"][-1]["runs_on"], "this computer")
        self.assertEqual(result["changed_files"], ["calc.py"])
        with self.assertRaisesRegex(ValueError, "already in your files"):
            self.engine.apply(task)
        self.assertEqual(self.engine.undo(task), {"restored": ["calc.py"], "conflicts": []})
        self.assertIn("a - b", self.calc())
        with self.assertRaisesRegex(ValueError, "already undone"):
            self.engine.undo(task)

    def test_commands_run_in_the_project_folder(self):
        command = f'"{PY}" -c "open(\'made.py\', \'w\').write(\'made = True\\n\'); print(\'ran here\')"'
        task, state = self.run_task(Script([call(1, "run_command", command=command), ModelResponse("Done.", [])]))
        self.assertEqual((self.root / "made.py").read_text(encoding="utf-8"), "made = True\n")
        terminal = [e["data"] for e in state["events"] if e["kind"] == "terminal_output"]
        self.assertIn("ran here", "".join(t["output"] for t in terminal))
        self.assertEqual(terminal[-1]["exit_code"], 0)
        self.engine.undo(task)
        self.assertFalse((self.root / "made.py").exists())  # a file the turn created goes away again

    def test_a_file_changed_by_someone_else_is_not_overwritten(self):
        def meanwhile(n, messages):
            if n == 2:  # after the agent's first edit was written, the user edits the same file
                (self.root / "calc.py").write_text("def add(a, b):\n    return b + a  # mine\n", encoding="utf-8")
        script = Script([call(1, "edit_file", **FIX), call(2, "run_command", command="echo hi"), ModelResponse("Done.", [])], meanwhile)
        with patch.dict(os.environ, {}):
            task, state = self.run_task(script, confirmation="never")
        self.assertIn("# mine", self.calc())
        self.assertIn("changed outside the agent", state["result"]["error"])

    def test_undo_leaves_files_you_changed_afterwards(self):
        task, _ = self.run_task(Script([call(1, "edit_file", **FIX), ModelResponse("Done.", [])]))
        (self.root / "calc.py").write_text("# rewritten by me\n", encoding="utf-8")
        self.assertEqual(self.engine.undo(task), {"restored": [], "conflicts": ["calc.py"]})
        self.assertEqual(self.calc(), "# rewritten by me\n")

    def test_ask_mode_and_always_allow(self):
        decided = threading.Event()

        def approve():
            for _ in range(200):
                waiting = self.engine.confirmations.waiting(task_box[0]) if task_box else []
                if waiting:
                    self.engine.confirm(task_box[0], {"id": waiting[0], "accept": True, "remember": True})
                    decided.set()
                    return
                time.sleep(0.05)

        task_box = []
        script = Script([call(1, "run_command", command="echo first"), call(2, "run_command", command="echo first"), ModelResponse("Done.", [])])
        threading.Thread(target=approve, daemon=True).start()
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            task_box.append(self.engine.submit({"objective": "Echo", **LOCAL, "confirmation": "ask"}))
            state = self.idle(task_box[0])
        self.assertTrue(decided.is_set())
        asked = [e for e in state["events"] if e["kind"] == "confirmation_requested"]
        self.assertEqual(len(asked), 1)  # the second identical command was remembered
        self.assertEqual(self.engine.allowed_commands(), ["echo first"])
        self.assertEqual(self.engine.forget_command("echo first"), [])

    def test_local_needs_its_approval_and_agent_mode(self):
        for request in ({**LOCAL, "approve_execution": False}, {**LOCAL, "mode": "model"}):
            with self.assertRaises(ValueError):
                self.engine.submit({"objective": "x", **request})


if __name__ == "__main__":
    unittest.main()
