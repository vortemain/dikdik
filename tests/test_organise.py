import io
import threading
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from dikdik.agent import ModelResponse, run_agent
from dikdik.engine import Engine
from dikdik.providers import _assemble


class OrganiseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create({"objective": "Fix add", "mode": "offline"})

    def summary(self):
        return next(t for t in self.engine.store.list() if t["id"] == self.task)

    def test_folders_and_tags(self):
        self.assertEqual((self.summary()["folder"], self.summary()["tags"]), ("", []))
        self.engine.store.update_meta(self.task, {"folder": " Bugs ", "tags": ["UI", "ui", "needs review"]})
        self.assertEqual((self.summary()["folder"], self.summary()["tags"]), ("Bugs", ["needs review", "ui"]))
        self.engine.store.update_meta(self.task, {"folder": None, "tags": []})
        self.assertEqual((self.summary()["folder"], self.summary()["tags"]), ("", []))
        for bad in ({"folder": "x" * 61}, {"folder": "a\nb"}, {"tags": "ui"}, {"tags": ["#!"]}, {"tags": [str(i) for i in range(11)]}, {"colour": "red"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.store.update_meta(self.task, bad)

    def test_app_settings_for_git_and_sounds(self):
        saved = self.engine.update_app_settings({"git_name": "Ada", "git_email": "ada@example.com", "sounds": True})
        self.assertEqual((saved["git_name"], saved["sounds"]), ("Ada", True))
        self.assertEqual(self.engine.git_identity(), ("Ada", "ada@example.com"))
        for bad in ({"git_email": "not an email"}, {"git_name": "a\nb"}, {"sounds": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.update_app_settings(bad)

    def test_agent_info_lists_tools_skills_subagents_and_hooks(self):
        (self.root / ".dikdik").mkdir()
        (self.root / ".dikdik" / "hooks.json").write_text(json.dumps({"block": [{"tool": "run_command", "pattern": "git push", "message": "No pushes"}]}), encoding="utf-8")
        info = self.engine.agent_info()
        names = {tool["name"] for tool in info["tools"]}
        self.assertTrue({"read_file", "edit_file", "run_command", "browse_url", "delegate", "task_tracker"} <= names)
        self.assertEqual(info["subagents"][0]["name"], "explorer")
        self.assertEqual(info["hooks"][0]["message"], "No pushes")
        self.assertIsInstance(info["skills"], list)

    def test_archive_contains_events_and_candidate_files(self):
        artifact = self.engine.data / "artifacts" / self.task
        artifact.mkdir(parents=True)
        (artifact / "calc.py").write_text("x = 2\n", encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(self.engine.archive(self.task))) as bundle:
            self.assertEqual(sorted(bundle.namelist()), ["candidate/calc.py", "conversation.json"])
            self.assertEqual(json.loads(bundle.read("conversation.json"))["id"], self.task)
        with self.assertRaises(KeyError):
            self.engine.archive("missing")


class ThinkingTests(unittest.TestCase):
    def test_streamed_reasoning_is_kept_apart_from_the_reply(self):
        chunks = [b'data: {"choices":[{"delta":{"reasoning_content":"Let me think. "}}]}',
                  b'data: {"choices":[{"delta":{"reasoning_content":"It is add."}}]}',
                  b'data: {"choices":[{"delta":{"content":"Fixed."},"finish_reason":"stop"}]}', b"data: [DONE]"]
        message = _assemble(chunks, None)["choices"][0]["message"]
        self.assertEqual((message["content"], message["reasoning_content"]), ("Fixed.", "Let me think. It is add."))

    def test_agent_records_thinking_as_its_own_event(self):
        class Model:
            def respond(self, messages, tools):
                return ModelResponse("Nothing to change.", [], reasoning="The code is fine.")
        events = []
        run_agent({"a.py": "x = 1\n"}, "Look", Model(), None, lambda kind, data: events.append((kind, data)), threading.Event())
        self.assertIn(("thinking", {"content": "The code is fine."}), events)
        kinds = [kind for kind, _ in events]
        self.assertLess(kinds.index("thinking"), kinds.index("message"))


if __name__ == "__main__":
    unittest.main()
