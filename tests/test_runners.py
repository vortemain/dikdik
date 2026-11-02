import threading
import unittest

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.runners import detect_runners


class DetectionTests(unittest.TestCase):
    def test_detects_each_stack(self):
        self.assertEqual([r["kind"] for r in detect_runners({"app.py": "", "test_app.py": ""})], ["unittest"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": '{"scripts": {"test": "bun test"}}'})], ["bun_test"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": "{}", "bun.lock": ""})], ["bun_test"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": '{"scripts": {"test": "vitest"}}'})], ["npm_test"])
        self.assertEqual(detect_runners({"package.json": "{}"}), [])  # no test script, no bun
        self.assertEqual([r["kind"] for r in detect_runners({"Cargo.toml": "[package]"})], ["cargo_test"])
        mixed = detect_runners({"Cargo.toml": "", "package.json": "{}", "bunfig.toml": "", "tests/test_x.py": ""})
        self.assertEqual([r["kind"] for r in mixed], ["unittest", "bun_test", "cargo_test"])
        self.assertEqual(detect_runners({"package.json": "not json"}), [])


class AgentShellTests(unittest.TestCase):
    def test_run_command_keeps_allowed_changes_and_protects_original_tests(self):
        original = {"app.py": "value = 1\n", "tests/test_app.py": "assert True\n"}

        def shell(files, command, timeout):
            self.assertEqual((command, timeout), ("make fix", 60))
            return {"status": "passed", "exit_code": 0, "output": "ok",
                    "changed": {"app.py": "value = 2\n", "tests/test_app.py": "assert False\n", "new.py": "x = 1\n", "blocked.py": "y\n"}}

        class Model:
            calls = [
                ModelResponse("", [ToolCall("1", "run_command", {"command": "make fix"})]),
                ModelResponse("", [ToolCall("2", "run_tests", {})]),
                ModelResponse("", [ToolCall("3", "finish", {})]),
            ]
            seen = []

            def respond(self, messages, tools):
                self.seen.append([t["function"]["name"] for t in tools])
                return self.calls.pop(0)

        events = []
        model = Model()
        result = run_agent(original, "Fix", model, lambda files: {"kind": "unittest", "status": "passed"},
                           lambda kind, data: events.append((kind, data)), threading.Event(),
                           creation_policy=lambda path: path == "new.py", shell=shell)
        self.assertIn("run_command", model.seen[0])
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(result.files["app.py"], "value = 2\n")
        self.assertEqual(result.files["new.py"], "x = 1\n")
        self.assertEqual(result.files["tests/test_app.py"], "assert True\n")
        self.assertNotIn("blocked.py", result.files)
        observation = next(d["observation"] for k, d in events if k == "tool" and d["phase"] == "end" and d["name"] == "run_command")
        self.assertEqual(observation["changed_files"], ["app.py", "new.py"])
        self.assertEqual(observation["rejected_changes"], ["blocked.py", "tests/test_app.py"])
