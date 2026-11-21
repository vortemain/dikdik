"""Messages sent mid-run reach the model at the next tool boundary instead of waiting for the run to end."""

import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.agent import STEER_NOTE, ModelResponse, ToolCall, run_agent
from dikdik.engine import Engine


class ScriptedModel:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.messages = []

    def respond(self, messages, tools):
        self.messages.append([dict(m) for m in messages])
        return ModelResponse("", next(self.batches, []), 5)


class RunAgentSteeringTests(unittest.TestCase):
    def test_steered_text_follows_the_tool_results_of_the_current_step(self):
        inbox = [["Only look at app.py, please."]]
        steer_calls = []

        def steer():
            steer_calls.append(1)
            return inbox.pop(0) if inbox else []

        model = ScriptedModel([[ToolCall("1", "read_file", {"path": "app.py"})], [ToolCall("2", "finish", {})]])
        run_agent({"app.py": "value = 1\n"}, "Review the code", model, None,
                           lambda kind, data: None, threading.Event(), read_only=True, steer=steer)
        self.assertGreaterEqual(len(steer_calls), 1)
        second = model.messages[1]
        tool_index = max(i for i, m in enumerate(second) if m["role"] == "tool")
        steered = [i for i, m in enumerate(second) if m["role"] == "user" and str(m["content"]).startswith(STEER_NOTE)]
        self.assertEqual(len(steered), 1)
        self.assertGreater(steered[0], tool_index)
        self.assertIn("Only look at app.py, please.", second[steered[0]]["content"])

    def test_without_steering_nothing_is_added(self):
        model = ScriptedModel([[ToolCall("1", "read_file", {"path": "app.py"})], [ToolCall("2", "finish", {})]])
        run_agent({"app.py": "value = 1\n"}, "Review the code", model, None,
                  lambda kind, data: None, threading.Event(), read_only=True, steer=lambda: [" ", None])
        self.assertFalse(any(m["role"] == "user" and str(m["content"]).startswith(STEER_NOTE) for m in model.messages[1]))


class EngineSteeringTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "repo"
        root.mkdir()
        (root / "notes.txt").write_text("hello\n")
        self.engine = Engine(root, Path(temp.name) / "data")
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create({"objective": "Summarise", "mode": "chat", "verification": "syntax"})

    def test_same_mode_messages_are_taken_once_and_recorded(self):
        chat = {"objective": "Shorter, please", "mode": "chat", "verification": "syntax"}
        other = {"objective": "Now fix it", "mode": "agent", "verification": "syntax"}
        with_file = {"objective": "See attached", "mode": "chat", "verification": "syntax", "attachments": ["a"]}
        with self.engine.lock:
            self.engine.pending[self.task] = [chat, other, with_file]
        self.assertEqual(self.engine._steer(self.task, "chat"), ["Shorter, please"])
        self.assertEqual(self.engine.pending[self.task], [other, with_file])
        self.assertEqual(self.engine._steer(self.task, "chat"), [])
        steered = [e["data"] for e in self.engine.store.get(self.task)["events"] if e["kind"] == "user_steered"]
        self.assertEqual(steered, [{"text": "Shorter, please"}])

    def test_the_queue_is_cleared_when_everything_was_taken(self):
        with self.engine.lock:
            self.engine.pending[self.task] = [{"objective": "One", "mode": "chat", "verification": "syntax"},
                                              {"objective": "Two", "mode": "chat", "verification": "syntax"}]
        self.assertEqual(self.engine._steer(self.task, "chat"), ["One", "Two"])
        self.assertNotIn(self.task, self.engine.pending)


if __name__ == "__main__":
    unittest.main()
