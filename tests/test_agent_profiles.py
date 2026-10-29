import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse
from dikdik.engine import Engine, TERMINAL
from dikdik.profiles import ProfileStore
from dikdik.secrets_store import SecretsStore


class Recorder:
    def __init__(self):
        self.seen, self.tools = [], []

    def respond(self, messages, tools):
        self.seen.append(messages)
        self.tools.append([t["function"]["name"] for t in tools])
        return ModelResponse("Done.", [])


class AgentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        (self.root / ".dikdik" / "skills").mkdir(parents=True)
        (self.root / "a.py").write_text("x = 1\n")
        (self.root / ".dikdik" / "skills" / "style.md").write_text("Use tabs.\n")
        self.data = base / "data"
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        self.agents = self.engine.agents

    def run_task(self, **request):
        model = Recorder()
        captured = {}

        def adapter(approval, resolve, on_text=None, cancel=None):
            captured["resolve"] = resolve
            return model

        with patch("dikdik.engine.CompatibleAgentModel", side_effect=adapter):
            task = self.engine.submit({"objective": "Go", "mode": "agent", "approve_external": True, "verification": "syntax", **request})
            for _ in range(400):
                data = self.engine.store.get(task)
                if data["state"] in TERMINAL and not self.engine.active:
                    break
                time.sleep(0.01)
        return data, model, captured

    def test_validation(self):
        made = self.agents.create({"name": "Reviewer", "instructions": "Only review.", "max_model_calls": 5, "allow_shell": False})
        self.assertEqual(self.agents.get(made["id"])["max_model_calls"], 5)
        for bad in ({"name": "Default"}, {"name": ""}, {"name": "x", "max_model_calls": 0}, {"name": "x", "deadline_seconds": 99999},
                    {"name": "x", "allow_shell": "no"}, {"name": "x", "colour": "red"}, {"name": "x", "model_profile_id": "missing"},
                    {"name": "Reviewer"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.agents.create(bad)
        updated = self.agents.update(made["id"], {"instructions": "Review carefully.", "max_model_calls": None})
        self.assertEqual((updated["instructions"], updated["max_model_calls"]), ("Review carefully.", None))
        with self.assertRaises(KeyError):
            self.agents.update("missing", {"name": "y"})
        self.assertEqual(self.agents.delete(made["id"]), {"deleted": made["id"]})

    def test_profile_shapes_the_agent(self):
        made = self.agents.create({"name": "Careful", "instructions": "Explain before editing.", "max_model_calls": 3,
                                   "allow_shell": False, "use_skills": False})
        data, model, _ = self.run_task(agent_profile=made["id"], verification="docker", approve_execution=True)
        self.assertEqual(data["state"], "completed", data["result"])
        texts = [m["content"] for m in model.seen[0] if m["role"] == "user"]
        self.assertIn("Instructions from the user's agent profile “Careful”:\nExplain before editing.", texts)
        self.assertFalse(any("Use tabs." in t for t in texts))  # skills switched off
        self.assertNotIn("run_command", model.tools[0])  # shell switched off despite Docker approval
        started = next(e for e in data["events"] if e["kind"] == "agent_started")["data"]
        self.assertEqual((started["profile"], started["max_model_calls"]), ("Careful", 3))
        self.assertEqual(data["request"]["agent_profile"], made["id"])

    def test_default_profile_keeps_skills_and_shell(self):
        data, model, _ = self.run_task(verification="docker", approve_execution=True)
        self.assertIn("run_command", model.tools[0])
        self.assertTrue(any("Use tabs." in m["content"] for m in model.seen[0]))
        self.assertEqual(next(e for e in data["events"] if e["kind"] == "agent_started")["data"]["profile"], "Default")

    def test_profile_model_is_used_instead_of_the_default(self):
        secrets = SecretsStore(self.data / "secrets.bin", self.data / "secrets.key")
        secrets.set("k1", b"sk-one")
        secrets.set("k2", b"sk-two")
        profiles = ProfileStore(self.engine.store)
        profiles.create({"name": "Default model", "provider": "openai", "model": "gpt-default", "credential_secret_ref": "k1"})
        other = profiles.create({"name": "Claude", "provider": "anthropic", "model": "claude-fast", "credential_secret_ref": "k2"})[1]["id"]
        made = self.agents.create({"name": "Fast", "model_profile_id": other})
        _, _, captured = self.run_task(agent_profile=made["id"])
        config = captured["resolve"]()
        self.assertEqual((config.model, config.key), ("claude-fast", "sk-two"))
        _, _, captured = self.run_task()
        self.assertEqual(captured["resolve"]().model, "gpt-default")

    def test_missing_profile_is_rejected_at_submit(self):
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            self.engine.submit({"objective": "Go", "mode": "agent", "approve_external": True, "verification": "syntax", "agent_profile": "gone"})
