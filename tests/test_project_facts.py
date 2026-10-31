"""Project orientation for the model: measured layout and remembered commands."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse
from dikdik.engine import Engine, TERMINAL
from dikdik.project_facts import MARKER, ProjectFacts, outline

ENV = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}


class ProjectFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_outline_names_stack_scripts_layout_and_line_endings(self):
        files = {"package.json": json.dumps({"scripts": {"check": "tsc", "dev": "next dev"},
                                             "dependencies": {"next": "15", "react": "19"}}),
                 "bun.lock": "{}\n",
                 **{f"app/page{i}.tsx": "export default 1;\r\nexport const x = 2;\r\n" for i in range(3)}}
        text = outline(files, {"worked": {"bun run check": {"last": 2, "seconds": 80}}, "missing": {"npx tsc": {"last": 1}}})
        self.assertTrue(text.startswith(MARKER))
        for expected in ("Next.js", "React", "package manager bun", "mostly CRLF", "app/ (3)", "check: tsc",
                         "`bun run check` (80s)", "unavailable here before: `npx tsc`"):
            self.assertIn(expected, text)
        self.assertEqual(outline({}), "")
        self.assertLessEqual(len(outline({f"d{i}/sub{i}/f.ts": "x\n" * 3 for i in range(3000)}).encode()), 3600)

    def test_only_check_commands_are_remembered_with_their_outcome(self):
        facts = ProjectFacts(self.base / "data", self.base / "project")
        facts.record_command("bun run check", {"status": "passed", "seconds": 12.5})
        facts.record_command("echo hello", {"status": "passed"})
        facts.record_command("npx tsc --noEmit", {"status": "failed", "output": "To get access to the TypeScript compiler, tsc"})
        facts.record_command("bun run test", {"status": "failed", "output": "1 test failed"})  # a real failure says nothing about availability
        known = facts.load()
        self.assertEqual(sorted(known["worked"]), ["bun run check"])
        self.assertEqual(known["worked"]["bun run check"]["seconds"], 12.5)
        self.assertEqual(sorted(known["missing"]), ["npx tsc --noEmit"])
        facts.record_command("npx tsc --noEmit", {"status": "passed"})  # installed since: it moves, never sits in both
        self.assertEqual((sorted(facts.load()["worked"]), facts.load()["missing"]), (["bun run check", "npx tsc --noEmit"], {}))
        self.assertFalse((self.base / "project").exists())  # kept in Dikdik's data folder, never the project

    def test_local_task_starts_with_the_outline(self):
        root = self.base / "repo"
        root.mkdir()
        (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        engine = Engine(root, self.base / "data")
        self.addCleanup(engine.close)
        seen = []

        class Model:
            def respond(self, messages, tools):
                seen.append(messages)
                return ModelResponse("It adds two numbers.", [])

        with patch.dict(os.environ, ENV), patch("dikdik.engine.CompatibleAgentModel", return_value=Model()):
            task = engine.submit({"objective": "What does add do?", "mode": "agent", "verification": "local",
                                  "approve_external": True, "approve_execution": True})
            deadline = time.monotonic() + 30
            while not (engine.store.get(task, include_events=False)["state"] in TERMINAL and not engine.busy(task)):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
        first_user = next(message for message in seen[0] if message["role"] == "user")
        self.assertTrue(first_user["content"].startswith(MARKER))
        self.assertIn("Stack: Python", first_user["content"])


if __name__ == "__main__":
    unittest.main()
