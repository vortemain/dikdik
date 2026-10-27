"""ACP agent runs: a real subprocess speaking the protocol, in a throwaway copy."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine

AGENT = (Path(__file__).parent / "fixtures" / "fake_acp_agent.py").as_posix()
KEY = "sk-fake-agent-key-0123456789"


class AcpRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (base / "outside.txt").write_text("private", encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.engine.models.secrets.set("fake-key", KEY.encode())

    def agent(self, mode="work", **extra):
        return self.engine.acp_agents.create({"name": "Fake agent", "command": sys.executable, "args": f'"{AGENT}" {mode}',
                                              "credentials": ["FAKE_AGENT_KEY=fake-key"], **extra}, time.time())["id"]

    def run_task(self, agent_id, **extra):
        return self.engine.submit({"objective": "Change value to 2", "mode": "acp", "verification": "syntax",
                                   "approve_external": True, "approve_host": True, "acp_agent": agent_id, **extra})

    def wait(self, task, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.engine.store.get(task)
            if state["state"] not in {"running", "queued"} and not self.engine.busy(task):
                return state
            time.sleep(0.1)
        self.fail("the ACP task did not finish")

    def messages(self, state, kind):
        return [e["data"] for e in state["events"] if e["kind"] == kind]

    def test_candidate_comes_from_the_copy_and_workspace_stays_untouched(self):
        with patch.dict(os.environ, {"DIKDIK_SERVER_ONLY": "do-not-pass"}):
            state = self.wait(self.run_task(self.agent()))
        self.assertEqual(state["state"], "completed", state["result"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.assertFalse((self.root / "extra.py").exists())
        self.assertEqual(state["result"]["changed_files"], ["app.py", "extra.py"])
        self.assertIn("+value = 2", state["result"]["diff"])
        said = " ".join(m["content"] for m in self.messages(state, "agent_message"))
        self.assertIn("permission=once", said)  # a one-time allow, never "always"
        self.assertIn("outside read refused=yes", said)
        self.assertIn("server env leaked=no", said)
        self.assertIn("key=[secret]", said)  # the agent got the key, the chat never shows it
        self.assertNotIn(KEY, repr(state))
        self.assertTrue(self.messages(state, "agent_thinking"))
        self.assertTrue(any(c.get("name") == "Edit app.py" for c in self.messages(state, "agent_tool_call")))
        self.assertTrue(any("Allowed once" in r["result"] for r in self.messages(state, "agent_tool_result")))
        self.assertFalse((self.engine.data / "acp-work" / state["id"]).exists())
        self.assertEqual(state["result"]["agent"], {"acp": "Fake agent", "stop_reason": "end_turn", "tool_calls": 1})
        self.engine.apply(state["id"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 2\n")

    def test_needs_both_approvals(self):
        agent_id = self.agent()
        for missing in ("approve_host", "approve_external"):
            request = {"objective": "x", "mode": "acp", "verification": "syntax", "approve_external": True,
                       "approve_host": True, "acp_agent": agent_id}
            del request[missing]
            with self.assertRaises(ValueError):
                self.engine.submit(request)
        with self.assertRaises(ValueError):
            self.engine.submit({"objective": "x", "mode": "agent", "verification": "syntax", "approve_external": True, "approve_host": True})
        with self.assertRaises(ValueError):
            self.run_task(agent_id, verification="docker", approve_execution=True)

    def test_cancel_stops_the_agent(self):
        task = self.run_task(self.agent("hang"))
        deadline = time.monotonic() + 20
        while not any(e["kind"] == "agent_started" for e in self.engine.store.get(task)["events"]):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        time.sleep(0.5)
        self.engine.cancel(task)
        state = self.wait(task, timeout=30)
        self.assertEqual(state["state"], "cancelled")
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_crash_is_reported_plainly(self):
        state = self.wait(self.run_task(self.agent("crash")))
        self.assertEqual(state["state"], "failed")
        self.assertIn("exit code 3", state["result"]["error"])
        self.assertIn("boom", state["result"]["error"])

    def test_missing_program_and_missing_key(self):
        missing = self.engine.acp_agents.create({"name": "Gone", "command": "dikdik-no-such-agent-xyz", "preset": "codex"}, time.time())["id"]
        state = self.wait(self.run_task(missing))
        self.assertEqual(state["state"], "failed")
        self.assertIn("not installed", state["result"]["error"])
        self.assertIn("npm install -g @zed-industries/codex-acp", state["result"]["error"])
        no_key = self.engine.acp_agents.create({"name": "No key", "command": sys.executable, "args": f'"{AGENT}"',
                                                "credentials": ["FAKE_AGENT_KEY=never-saved"]}, time.time())["id"]
        state = self.wait(self.run_task(no_key))
        self.assertIn("Save the key never-saved", state["result"]["error"])

    def test_profile_validation(self):
        with self.assertRaises(ValueError):
            self.engine.acp_agents.create({"name": "x", "command": "x", "credentials": ["bad name=x"]}, time.time())
        agent_id = self.agent()
        with self.assertRaises(ValueError):
            self.engine.acp_agents.update(agent_id, {"command": "-x"})
        preset = self.engine.acp_agents.create({"preset": "gemini-cli"}, time.time())
        self.assertEqual((preset["name"], preset["command"], preset["args"]), ("Gemini CLI", "gemini", "--experimental-acp"))


if __name__ == "__main__":
    unittest.main()
