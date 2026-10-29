import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse
from dikdik.engine import Engine, TERMINAL
from dikdik.skills import CONTEXT_LIMIT, SKILL_UNTRUSTED_MARKER


class Recorder:
    def __init__(self):
        self.seen = []

    def respond(self, messages, tools):
        self.seen.append(messages)
        return ModelResponse("Answered.", [])


class SkillContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        (self.root / ".dikdik" / "skills" / "deploy").mkdir(parents=True)
        (self.root / "app.py").write_text("x = 1\n")
        (self.root / ".dikdik" / "skills" / "style.md").write_text("Use four spaces.\n")
        (self.root / ".dikdik" / "skills" / "deploy" / "SKILL.md").write_text(
            "---\nname: Deploy\ndescription: How we ship\n---\nRun make release.\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.skills = self.engine.skills

    def test_folder_skills_and_front_matter(self):
        rows = {row["id"]: row for row in self.skills.enumerate_skills()}
        self.assertEqual((rows["deploy"]["name"], rows["deploy"]["description"]), ("Deploy", "How we ship"))
        self.assertEqual(rows["style"]["description"], "")

    def test_context_is_marked_untrusted_and_skips_disabled(self):
        text, names = self.skills.context()
        self.assertTrue(text.startswith(SKILL_UNTRUSTED_MARKER))
        self.assertIn("Deploy", names)
        self.assertIn("style", names)
        self.assertIn("planning", names)  # a bundled skill is present
        self.assertIn("Run make release.", text)
        self.assertNotIn("name: Deploy", text)  # front matter is not repeated as text
        self.skills.set_enabled("style", False)
        self.assertNotIn("style", self.skills.context()[1])

    def test_skill_routing_loads_relevant_skills_only(self):
        # A bug-fix request routes to debugging and tdd, not ui-ux-design
        text, names = self.skills.context("Fix the crash in the login form", "agent")
        self.assertIn("debugging", names)
        self.assertIn("test-driven-development", names)
        self.assertNotIn("ui-ux-design", names)
        # A UI request routes to ui-ux-design
        text, names = self.skills.context("Build a website landing page", "agent")
        self.assertIn("ui-ux-design", names)
        self.assertIn("planning", names)  # always-on for code work
        # A documentation request routes to document-creation
        text, names = self.skills.context("Write a README for the project", "agent")
        self.assertIn("document-creation", names)
        # A simple question loads nothing specific
        text, names = self.skills.context("What is 2 + 2?", "agent")
        self.assertIn("planning", names)  # always-on fallback for code mode

    def test_context_is_bounded(self):
        for index in range(8):
            (self.root / ".dikdik" / "skills" / f"big{index}.md").write_text("x" * 20000)
        text, names = self.skills.context()
        self.assertLessEqual(len(text.encode()), CONTEXT_LIMIT + 1024)
        self.assertLess(len(names), 10)

    def test_create_and_delete(self):
        status, made = self.skills.create({"name": "Testing Rules", "scope": "project", "content": "Use unittest.", "description": "Testing"})
        self.assertEqual((status, made["id"]), (201, "testing-rules"))
        self.assertIn("description: Testing", (self.root / ".dikdik" / "skills" / "testing-rules.md").read_text())
        for bad in ({"name": "../x", "content": "y"}, {"name": "ok", "content": ""}, {"name": "ok", "content": "y", "scope": "global"},
                    {"name": "Testing Rules", "content": "again"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.skills.create(bad)
        self.assertEqual(self.skills.delete("testing-rules")[1], {"deleted": "testing-rules"})
        with self.assertRaisesRegex(ValueError, "Folder skills"):
            self.skills.delete("deploy")
        with self.assertRaises(KeyError):
            self.skills.delete("missing")

    def test_agent_receives_enabled_skills_and_the_task_records_them(self):
        model = Recorder()
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Explain", "mode": "agent", "verification": "syntax", "approve_external": True})
            for _ in range(400):
                data = self.engine.store.get(task)
                if data["state"] in TERMINAL and not self.engine.active:
                    break
                time.sleep(0.01)
        shown = model.seen[0]
        skill_message = next(m["content"] for m in shown if m["role"] == "user" and SKILL_UNTRUSTED_MARKER in m["content"])
        self.assertIn("Use four spaces.", skill_message)
        self.assertEqual(shown[-1]["content"], "Explain")  # the objective still comes last
        used = next(e for e in data["events"] if e["kind"] == "skills")
        self.assertIn("Deploy", used["data"]["names"])
        self.assertIn("style", used["data"]["names"])
