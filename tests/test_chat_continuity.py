"""Chat is a general assistant that researches, and conversations keep their files across pauses and turns."""

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik import documents, research
from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL

DDG_LITE = """<table><tr><td>1.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.co.ke%2F&amp;rut=abc" class='result-link'>Example Developers &#x2014; Build</a>
</td></tr><tr><td></td><td class='result-snippet'>
<b>Example</b> builds custom software and AI.</td></tr>
<a rel="nofollow" href="https://duckduckgo.com/y.js?ad=1" class='result-link'>An advert</a>
</table>"""

PROPOSAL = "# Proposal\n## Objectives\n1. Assess staff knowledge.\n2. Assess blood availability.\n"


class KeylessSearchTests(unittest.TestCase):
    def test_duckduckgo_lite_results_are_unwrapped_and_ads_dropped(self):
        results = research.parse_duckduckgo_lite(DDG_LITE)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.co.ke/")
        self.assertEqual(results[0]["title"], "Example Developers — Build")
        self.assertEqual(results[0]["content"], "Example builds custom software and AI.")

    def test_search_works_without_a_key_and_falls_back_to_wikipedia(self):
        secrets = Mock()
        secrets.list.return_value = []
        self.assertTrue(research.configured(secrets))
        self.assertFalse(research.tavily_connected(secrets))
        wiki = json.dumps({"query": {"search": [{"title": "Malaria vaccine", "snippet": "<span>R21</span> vaccine"}]}})

        def web(url, headers=None):
            return DDG_LITE if "duckduckgo" in url else wiki
        with patch("dikdik.research._get", side_effect=web):
            found = research.search(secrets, {"query": "example developers"})
        self.assertEqual(found["provider"], "DuckDuckGo")
        self.assertEqual(found["results"][0]["url"], "https://example.co.ke/")

        def blocked(url, headers=None):
            if "duckduckgo" in url:
                raise OSError("blocked")
            return wiki
        with patch("dikdik.research._get", side_effect=blocked):
            found = research.search(secrets, {"query": "malaria vaccine"})
        self.assertEqual(found["provider"], "Wikipedia")
        self.assertEqual(found["results"][0]["url"], "https://en.wikipedia.org/wiki/Malaria_vaccine")
        with patch("dikdik.research._get", side_effect=OSError("offline")):
            self.assertEqual(research.search(secrets, {"query": "x"})["status"], "failed")
        with self.assertRaises(ValueError):
            research.search(secrets, {"query": ""})


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        (self.base / "p").mkdir()
        (self.base / "p" / "app.py").write_text("x = 1\n")
        self.engine = Engine(self.base / "p", self.base / "data")
        self.addCleanup(self.engine.close)

    def wait(self, task):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            data = self.engine.store.get(task)
            if data["state"] in TERMINAL and not self.engine.busy(task):
                return data
            time.sleep(0.02)
        self.fail("did not finish")

    def attach(self, name, data):
        return self.engine.attachments.add({"name": name, "type": "application/octet-stream",
                                            "data": base64.b64encode(data).decode()})["id"]

    def test_chat_always_has_web_search_and_no_project_rules(self):
        model = Mock()
        model.respond.return_value = ModelResponse("Hi! How can I help?", [])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            self.wait(self.engine.submit({"objective": "hi", "mode": "chat", "verification": "syntax", "approve_external": True}))
        messages, tools = model.respond.call_args.args
        names = {tool["function"]["name"] for tool in tools}
        self.assertTrue({"web_search", "web_fetch", "read_document", "read_image", "statistics"} <= names)
        system = messages[0]["content"]
        self.assertIn("RESEARCH BEFORE YOU ANSWER", system)
        self.assertNotIn("selected project folder", system)
        self.assertNotIn("Task ownership", system)

    def test_chat_replies_stream_to_the_screen_as_they_are_written(self):
        seen = []
        model = Mock()
        model.respond.return_value = ModelResponse("A long lesson.", [])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model) as factory:
            task = self.engine.submit({"objective": "teach me", "mode": "chat", "verification": "syntax", "approve_external": True})
            self.wait(task)
        on_text = factory.call_args.args[2] if len(factory.call_args.args) > 2 else factory.call_args.kwargs.get("on_text")
        self.assertTrue(callable(on_text), "chat must pass a streaming callback to the model")
        on_text("A long le")
        seen.append(self.engine.live.get(task))
        on_text("")
        self.assertEqual(seen, ["A long le"])
        self.assertNotIn(task, self.engine.live)
        long = "x" * 40000
        on_text(long)
        self.assertEqual(self.engine.live[task], long)  # the whole reply so far, not only its last 16 KB
        on_text("")

    def test_attachments_from_earlier_turns_stay_in_the_conversation(self):
        proposal, _ = documents.build_docx(PROPOSAL, root=self.base)
        ident = self.attach("Proposal.docx", proposal)
        model = Mock()
        model.respond.return_value = ModelResponse("Read it.", [])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Summarise my proposal", "mode": "chat", "verification": "syntax",
                                       "approve_external": True, "attachments": [ident]})
            self.wait(task)
            self.engine.send(task, {"objective": "Now list its objectives", "mode": "chat", "verification": "syntax",
                                    "approve_external": True})
            self.wait(task)
        follow_up = model.respond.call_args.args[0]
        text = json.dumps(follow_up)
        self.assertIn("Files the user attached", text)
        self.assertIn("attachments/Proposal.docx", text)
        self.assertEqual(sum(1 for m in follow_up if m.get("content") == "Now list its objectives"), 1)  # not duplicated

    def test_a_paused_chat_resumes_as_a_chat_from_its_saved_state(self):
        entered, release = threading.Event(), threading.Event()
        calls = {"n": 0}

        def respond(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                release.wait(5)
                return ModelResponse("Planning.", [ToolCall("s1", "task_tracker", {"tasks": [{"title": "research", "status": "in_progress"}]})])
            return ModelResponse("Here is the finished answer.", [])
        model = Mock()
        model.respond.side_effect = respond
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Research R21", "mode": "chat", "verification": "syntax", "approve_external": True})
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.engine.interrupt(task))
            release.set()
            self.assertEqual(self.wait(task)["state"], "paused")
            self.assertTrue(self.engine._transcript_path(task).is_file())
            with patch.object(self.engine, "_agent_workflow", side_effect=AssertionError("resumed as a code agent")):
                self.engine.resume(task, {})
                result = self.wait(task)
        self.assertEqual(result["state"], "completed")
        self.assertIn("chat_files", result["result"])  # the chat workflow finished the turn

    def test_code_mode_keeps_attachments_readable_by_path(self):
        ident = self.attach("data.csv", b"age,score\n30,4\n40,5\n")
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("", [ToolCall("1", "statistics", {"path": "attachments/data.csv", "analysis": "descriptives", "columns": ["score"]})]),
            ModelResponse("Mean score is 4.5.", [])]
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Analyse the attached data", "mode": "agent", "verification": "syntax",
                                       "approve_external": True, "attachments": [ident]})
            self.wait(task)
        first = json.dumps(model.respond.call_args_list[0].args[0])
        self.assertIn("attachments/data.csv", first)
        observed = json.dumps(model.respond.call_args_list[-1].args[0])
        self.assertIn("4.5", observed)
        self.assertNotIn("does not exist", observed)


if __name__ == "__main__":
    unittest.main()
