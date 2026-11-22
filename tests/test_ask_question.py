"""Clarifying questions in every interactive task, with credentials in answers sealed, never shown to the model."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL

# Assembled at runtime so this file never contains a literal credential itself.
OPENAI = "sk-" + "proj-" + "Q7rT2xLm9KpW4vZn8YcB3hJd6FgS1aEu5NqR0oXi"
PASSWORD = "Correct" + "Horse-Battery9"


class Script:
    def __init__(self, replies):
        self.replies, self.seen, self.tools = list(replies), [], []

    def respond(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        self.tools.append([tool["function"]["name"] for tool in tools])
        return self.replies.pop(0)


def ask(i, **arguments):
    return ModelResponse("", [ToolCall(f"q{i}", "ask_question", arguments)])


class AskQuestionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        (base / "project").mkdir()
        (base / "project" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.engine = Engine(base / "project", base / "data")
        self.addCleanup(self.engine.close)

    def run_task(self, model, request, answer=None):
        """Submit, answer the first question with `answer` (a dict for answer_question) and wait."""
        holder = {}

        def answer_it():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                asked = [e["data"] for e in self.engine.store.get(holder["task"])["events"] if e["kind"] == "question_requested"]
                if asked:
                    holder["asked"] = asked[0]
                    self.engine.answer_question(holder["task"], {"id": asked[0]["id"], **answer})
                    return
                time.sleep(0.02)

        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            holder["task"] = task = self.engine.submit({"approve_external": True, "verification": "syntax", **request})
            worker = threading.Thread(target=answer_it)
            if answer is not None:
                worker.start()
            deadline = time.monotonic() + 30
            while not (self.engine.store.get(task)["state"] in TERMINAL and not self.engine.busy(task)):
                self.assertLess(time.monotonic(), deadline, "task did not finish")
                time.sleep(0.02)
            if answer is not None:
                worker.join(5)
        return self.engine.store.get(task), holder.get("asked")

    def tool_results(self, model):
        return [m for m in model.seen[-1] if m.get("role") == "tool"]

    def test_a_chat_can_ask_a_clarifying_question_and_use_the_answer(self):
        model = Script([ask(1, question="Which citation style should the reference list use?", choices=["APA 7", "Harvard"]),
                        ModelResponse("Here is the reference list in APA 7.", [])])
        data, asked = self.run_task(model, {"objective": "Format my reference list", "mode": "chat"}, {"answer": "APA 7"})
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertIn("ask_question", model.tools[0])
        self.assertEqual(asked["choices"], ["APA 7", "Harvard"])
        self.assertIn("APA 7", json.dumps(self.tool_results(model)))

    def test_an_agent_task_can_ask_without_command_approval(self):
        model = Script([ask(1, question="Should the greeting be formal or casual?"), ModelResponse("Noted: casual.", [])])
        data, _ = self.run_task(model, {"objective": "Explain how the greeting should read", "mode": "agent"}, {"answer": "casual"})
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertIn("ask_question", model.tools[0])
        self.assertNotIn("request_secret", model.tools[0])  # secrets for commands still need approved commands

    def test_unattended_runs_are_never_asked(self):
        model = Script([ModelResponse("Done.", [])])
        data, _ = self.run_task(model, {"objective": "Summarise the project", "mode": "chat", "questions": False})
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertNotIn("ask_question", model.tools[0])

    def test_a_credential_pasted_into_an_answer_is_sealed_and_never_shown(self):
        model = Script([ask(1, question="Which API key should the script use?"), ModelResponse("I will read it from the environment.", [])])
        data, _ = self.run_task(model, {"objective": "Write a script that calls the API", "mode": "chat"},
                                {"answer": f"Use this one: {OPENAI}"})
        self.assertEqual(data["state"], "completed", data.get("result"))
        seen_by_model = json.dumps(model.seen)
        self.assertNotIn(OPENAI, seen_by_model)
        result = json.loads(self.tool_results(model)[0]["content"])
        name = result["secrets"][0]
        self.assertIn("$" + name, result["answer"])
        self.assertIn("never shown to you", result["note"])
        self.assertNotIn(OPENAI, json.dumps(data["events"]))
        self.assertEqual(self.engine.models.secrets.reveal(name).decode(), OPENAI)
        answered = next(e["data"] for e in data["events"] if e["kind"] == "question_answered")
        self.assertEqual(answered["secrets"], [name])

    def test_a_sensitive_question_seals_any_answer(self):
        model = Script([ask(1, question="What is the database password?", sensitive=True, name="DB_PASSWORD"),
                        ModelResponse("Thanks, the app will read DB_PASSWORD.", [])])
        data, asked = self.run_task(model, {"objective": "Connect the app to the database", "mode": "agent",
                                            "approve_execution": True}, {"answer": PASSWORD})
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertTrue(asked["sensitive"])
        self.assertNotIn(PASSWORD, json.dumps(model.seen))
        self.assertNotIn(PASSWORD, json.dumps(data["events"]))
        result = json.loads(self.tool_results(model)[0]["content"])
        self.assertEqual((result["answer"], result["secrets"]), ("$DB_PASSWORD", ["DB_PASSWORD"]))
        self.assertEqual(self.engine.models.secrets.reveal("DB_PASSWORD").decode(), PASSWORD)

    def test_the_user_can_mark_an_answer_secret(self):
        model = Script([ask(1, question="How should I sign in to the staging server?"), ModelResponse("Stored.", [])])
        data, _ = self.run_task(model, {"objective": "Deploy to staging", "mode": "chat"}, {"answer": PASSWORD, "secret": True})
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertNotIn(PASSWORD, json.dumps(model.seen))
        self.assertNotIn(PASSWORD, json.dumps(data["events"]))

    def test_models_are_told_to_clarify_before_ambiguous_deliverables(self):
        model = Script([ModelResponse("Hello.", [])])
        self.run_task(model, {"objective": "Write me a report", "mode": "chat"})
        system = model.seen[0][0]["content"]
        self.assertIn("ask_question", system)
        self.assertIn("sensitive", system)


if __name__ == "__main__":
    unittest.main()
