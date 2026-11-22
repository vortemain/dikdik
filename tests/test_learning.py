"""Phase 5: durable memory, session search and background review."""

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik import logs, memory, review, session_search
from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL
from dikdik.migrations import LATEST, MIGRATIONS, migrate
from dikdik.server_routes import build_routes
from dikdik.skills import SkillIndex
from dikdik.store import CHAT_SCOPE, Store


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.store = Store(self.base / "state.sqlite3")
        self.store.bind_workspace(self.base / "project")
        self.memory = memory.Memory(self.store)


class MemoryTests(Base):
    def test_add_replace_remove(self):
        self.memory.apply("add", "user", "Prefers British spelling.")
        self.memory.apply("add", "project", "Tests run with python -m unittest.", workspace=self.store.workspace)
        self.memory.apply("replace", "user", "Prefers British spelling and short answers.", old_text="British")
        view = self.memory.view(self.store.workspace)
        self.assertEqual([e["content"] for e in view["user"]["entries"]], ["Prefers British spelling and short answers."])
        self.assertEqual(view["project"]["entries"][0]["content"], "Tests run with python -m unittest.")
        self.memory.apply("remove", "user", old_text="British")
        self.assertEqual(self.memory.view(self.store.workspace)["user"]["entries"], [])

    def test_replace_needs_one_matching_entry(self):
        self.memory.apply("add", "user", "Likes tea.")
        self.memory.apply("add", "user", "Likes tea cakes.")
        with self.assertRaisesRegex(ValueError, "2 entries"):
            self.memory.apply("remove", "user", old_text="tea")
        with self.assertRaisesRegex(ValueError, "No user memory entry"):
            self.memory.apply("remove", "user", old_text="coffee")

    def test_overflow_is_refused_with_a_consolidation_error_and_nothing_is_dropped(self):
        filler = "x" * 400
        for n in range(memory.LIMITS["user"] // 410):
            self.memory.apply("add", "user", f"{n} {filler}")
        before = self.memory.entries("user")
        extra = "y" * (memory.LIMITS["user"] - sum(len(e["content"]) for e in before) + 1)
        with self.assertRaises(memory.MemoryFull) as caught:
            self.memory.apply("add", "user", extra)
        message = str(caught.exception)
        self.assertIn("consolidate", message.lower())
        self.assertIn(f"/{memory.LIMITS['user']}", message)
        self.assertEqual(self.memory.entries("user"), before)
        result = self.memory.tool({"action": "add", "target": "user", "content": extra}, workspace=None)
        self.assertIn("error", result)
        self.assertEqual(len(result["entries"]), len(before))  # the agent sees what to merge
        # Replacing an entry with a shorter one frees room, so consolidation works within the cap.
        self.memory.apply("replace", "user", "merged", old_text="0 x")
        self.memory.apply("add", "user", extra)

    def test_project_memory_is_per_project_and_unavailable_in_chats(self):
        self.memory.apply("add", "project", "Uses Postgres.", workspace="/a")
        self.assertEqual(self.memory.entries("project", "/b"), [])
        with self.assertRaisesRegex(ValueError, "project"):
            self.memory.apply("add", "project", "x", workspace=None)
        with self.assertRaisesRegex(ValueError, "project"):
            self.memory.apply("add", "project", "x", workspace=CHAT_SCOPE)

    def test_credentials_are_refused(self):
        logs.register_secret("hunter2-provider-value")
        for text in ("my key is sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz",
                     "password: CorrectHorse99",
                     "API_KEY=abcd1234efgh5678",
                     "github token ghp_" + "a" * 36,
                     "Authorization: Bearer abcdefghijklmnop",
                     "remember hunter2-provider-value",
                     "-----BEGIN RSA " + "PRIVATE KEY-----"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "credential"):
                    self.memory.apply("add", "user", text)
        self.assertEqual(self.memory.entries("user"), [])
        self.memory.apply("add", "user", "Rotates the API key monthly.")  # talking about keys is fine
        self.memory.apply("add", "user", "The token is stored in the vault.")

    def test_snapshot_is_frozen_per_conversation_and_marked_untrusted(self):
        self.memory.apply("add", "user", "Name is Wanjiru.")
        first = self.store.create({"objective": "hello", "mode": "chat"})
        text = self.memory.frozen(first, None)
        self.assertIn("Name is Wanjiru.", text)
        self.assertIn("UNTRUSTED", text)
        self.assertIn("DATA ONLY", text)
        self.memory.apply("add", "user", "Teaches chemistry.")
        self.assertEqual(self.memory.frozen(first, None), text)  # byte-identical for the prompt cache
        second = self.store.create({"objective": "again", "mode": "chat"})
        self.assertIn("Teaches chemistry.", self.memory.frozen(second, None))

    def test_empty_memory_has_no_snapshot(self):
        task = self.store.create({"objective": "hello", "mode": "chat"})
        self.assertEqual(self.memory.frozen(task, None), "")
        self.memory.apply("add", "user", "Later fact.")
        self.assertEqual(self.memory.frozen(task, None), "")  # still frozen for this conversation

    def test_replace_all_for_user_edits(self):
        self.memory.apply("add", "user", "old")
        self.memory.replace_all("user", ["one", "two"], workspace=None)
        self.assertEqual([e["content"] for e in self.memory.entries("user")], ["one", "two"])
        with self.assertRaises(memory.MemoryFull):
            self.memory.replace_all("user", ["z" * (memory.LIMITS["user"] + 1)], workspace=None)
        self.assertEqual([e["content"] for e in self.memory.entries("user")], ["one", "two"])
        self.memory.clear("user")
        self.assertEqual(self.memory.entries("user"), [])


class SessionSearchTests(Base):
    def make(self, objective, workspace, replies=()):
        self.store.workspace = workspace
        task = self.store.create({"objective": objective, "mode": "chat" if workspace == CHAT_SCOPE else "agent"})
        for text in replies:
            self.store.event(task, "agent_message", {"content": text})
        return task

    def test_index_on_write_and_scope_like_the_task_list(self):
        here = str(self.base / "project")
        mine = self.make("Fix the invoice rounding bug", here, ["Rounded invoice totals with Decimal quantize."])
        chat = self.make("Explain invoice VAT in Kenya", CHAT_SCOPE)
        other = self.make("Invoice export for the other client", str(self.base / "other"))
        self.store.workspace = here
        self.store.event(mine, "user_message", {"text": "Also handle negative invoice credits"})
        results = session_search.search(self.store, "invoice")
        found = {r["task"] for r in results}
        self.assertEqual(found, {mine, chat})
        self.assertNotIn(other, found)
        hit = next(r for r in results if r["task"] == mine and r["kind"] == "assistant")
        self.assertIn("Decimal", hit["snippet"])
        self.assertIn(">>", next(r for r in results if r["task"] == mine)["snippet"])  # match marked, no model involved
        self.assertNotIn(mine, {r["task"] for r in session_search.search(self.store, "invoice", exclude=mine)})

    def test_snippets_are_redacted_and_odd_queries_are_safe(self):
        here = str(self.base / "project")
        task = self.make("Deploy notes", here, ["The deploy key was sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz for staging"])
        hits = session_search.search(self.store, "deploy staging")
        self.assertTrue(hits)
        self.assertNotIn("abcdefghijklmnop", json.dumps(hits))
        for query in ('"', "AND OR NOT", "deploy*", "col:deploy", "(", ""):
            session_search.search(self.store, query)  # never an FTS syntax error
        self.store.delete(task)
        self.assertEqual(session_search.search(self.store, "deploy"), [])

    def test_tool_result_is_raw_snippets(self):
        self.make("Thesis chapter on maternal health", str(self.base / "project"))
        result = session_search.tool(self.store, {"query": "maternal"}, exclude=None)
        self.assertTrue(result["untrusted"])
        self.assertEqual(result["results"][0]["objective"], "Thesis chapter on maternal health")
        self.assertIn("error", session_search.tool(self.store, {"query": "  "}, exclude=None))


class MigrationTests(unittest.TestCase):
    def test_v3_backfills_existing_conversations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite3"
            migrate(path, [m for m in MIGRATIONS if m[0] < 3])
            db = sqlite3.connect(path)
            db.execute("INSERT INTO tasks(id,created,state,request,workspace) VALUES('t1',1,'completed',?, 'w')",
                       (json.dumps({"objective": "Plan the harvest schedule"}),))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES('t1',2,'agent_message',?)",
                       (json.dumps({"content": "Harvest maize in August."}),))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES('t1',3,'user_message',?)",
                       (json.dumps({"text": "What about beans?"}),))
            db.commit()
            db.close()
            report = migrate(path)
            self.assertEqual(report["to"], LATEST)
            self.assertIn("durable memory and session search", report["applied"])
            self.assertTrue(report["backup"])
            store = Store(path)
            store.workspace = "w"
            kinds = {r["kind"] for r in session_search.search(store, "harvest")}
            self.assertEqual(kinds, {"objective", "assistant"})
            self.assertEqual(session_search.search(store, "beans")[0]["kind"], "user")
            with closing(sqlite3.connect(path)) as check:
                tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master")}
            self.assertTrue({"memory_entries", "skill_proposals", "session_index"} <= tables)


def _events(*items):
    return [{"kind": kind, "data": data} for kind, data in items]


class ReviewTriggerTests(unittest.TestCase):
    def test_simple_turn_has_no_trigger(self):
        events = _events(("queued", {"objective": "hi"}), ("agent_message", {"content": "Hello"}))
        self.assertEqual(review.triggers(events, "completed"), [])

    def test_recovered_error(self):
        events = _events(("queued", {"objective": "fix"}),
                         ("agent_tool", {"phase": "end", "name": "run_command", "failed": True}),
                         ("agent_recovery", {"tool": "run_command", "action": "change_approach"}),
                         ("agent_tool", {"phase": "end", "name": "run_command", "failed": False}))
        self.assertEqual(review.triggers(events, "completed"), ["recovered_error"])
        self.assertEqual(review.triggers(events, "failed"), [])

    def test_user_correction_in_this_turn_only(self):
        events = _events(("queued", {"objective": "write it"}), ("completed", {}),
                         ("user_message", {"text": "No, I meant the APA 7th edition style"}),
                         ("agent_message", {"content": "Fixed."}))
        self.assertEqual(review.triggers(events, "completed"), ["user_correction"])
        events += _events(("completed", {}), ("user_message", {"text": "Thanks, now add a table"}))
        self.assertEqual(review.triggers(events, "completed"), [])

    def test_multi_step_workflow(self):
        tools = [("agent_tool", {"phase": "end", "name": name, "failed": False})
                 for name in ("read_document", "statistics", "statistics", "create_document", "web_search")]
        self.assertEqual(review.triggers(_events(("queued", {"objective": "analyse"}), *tools), "completed"), ["workflow"])


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


class ReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.skills = SkillIndex(self.store, self.base / "skills", self.base / "project" / ".dikdik" / "skills", self.base / "installed")
        self.task = self.store.create({"objective": "Clean the survey data", "mode": "agent"})
        self.store.event(self.task, "agent_tool", {"phase": "end", "name": "statistics", "failed": True,
                                                   "observation": {"error": "column age_yrs not found"}})
        self.store.event(self.task, "agent_tool", {"phase": "end", "name": "statistics", "failed": False})
        self.store.event(self.task, "agent_message", {"content": "Done: the column is called age."})

    def test_review_applies_memory_and_stores_a_skill_proposal_only(self):
        reply = json.dumps({"memory": [{"action": "add", "target": "project", "content": "Survey age column is `age`, not age_yrs."},
                                       {"action": "add", "target": "user", "content": "token=abcd1234efgh5678ijkl"}],
                            "skill": {"name": "survey-cleaning", "description": "Check column names first",
                                      "content": "List the columns before computing statistics."}})
        model = Scripted("Here you go:\n```json\n" + reply + "\n```")
        report = review.run(self.store, self.memory, self.task, model, workspace=self.store.workspace, triggers=["recovered_error"])
        self.assertEqual(len(model.calls), 1)
        prompt = json.dumps(model.calls[0])
        self.assertIn("age_yrs", prompt)
        self.assertIn("untrusted", prompt.lower())
        self.assertEqual(report["memory_applied"], 1)
        self.assertEqual(len(report["memory_refused"]), 1)  # the credential never lands
        self.assertEqual(self.memory.entries("project", self.store.workspace)[0]["source"], "review")
        proposals = review.proposals(self.store)
        self.assertEqual([p["name"] for p in proposals], ["survey-cleaning"])
        self.assertEqual(proposals[0]["status"], "pending")
        self.assertIsNone(self.skills.find("survey-cleaning"))  # proposals are never enabled on their own
        review.decide(self.store, self.skills, proposals[0]["id"], "approve")
        self.assertTrue(self.skills.find("survey-cleaning"))
        self.assertEqual(review.proposals(self.store)[0]["status"], "approved")

    def test_overflowing_memory_update_is_reported_not_forced(self):
        for n in range(memory.LIMITS["user"] // 210):
            self.memory.apply("add", "user", f"{n} " + "u" * 200)
        model = Scripted(json.dumps({"memory": [{"action": "add", "target": "user", "content": "v" * 500}], "skill": None}))
        report = review.run(self.store, self.memory, self.task, model, workspace=None, triggers=["workflow"])
        self.assertEqual(report["memory_applied"], 0)
        self.assertIn("consolidate", report["memory_refused"][0].lower())

    def test_bad_model_output_changes_nothing(self):
        report = review.run(self.store, self.memory, self.task, Scripted("I have no suggestions."), workspace=None, triggers=["workflow"])
        self.assertEqual(report["memory_applied"], 0)
        self.assertEqual(review.proposals(self.store), [])

    def test_unused_user_skills_go_stale_then_archived_without_a_model(self):
        self.skills.create({"name": "old-habit", "scope": "personal", "content": "Do the thing."})
        self.skills.create({"name": "fresh", "scope": "personal", "content": "Do it."})
        now = time.time()
        review.sweep_skills(self.store, self.skills, now=now - 15 * 86400)  # first seen 15 days ago
        self.skills.load("fresh")  # use resets the clock
        review.sweep_skills(self.store, self.skills, now=now)
        rows = {r["id"]: r for r in self.skills.enumerate_skills()}
        self.assertEqual(rows["old-habit"].get("lifecycle"), "stale")
        self.assertTrue(rows["old-habit"]["enabled"])
        self.assertNotIn("lifecycle", rows["fresh"])
        review.sweep_skills(self.store, self.skills, now=now + 16 * 86400)
        rows = {r["id"]: r for r in self.skills.enumerate_skills()}
        self.assertEqual(rows["old-habit"]["lifecycle"], "archived")
        self.assertFalse(rows["old-habit"]["enabled"])
        self.skills.set_enabled("old-habit", True)  # the user brings it back: a fresh start
        self.assertNotIn("lifecycle", self.skills.find("old-habit"))


class EngineLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        (base / "p").mkdir()
        self.engine = Engine(base / "p", base / "data")
        self.addCleanup(self.engine.close)

    def wait(self, task):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            data = self.engine.store.get(task)
            if data["state"] in TERMINAL and not self.engine.busy(task):
                return data
            time.sleep(0.02)
        self.fail("did not finish")

    def chat(self, model, objective, task=None):
        request = {"objective": objective, "mode": "chat", "verification": "syntax", "approve_external": True}
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            if task is None:
                task = self.engine.submit(request)
            else:
                self.engine.send(task, request)
            self.wait(task)
        return task

    def test_memory_tool_writes_show_up_in_the_next_conversation_only(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("", [ToolCall("m1", "memory", {"action": "add", "target": "user", "content": "Prefers metric units."})]),
            ModelResponse("Noted.", []),
            ModelResponse("Still here.", []),
            ModelResponse("Hello again.", []),
        ]
        first = self.chat(model, "Remember that I prefer metric units")
        messages, tools = model.respond.call_args_list[0].args
        self.assertTrue({"memory", "session_search"} <= {t["function"]["name"] for t in tools})
        self.assertNotIn("Prefers metric units", json.dumps(messages))
        self.chat(model, "and now?", first)
        self.assertNotIn("Prefers metric units", json.dumps(model.respond.call_args_list[2].args[0]))
        self.chat(model, "Hi")
        later = json.dumps(model.respond.call_args_list[3].args[0])
        self.assertIn("Prefers metric units", later)
        self.assertIn("UNTRUSTED", later)

    def test_memory_setting_off_removes_tools_and_snapshot(self):
        self.engine.memory.apply("add", "user", "Secret-free fact.")
        self.engine.update_app_settings({"memory": False})
        model = Mock()
        model.respond.return_value = ModelResponse("Hi", [])
        self.chat(model, "hello")
        messages, tools = model.respond.call_args.args
        self.assertFalse({"memory", "session_search"} & {t["function"]["name"] for t in tools})
        self.assertNotIn("Secret-free fact", json.dumps(messages))

    def test_review_runs_in_the_background_and_respects_the_setting(self):
        scripted = Scripted(json.dumps({"memory": [{"action": "add", "target": "user", "content": "Writes in APA style."}], "skill": None}))
        model = Mock()
        model.respond.side_effect = [ModelResponse("Done.", []), ModelResponse("Fixed to APA.", [])]
        with patch.object(Engine, "_review_model", return_value=scripted):
            task = self.chat(model, "Format my references")
            self.chat(model, "No, that's wrong: use APA 7", task)
            self.engine.reviewer.submit(lambda: None).result(timeout=10)  # drain the review queue
        self.assertEqual(len(scripted.calls), 1)
        self.assertEqual([e["content"] for e in self.engine.memory.entries("user")], ["Writes in APA style."])
        kinds = [e for e in self.engine.store.get(task)["events"] if e["kind"] == "learning_review"]
        self.assertEqual(kinds[-1]["data"]["triggers"], ["user_correction"])
        self.engine.update_app_settings({"background_review": False})
        again = Scripted("{}")
        model.respond.side_effect = [ModelResponse("Ok.", [])]
        with patch.object(Engine, "_review_model", return_value=again):
            self.chat(model, "Wrong again, use Harvard", task)
            self.engine.reviewer.submit(lambda: None).result(timeout=10)
        self.assertEqual(again.calls, [])

    def test_review_is_skipped_without_a_fast_model(self):
        task = self.engine.store.create({"objective": "x", "mode": "chat", "approve_external": True})
        self.assertIsNone(self.engine._review_model({"approve_external": True}))
        self.assertIsNone(self.engine._review_model({"approve_external": False}))
        del task

    def test_http_routes_view_edit_and_clear(self):
        routes = build_routes(self.engine)
        status, body = routes("POST", "/api/memory", {"action": "add", "target": "project", "content": "Uses Django 5."})
        self.assertEqual(status, 200)
        status, body = routes("GET", "/api/memory", None)
        self.assertEqual(body["project"]["entries"][0]["content"], "Uses Django 5.")
        self.assertEqual(body["project"]["limit"], memory.LIMITS["project"])
        status, body = routes("PATCH", "/api/memory/user", {"entries": ["Likes tables."]})
        self.assertEqual((status, body["user"]["entries"][0]["content"]), (200, "Likes tables."))
        status, body = routes("POST", "/api/memory", {"action": "add", "target": "user", "content": "password: hunter2hunter2"})
        self.assertEqual(status, 400)
        self.assertNotIn("hunter2", json.dumps(body))
        status, body = routes("DELETE", "/api/memory/user", None)
        self.assertEqual((status, body["user"]["entries"]), (200, []))
        self.assertEqual(routes("DELETE", "/api/memory/nope", None)[0], 404)
        self.assertEqual(routes("GET", "/api/skill-proposals", None), (200, {"proposals": []}))


if __name__ == "__main__":
    unittest.main()
