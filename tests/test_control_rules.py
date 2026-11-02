"""Fast unit tests for the approval engine, written for mutants the engine-level tests missed."""

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import control
from dikdik.agent import ToolCall
from dikdik.store import Store


def call(name="read_file", **arguments):
    return ToolCall("c1", name, arguments)


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.confirmations = control.Confirmations()

    def decide_soon(self, **decision):
        def worker():
            for _ in range(200):
                if ("t", "c1") in self.confirmations.pending:
                    self.confirmations.decide("t", "c1", **decision)
                    return
                time.sleep(0.01)
        threading.Thread(target=worker, daemon=True).start()

    def test_no_decision_never_allows_the_action(self):
        allowed, reason = self.confirmations.wait("t", call(), threading.Event(), timeout=0.3)
        self.assertEqual((allowed, reason), (False, "No decision was made, so the action did not run"))
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(self.confirmations.wait("t", call(), cancel)[0])
        self.assertEqual(self.confirmations.pending, {})

    def test_allow_reject_and_remember(self):
        self.decide_soon(accept=True, remember=True)
        self.assertEqual(self.confirmations.wait("t", call(), threading.Event(), timeout=5), (True, ""))
        self.assertIn(("t", "c1"), self.confirmations.remembered)
        self.confirmations.remembered.clear()
        self.decide_soon(accept=False, remember=True)
        self.assertEqual(self.confirmations.wait("t", call(), threading.Event(), timeout=5), (False, "The user rejected this action"))
        self.assertEqual(self.confirmations.remembered, set())  # a rejection is never remembered
        with self.assertRaises(ValueError):
            self.confirmations.decide("t", "c1", "yes")
        with self.assertRaises(KeyError):
            self.confirmations.decide("t", "missing", True)


class SecretRequestTests(unittest.TestCase):
    def test_answers_are_validated_and_delivered(self):
        requests = control.SecretRequests()
        def answer():
            for _ in range(200):
                if ("t", "r") in requests.pending:
                    for bad in ("   ", "", "x" * 65537, 7):
                        with self.assertRaises(ValueError):
                            requests.answer("t", "r", bad)
                    requests.answer("t", "r", "s3cret-value")
                    return
                time.sleep(0.01)
        threading.Thread(target=answer, daemon=True).start()
        self.assertEqual(requests.wait("t", "r", threading.Event(), timeout=5), "s3cret-value")
        self.assertIsNone(requests.wait("t", "r", threading.Event(), timeout=0.3))  # nobody answered

    def test_an_answer_after_several_polls_is_still_delivered(self):
        requests = control.SecretRequests()
        def late():
            time.sleep(0.7)  # past several 0.2 s polls, well inside the deadline
            requests.answer("t", "r", "late-value")
        threading.Thread(target=late, daemon=True).start()
        self.assertEqual(requests.wait("t", "r", threading.Event(), timeout=10), "late-value")


class PermissionRuleTests(unittest.TestCase):
    def test_blank_and_star_resources_match_everything(self):
        for resource in ("*", ""):
            rules = [{"action": "read", "resource": resource, "effect": "deny"}]
            self.assertEqual(control.permission_rule_effect(rules, call(path="src/app.py")), "deny")
        rules = [{"action": "read", "resource": "docs/*", "effect": "deny"}]
        self.assertIsNone(control.permission_rule_effect(rules, call(path="src/app.py")))

    def test_resource_length_limit_is_exact(self):
        control.parse_rules([{"action": "read", "resource": "x" * 256, "effect": "allow"}])
        with self.assertRaises(ValueError):
            control.parse_rules([{"action": "read", "resource": "x" * 257, "effect": "allow"}])


class HookRuleTests(unittest.TestCase):
    def test_a_pattern_blocks_only_matching_calls(self):
        rules = control.parse_hooks({"block": [{"tool": "run_command", "pattern": "rm -rf"}]}, "project")
        self.assertIsNotNone(control.blocked_by(rules, call("run_command", command="rm -rf /tmp/x")))
        self.assertIsNone(control.blocked_by(rules, call("run_command", command="ls")))
        self.assertIsNone(control.blocked_by(rules, call("read_file", command="rm -rf")))

    def test_arguments_are_matched_in_a_stable_order(self):
        rules = control.parse_hooks({"block": [{"pattern": '"a": 1, "b": 2'}]}, "project")
        self.assertIsNotNone(control.blocked_by(rules, ToolCall("c", "x", {"b": 2, "a": 1})))

    def test_an_invalid_pattern_blocks_and_explains(self):
        rules = control.parse_hooks({"block": [{"pattern": "("}]}, "project")
        self.assertIn("not a valid regular expression", control.blocked_by(rules, call(path="a")))


class SubagentLoaderTests(unittest.TestCase):
    def test_front_matter_names_and_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / ".dikdik" / "agents"
            folder.mkdir(parents=True)
            (folder / "a.md").write_text("---\nname: Reviewer Bot\ndescription: " + "d" * 400 + "\n---\n" + "i" * 9000, encoding="utf-8")
            (folder / "b.md").write_text("no front matter body", encoding="utf-8")
            (folder / "c.md").write_text("---\nname: " + "n" * 50 + "\n---\nbody", encoding="utf-8")
            for index in range(25):
                (folder / f"z{index:02d}.md").write_text("x", encoding="utf-8")
            agents = {a["name"]: a for a in control.load_subagents(root, root / "none")}
        self.assertEqual(agents["reviewer-bot"]["source"], "project")
        self.assertEqual((len(agents["reviewer-bot"]["description"]), len(agents["reviewer-bot"]["instructions"])), (300, 8000))
        self.assertEqual(agents["b"]["instructions"], "no front matter body")
        self.assertIn("n" * 40, agents)
        self.assertEqual(len(agents), 1 + 20)  # the built-in explorer plus at most 20 files per folder


class JudgeTests(unittest.TestCase):
    def test_context_window_and_bounded_reason(self):
        seen = {}
        def complete(config, messages, limit):
            seen["count"] = len(messages)
            return json.dumps({"achieved": True, "reason": "r" * 600, "next": ""})
        with patch.object(control, "complete", complete):
            verdict = control.judge(None, "goal", [{"role": "user", "content": str(i)} for i in range(30)])
        self.assertEqual(seen["count"], 1 + 20 + 1)
        self.assertEqual((verdict["achieved"], len(verdict["reason"])), (True, 500))
        with patch.object(control, "complete", lambda *a: "achieved: maybe"):
            self.assertFalse(control.judge(None, "goal", [])["achieved"])


class AttachmentTests(unittest.TestCase):
    def test_types_and_base64_are_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            attachments = control.Attachments(Store(Path(temp) / "s.sqlite3"), Path(temp) / "files")
            good = attachments.add({"name": "notes.txt", "type": "text/plain", "data": base64.b64encode(b"hi").decode()})
            self.assertEqual(good["size"], 2)
            exact = attachments.add({"name": "a.txt", "type": "text/plain", "data": base64.b64encode(b"x" * 65536).decode()})
            over = attachments.add({"name": "b.txt", "type": "text/plain", "data": base64.b64encode(b"x" * 65537).decode()})
            self.assertNotIn("(truncated)", attachments.resolve([exact["id"]])[2])
            self.assertIn("(truncated)", attachments.resolve([over["id"]])[2])
            for bad in ({"type": "not a mime type"}, {"type": "x" * 101 + "/y"}, {"data": "QUJD@"}, {"data": ""},
                        {"name": ".hidden"}, {"name": "a/b.txt"}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    attachments.add({"name": "notes.txt", "type": "text/plain", "data": "aGk=", **bad})


if __name__ == "__main__":
    unittest.main()
