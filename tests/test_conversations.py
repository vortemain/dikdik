import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL
from dikdik.repository import inventory


def offline(text, before, after):
    return {"objective": text, "mode": "offline", "verification": "syntax", "edits": [{"path": "calculator.py", "before": before, "after": after}]}


AGENT = {"mode": "agent", "verification": "docker", "approve_external": True, "approve_execution": True}


class ScriptedModel:
    """Replays responses and records every conversation it was shown."""

    def __init__(self, responses):
        self.responses, self.seen = list(responses), []

    def respond(self, messages, tools):
        self.seen.append(messages)
        return self.responses.pop(0)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        self.data = base / "data"
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)

    def idle(self, task):
        for _ in range(400):
            with self.engine.lock:
                busy = bool(self.engine.active) or bool(self.engine.pending)
            data = self.engine.store.get(task)
            if not busy and data["state"] in TERMINAL:
                return data
            time.sleep(0.01)
        self.fail("task did not finish")

    def kinds(self, data):
        return [event["kind"] for event in data["events"]]

    def test_offline_follow_up_builds_on_the_previous_candidate(self):
        task = self.engine.submit(offline("Fix addition", "a - b", "a + b"))
        self.idle(task)
        self.assertFalse(self.engine.send(task, offline("Swap the operands", "a + b", "b + a")))
        data = self.idle(task)
        self.assertEqual(data["state"], "completed", data["result"])
        diff = data["result"]["diff"]
        self.assertIn("-    return a - b", diff)  # diff stays relative to the untouched source
        self.assertIn("+    return b + a", diff)
        self.assertEqual(self.kinds(data).count("running"), 2)
        self.assertNotIn("plan", self.kinds(data))  # internal execution stages are not an agent-generated plan
        message = next(e for e in data["events"] if e["kind"] == "user_message")
        self.assertEqual(message["data"]["text"], "Swap the operands")
        self.assertEqual((self.root / "calculator.py").read_text(), "def add(a, b):\n    return a - b\n")

    def test_a_question_gets_the_model_answer_without_a_canned_plan(self):
        model = ScriptedModel([ModelResponse("Overly uniform layouts and generic copy can make a site feel AI-generated.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.idle(self.engine.submit({"objective": "What makes a website look AI-generated?", "mode": "agent",
                                               "verification": "syntax", "approve_external": True}))
        self.assertEqual(data["state"], "completed")
        self.assertNotIn("plan", self.kinds(data))
        messages = [e["data"]["content"] for e in data["events"] if e["kind"] == "agent_message"]
        self.assertEqual(messages, ["Overly uniform layouts and generic copy can make a site feel AI-generated."])

    def test_follow_up_errors(self):
        with self.assertRaises(KeyError):
            self.engine.send("missing", offline("x", "a - b", "a + b"))
        task = self.engine.submit(offline("Fix", "a - b", "a + b"))
        self.idle(task)
        with self.assertRaisesRegex(ValueError, "Objective"):
            self.engine.send(task, {**offline("", "a", "b")})
        agent_request = {"objective": "Again", "mode": "agent", "verification": "syntax", "approve_external": True}
        other = self.engine.store.create(agent_request)
        self.engine.store.state(other, "running")
        with self.engine.lock:
            self.engine.active[other] = threading.Event()
        try:
            with self.assertRaisesRegex(ValueError, "An agent task is already running"):
                self.engine.send(task, agent_request)
        finally:
            with self.engine.lock:
                self.engine.active.pop(other)

    def test_message_sent_while_running_is_queued_then_run(self):
        gate, calls = threading.Event(), []

        def slow_inventory(root):
            calls.append(1)
            if len(calls) == 1:
                gate.wait(5)
            return inventory(root)

        with patch("dikdik.engine.inventory", side_effect=slow_inventory):
            task = self.engine.submit(offline("Fix addition", "a - b", "a + b"))
            for _ in range(200):
                if calls:
                    break
                time.sleep(0.01)
            self.assertTrue(self.engine.send(task, offline("Then swap", "a + b", "b + a")))
            queued = [e for e in self.engine.store.get(task)["events"] if e["kind"] == "user_message"]
            self.assertTrue(queued[0]["data"]["queued"])
            gate.set()
            data = self.idle(task)
        self.assertEqual(data["state"], "completed", data["result"])
        self.assertIn("+    return b + a", data["result"]["diff"])

    def test_stop_drops_queued_messages(self):
        gate = threading.Event()

        def slow_inventory(root):
            gate.wait(5)
            return inventory(root)

        with patch("dikdik.engine.inventory", side_effect=slow_inventory):
            task = self.engine.submit(offline("Fix addition", "a - b", "a + b"))
            time.sleep(0.05)
            self.engine.send(task, offline("Then swap", "a + b", "b + a"))
            self.assertTrue(self.engine.cancel(task))
            gate.set()
            data = self.idle(task)
        self.assertEqual(data["state"], "cancelled")
        self.assertEqual(self.kinds(data).count("running"), 1)
        stop = next(e for e in data["events"] if e["kind"] == "cancellation_requested")
        self.assertEqual(stop["data"]["dropped_messages"], 1)

    def test_agent_follow_up_sees_history_and_earlier_changes(self):
        first = ScriptedModel([
            ModelResponse("Fixing the operator.", [ToolCall("e", "edit_file", {"path": "calculator.py", "before": "a - b", "after": "a + b"})]),
            ModelResponse("", [ToolCall("t", "run_tests", {})]),
            ModelResponse("", [ToolCall("f", "finish", {})]),
        ])
        second = ScriptedModel([ModelResponse("It now returns the sum of a and b.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[first, second]), \
                patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "passed"}):
            task = self.engine.submit({"objective": "Fix addition", **AGENT})
            self.assertEqual(self.idle(task)["state"], "completed")
            self.engine.send(task, {"objective": "What does add return now?", **AGENT})
            data = self.idle(task)
        self.assertEqual(data["state"], "completed", data["result"])
        shown = second.seen[0]
        texts = [m["content"] for m in shown if m["role"] in {"user", "assistant"} and not m["content"].startswith("<!-- dikdik-untrusted")]
        self.assertEqual(texts[0], "Fix addition")  # the original objective is the first non-skill user message
        self.assertIn("Fixing the operator.", texts)
        self.assertTrue(any("Turn ended: completed" in t and "calculator.py" in t for t in texts))
        self.assertIn("already contains changes", texts[-1])
        # The earlier change is still the candidate, so the diff survives a question-only turn.
        self.assertIn("+    return a + b", data["result"]["diff"])
        self.assertFalse(data["result"]["turn_changed"])  # the question changed nothing
        replies = [e["data"]["content"] for e in data["events"] if e["kind"] == "agent_message"]
        self.assertEqual(replies, ["Fixing the operator.", "It now returns the sum of a and b."])

    def test_agent_can_answer_without_tools(self):
        model = ScriptedModel([ModelResponse("This project adds two numbers.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.idle(self.engine.submit({"objective": "What does this project do?", **AGENT}))
        self.assertEqual(data["state"], "completed", data["result"])
        self.assertEqual(data["result"]["diff"], "")
        self.assertFalse(data["result"]["tests_verified"])

    def test_agent_tool_events_carry_arguments_for_the_chat(self):
        model = ScriptedModel([
            ModelResponse("", [ToolCall("r", "read_file", {"path": "calculator.py"})]),
            ModelResponse("Read it.", []),
        ])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.idle(self.engine.submit({"objective": "Look", **AGENT}))
        start = next(e for e in data["events"] if e["kind"] == "agent_tool" and e["data"]["phase"] == "start")
        self.assertEqual(start["data"]["arguments"], {"path": "calculator.py"})
        end = next(e for e in data["events"] if e["kind"] == "agent_tool" and e["data"]["phase"] == "end")
        self.assertIn("return a - b", json.dumps(end["data"]["observation"]))

    def test_rename_pin_archive_and_delete(self):
        task = self.engine.submit(offline("Fix addition", "a - b", "a + b"))
        self.idle(task)
        self.engine.store.update_meta(task, {"title": "  Addition fix  ", "pinned": True, "archived": True})
        row = self.engine.store.list()[0]
        self.assertEqual((row["title"], row["pinned"], row["archived"], row["objective"]), ("Addition fix", True, True, "Fix addition"))
        for bad in ({"state": "completed"}, {"pinned": "yes"}, {"title": "a\nb"}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.store.update_meta(task, bad)
        with self.assertRaises(KeyError):
            self.engine.store.update_meta("missing", {"title": "x"})
        self.assertTrue((self.data / "artifacts" / task).is_dir())
        self.engine.delete(task)
        self.assertFalse((self.data / "artifacts" / task).exists())
        with self.assertRaises(KeyError):
            self.engine.store.get(task)
        with self.assertRaises(KeyError):
            self.engine.delete(task)

    def test_running_task_cannot_be_deleted(self):
        with self.engine.lock:
            self.engine.active["busy"] = threading.Event()
        try:
            with self.assertRaisesRegex(ValueError, "Stop the task"):
                self.engine.delete("busy")
        finally:
            with self.engine.lock:
                self.engine.active.pop("busy")

    def test_deleting_a_just_finished_task_waits_for_the_worker(self):
        """A conversation deleted the instant it finished was refused with "Stop the task
        before deleting it": the terminal state is published a moment before the worker
        lets go. Deleting now waits that moment out and removes the conversation."""
        task = self.engine.submit(offline("Fix addition", "a - b", "a + b"))
        self.idle(task)
        with self.engine.lock:
            self.engine.active[task] = threading.Event()  # worker still winding down
        releaser = threading.Timer(0.3, lambda: self.engine.active.pop(task, None))
        releaser.start()
        self.addCleanup(releaser.join)
        started = time.perf_counter()
        self.engine.delete(task)
        self.assertGreaterEqual(time.perf_counter() - started, 0.2)
        with self.assertRaises(KeyError):
            self.engine.store.get(task)

    def test_deleting_a_running_task_stops_it_first(self):
        """Delete on a running conversation stops it the way the Stop button does, then
        removes it — the user never has to stop first and delete again."""
        task = self.engine.store.create(offline("Fix addition", "a - b", "a + b"))
        self.engine.store.state(task, "running")
        cancel = threading.Event()
        with self.engine.lock:
            self.engine.active[task] = cancel
            self.engine.pending[task] = [offline("Queued follow-up", "a + b", "b + a")]
        releaser = threading.Timer(0.3, lambda: self.engine.active.pop(task, None))  # the worker honours the cancel
        releaser.start()
        self.addCleanup(releaser.join)
        started = time.perf_counter()
        self.engine.delete(task)
        self.assertGreaterEqual(time.perf_counter() - started, 0.2)
        self.assertTrue(cancel.is_set())  # deletion asked the worker to stop
        self.assertNotIn(task, self.engine.pending)
        with self.assertRaises(KeyError):
            self.engine.store.get(task)

    def test_deleting_a_task_that_would_not_stop_refuses_honestly(self):
        """If the worker cannot reach a step boundary in time, the deletion says so instead
        of deleting half a conversation — and a later attempt succeeds."""
        task = self.engine.store.create(offline("Fix addition", "a - b", "a + b"))
        self.engine.store.state(task, "running")
        with self.engine.lock:
            self.engine.active[task] = threading.Event()  # a worker that never lets go
        with patch("dikdik.engine.STOP_GRACE", 0.4):
            started = time.perf_counter()
            with self.assertRaisesRegex(ValueError, "still stopping"):
                self.engine.delete(task)
            self.assertGreaterEqual(time.perf_counter() - started, 0.3)
        with self.engine.lock:
            self.engine.active.pop(task, None)
        self.engine.store.state(task, "cancelled")
        self.engine.delete(task)  # once it really finished, deletion goes through
        with self.assertRaises(KeyError):
            self.engine.store.get(task)
