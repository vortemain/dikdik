import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.control import Attachments, assess, blocked_by, load_hooks, load_subagents, needs_approval
from dikdik.engine import Engine, TERMINAL

ENV = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}
AGENT = {"mode": "agent", "verification": "syntax", "approve_external": True}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def answer(text):
    return {"choices": [{"message": {"content": text}}]}


class Script:
    """Scripted agent model; `hook(n, messages)` may act before reply n is returned."""

    def __init__(self, replies, hook=None):
        self.replies, self.hook, self.seen = list(replies), hook, []

    def respond(self, messages, tools):
        self.seen.append((messages, [t["function"]["name"] for t in tools]))
        if self.hook:
            self.hook(len(self.seen), messages)
        return self.replies.pop(0)


def edit(i, before="a - b", after="a + b"):
    return ModelResponse("", [ToolCall(f"e{i}", "edit_file", {"path": "calc.py", "before": before, "after": after})])


def call(i, name, **args):
    return ModelResponse("", [ToolCall(f"c{i}", name, args)])


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        self.data = base / "data"
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def idle(self, task, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.engine.lock:
                busy = bool(self.engine.active) or bool(self.engine.pending)
            data = self.engine.store.get(task)
            if not busy and data["state"] in TERMINAL:
                return data
            time.sleep(0.01)
        self.fail("task did not finish")

    def kinds(self, data, kind):
        return [e["data"] for e in data["events"] if e["kind"] == kind]

    def wait_event(self, task, kind, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.kinds(self.engine.store.get(task), kind)
            if found:
                return found[-1]
            time.sleep(0.02)
        self.fail(f"no {kind} event")

    # --- pause and exact resume ---------------------------------------------------
    def test_pause_then_resume_continues_the_same_transcript(self):
        holder = {}

        def pause_on_second(n, messages):
            if n == 2:
                self.engine.pause(holder["task"])

        first = Script([edit(1), call(2, "read_file", path="calc.py")], pause_on_second)
        second = Script([call(3, "run_tests"), call(4, "finish")])
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[first, second]):
            holder["task"] = task = self.engine.submit({"objective": "Fix add", **AGENT})
            data = self.idle(task)
            self.assertEqual(data["state"], "paused", data["result"])
            self.assertIn("+    return a + b", data["result"]["diff"])
            self.assertTrue((self.data / "transcripts" / f"{task}.json").is_file())
            self.assertTrue(self.engine.resume(task, AGENT))
            data = self.idle(task)
        self.assertEqual(data["state"], "completed", data["result"])
        resumed = second.seen[0][0]
        self.assertTrue(any(m.get("tool_call_id") == "e1" for m in resumed))  # earlier tool results kept
        self.assertIn("resumed", resumed[-1]["content"])
        self.assertEqual(self.kinds(data, "resumed")[0]["from_state"], "paused")
        with self.assertRaisesRegex(ValueError, "Only a paused"):
            self.engine.resume(task, AGENT)

    def test_interrupt_running_tool_records_error_and_resumes(self):
        entered = threading.Event()
        secret = "INTERRUPT_SECRET_MUST_NOT_APPEAR"

        def shell(_workdir, _files, _command, cancel, **_kwargs):
            entered.set()
            self.assertTrue(cancel.wait(3), "interrupt did not reach the active tool")
            return {"status": "cancelled", "exit_code": None, "output": secret, "changed": {}}

        first = Script([call(1, "run_command", command="sleep 30")])
        resumed = Script([ModelResponse("Recovered after the interruption.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[first, resumed]), patch("dikdik.engine.run_shell", side_effect=shell):
            task = self.engine.submit({"objective": "Run slowly", "mode": "agent", "verification": "docker",
                                       "approve_external": True, "approve_execution": True})
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.assertTrue(self.engine.interrupt(task))
            data = self.idle(task)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(data["state"], "paused")
            ended = [e["data"] for e in data["events"] if e["kind"] == "agent_tool" and e["data"].get("phase") == "end"][-1]
            self.assertEqual((ended["id"], ended["failed"], ended["observation"]),
                             ("c1", True, {"error": "Interrupted by user", "status": "cancelled", "exit_code": None,
                                            "output": "", "changed_files": [], "rejected_changes": []}))
            self.assertNotIn(secret, json.dumps(data))
            self.assertTrue(self.engine.resume(task, AGENT))
            data = self.idle(task)
        self.assertEqual(data["state"], "completed")
        self.assertIn("Interrupted by user", resumed.seen[0][0][-2]["content"])
        self.assertFalse(self.engine.interrupt(task))

    def test_interrupt_after_the_turn_finished_is_ignored(self):
        # The worker commits the terminal state before it releases the task; a Stop in
        # that window must not leave a flag that turns a later real cancel into a pause.
        task = self.engine.store.create({"objective": "x", **AGENT})
        self.engine.store.state(task, "completed", {})
        flag = threading.Event()
        self.engine.active[task] = flag
        try:
            self.assertFalse(self.engine.interrupt(task))
            self.assertFalse(flag.is_set())
            self.assertNotIn(task, self.engine.interrupting)
        finally:
            self.engine.active.pop(task, None)

    def test_budget_exhaustion_can_be_resumed_with_a_fresh_budget(self):
        profile = self.engine.agents.create({"name": "Tiny", "max_model_calls": 1})
        first = Script([edit(1)])
        second = Script([call(2, "run_tests"), call(3, "finish")])
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[first, second]):
            task = self.engine.submit({"objective": "Fix add", **AGENT, "agent_profile": profile["id"]})
            data = self.idle(task)
            self.assertEqual((data["state"], data["result"]["error"]), ("failed", "Model or token budget exhausted"))
            self.engine.resume(task, {**AGENT})
            self.assertEqual(self.idle(task)["state"], "completed")

    def test_resume_needs_saved_state(self):
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script([ModelResponse("Hi", [])])):
            task = self.engine.submit({"objective": "Say hi", **AGENT})
            self.idle(task)
        (self.data / "transcripts" / f"{task}.json").unlink()
        with self.engine.store.connect() as db:
            db.execute("UPDATE tasks SET state='failed' WHERE id=?", (task,))
        with self.assertRaisesRegex(ValueError, "no saved agent state"):
            self.engine.resume(task, AGENT)

    def test_stuck_agent_is_stopped(self):
        replies = [call(i, "read_file", path="calc.py") for i in range(5)]
        result = run_agent({"calc.py": "x = 1\n"}, "Look", Script(replies), None, lambda k, d: None, threading.Event())
        self.assertEqual((result.state, result.error), ("failed", "Agent is stuck repeating the same action"))

    def test_stuck_rules(self):
        from dikdik.agent import stuck_loop
        self.assertTrue(stuck_loop(list("xaaaa")))
        self.assertFalse(stuck_loop(list("aaa")))
        self.assertTrue(stuck_loop(list("ababab")))
        self.assertFalse(stuck_loop(list("ababa")))
        self.assertFalse(stuck_loop(list("abacab")))  # re-reading a file now and then is not a loop

    # --- approvals and hooks ---------------------------------------------------------
    def test_risk_rules(self):
        cases = {("run_command", "rm -rf build"): "high", ("run_command", "curl https://x"): "high",
                 ("run_command", "pip install requests"): "high", ("run_command", "python -m pytest"): "medium"}
        for (name, command), level in cases.items():
            self.assertEqual(assess(ToolCall("x", name, {"command": command}))[0], level, command)
        self.assertEqual(assess(ToolCall("x", "read_file", {"path": "a"}))[0], "low")
        self.assertEqual(assess(ToolCall("x", "edit_file", {"path": "package.json"}))[0], "medium")
        self.assertEqual(assess(ToolCall("x", "mcp__gh__issues", {}))[0], "medium")
        table = [(p, r, needs_approval(p, r)) for p in ("never", "risky", "always") for r in ("low", "medium", "high")]
        self.assertEqual([t[2] for t in table], [False, False, False, False, False, True, False, True, True])
        self.assertTrue(needs_approval("always", "low", "edit_file"))  # every action that is not read-only
        self.assertFalse(needs_approval("always", "low", "read_file"))
        self.assertFalse(needs_approval("risky", "low", "edit_file"))

    def test_reject_then_allow_in_the_conversation(self):
        decisions = iter([False, True])

        def decide():
            seen = set()
            for _ in range(2):
                while True:
                    asked = [e for e in self.kinds(self.engine.store.get(holder["task"]), "confirmation_requested") if e["id"] not in seen]
                    if asked:
                        break
                    time.sleep(0.02)
                seen.add(asked[0]["id"])
                self.engine.confirm(holder["task"], {"id": asked[0]["id"], "accept": next(decisions)})

        holder = {}
        model = Script([call(1, "run_command", command="rm -rf build"), call(2, "run_command", command="ls"), ModelResponse("Done.", [])])
        ran = []
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), \
                patch("dikdik.engine.run_shell", side_effect=lambda *a, **k: ran.append(a[2]) or {"status": "passed", "exit_code": 0, "output": "", "changed": {}}):
            holder["task"] = task = self.engine.submit({"objective": "Clean", "mode": "agent", "verification": "docker",
                                                        "approve_external": True, "approve_execution": True, "confirmation": "always"})
            worker = threading.Thread(target=decide)
            worker.start()
            data = self.idle(task)
            worker.join(5)
        self.assertEqual(ran, ["ls"])  # the rejected command never ran
        resolved = self.kinds(data, "confirmation_resolved")
        self.assertEqual([r["accepted"] for r in resolved], [False, True])
        asked = self.kinds(data, "confirmation_requested")
        self.assertEqual((asked[0]["risk"], asked[1]["risk"]), ("high", "medium"))
        rejected = next(e for e in self.kinds(data, "agent_tool") if e["phase"] == "end" and e["id"] == "c1")
        self.assertIn("rejected", rejected["observation"]["error"])
        with self.assertRaises(KeyError):
            self.engine.confirm(task, {"id": "nope", "accept": True})

    def test_project_hooks_block_tools(self):
        (self.root / ".dikdik").mkdir()
        (self.root / ".dikdik" / "hooks.json").write_text(json.dumps({"block": [{"tool": "edit_file", "pattern": "calc\\.py", "message": "calc.py is frozen"}]}))
        rules = load_hooks(self.root)
        self.assertEqual(blocked_by(rules, ToolCall("x", "edit_file", {"path": "calc.py", "before": "a", "after": "b"})), "calc.py is frozen")
        self.assertIsNone(blocked_by(rules, ToolCall("x", "read_file", {"path": "calc.py"})))
        model = Script([edit(1), ModelResponse("I could not edit it.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.idle(self.engine.submit({"objective": "Fix", **AGENT}))
        self.assertEqual(self.kinds(data, "hook_blocked")[0]["message"], "calc.py is frozen")
        self.assertEqual(data["result"]["diff"], "")
        (self.root / ".dikdik" / "hooks.json").write_text("{not json")
        self.assertIn("invalid", blocked_by(load_hooks(self.root), ToolCall("x", "read_file", {"path": "a"})))

    # --- side questions, condensing, goals ---------------------------------------------------
    def test_side_question_adds_nothing_to_the_conversation(self):
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script([ModelResponse("Hi", [])])):
            task = self.idle(self.engine.submit({"objective": "Say hi", **AGENT}))["id"]
        before = len(self.engine.store.get(task)["events"])
        with patch("dikdik.control.chat_completion", return_value=answer("It greeted you.")) as model:
            self.assertEqual(self.engine.ask(task, {"question": "What happened?", "approve_external": True}), {"answer": "It greeted you."})
        self.assertEqual(model.call_args.args[0][-1]["content"], "What happened?")
        self.assertEqual(len(self.engine.store.get(task)["events"]), before)
        with self.assertRaisesRegex(ValueError, "Approve"):
            self.engine.ask(task, {"question": "x"})

    def test_condense_replaces_older_turns_with_a_summary(self):
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[Script([ModelResponse("One", [])]), Script([ModelResponse("Two", [])]), Script([ModelResponse("Three", [])])]):
            task = self.idle(self.engine.submit({"objective": "First", **AGENT}))["id"]
            self.engine.send(task, {"objective": "Second", **AGENT})
            self.idle(task)
            with patch("dikdik.control.chat_completion", return_value=answer("They said one and two.")):
                self.assertEqual(self.engine.condense(task)["summary"], "They said one and two.")
            history = self.engine._history(task, include_last=True)
        self.assertTrue(history[0]["content"].startswith("[Summary of the earlier conversation]"))
        self.assertEqual(len(history), 1)

    def test_goal_loop_runs_until_the_judge_says_done(self):
        verdicts = iter([answer('{"achieved": false, "reason": "add still subtracts", "next": "fix add"}'),
                         answer('{"achieved": true, "reason": "add now adds"}')])
        models = [Script([ModelResponse("Looking.", [])]), Script([edit(1), call(2, "run_tests"), call(3, "finish")])]
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=models), \
                patch("dikdik.control.chat_completion", side_effect=lambda *a, **k: next(verdicts)):
            task = self.engine.submit({"objective": "Make add add", **AGENT, "goal_max_turns": 3})
            data = self.idle(task)
        statuses = [(g["status"], g["turns"]) for g in self.kinds(data, "goal_status")]
        self.assertEqual(statuses, [("active", 0), ("active", 1), ("achieved", 2)])
        self.assertIn("Judge feedback: fix add", models[1].seen[0][0][-1]["content"])
        self.assertEqual(data["state"], "completed")

    def test_goal_capped_and_stopped(self):
        # This is a goal-state unit test.  The preceding test covers the complete
        # asynchronous worker loop; waiting on that worker here made this assertion
        # depend on machine load and occasionally exhausted the 20-second test wait.
        request = self.engine._validate({"objective": "Impossible", **AGENT, "goal_max_turns": 1})
        task = self.engine.store.create(request)
        self.engine._record_extras(task, request)
        self.engine.store.event(task, "agent_message", {"content": "Try."})
        self.engine.store.state(task, "completed", {"changed_files": []})
        with patch("dikdik.control.chat_completion", return_value=answer('{"achieved": false, "reason": "no"}')):
            self.engine._after_goal_turn(task, request, "completed")
        data = self.engine.store.get(task)
        self.assertEqual(self.kinds(data, "goal_status")[-1]["status"], "capped")
        self.assertEqual(self.engine.goal_control(task, "stop"), {"status": "stopped"})
        with self.assertRaisesRegex(ValueError, "1–20 turns"):
            self.engine.submit({"objective": "x", **AGENT, "goal_max_turns": 50})

    # --- forks -------------------------------------------------------------------------------------
    def test_fork_whole_and_from_an_earlier_turn(self):
        models = [Script([edit(1), call(2, "run_tests"), call(3, "finish")]),
                  Script([edit(4, "a + b", "b + a"), call(5, "run_tests"), call(6, "finish")])]
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=models):
            task = self.idle(self.engine.submit({"objective": "Fix add", **AGENT}))["id"]
            first_end = [e for e in self.engine.store.get(task)["events"] if e["kind"] == "completed"][0]["seq"]
            self.engine.send(task, {"objective": "Swap", **AGENT})
            self.idle(task)
        whole = self.engine.fork(task, {})["id"]
        early = self.engine.fork(task, {"upto_seq": first_end, "title": "Before the swap"})["id"]
        self.assertIn("+    return b + a", self.engine.store.get(whole)["result"]["diff"])
        early_task = self.engine.store.get(early)
        self.assertEqual(early_task["title"], "Before the swap")
        self.assertEqual(sum(e["kind"] == "completed" for e in early_task["events"]), 1)
        self.assertEqual(self.engine.file_text("calc.py", early)["text"].strip().splitlines()[-1].strip(), "return a + b")
        self.assertEqual(early_task["events"][-1]["kind"], "forked")
        with self.assertRaisesRegex(ValueError, "finished turn"):
            self.engine.fork(task, {"upto_seq": 1})

    def test_branch_from_a_message(self):
        models = [Script([ModelResponse("First answer.", [])]), Script([ModelResponse("Second answer.", [])])]
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=models):
            task = self.idle(self.engine.submit({"objective": "Hello", **AGENT}))["id"]
            self.engine.send(task, {"objective": "Tell me more", **AGENT})
            self.idle(task)
        events = self.engine.store.get(task)["events"]
        follow_up = next(e["seq"] for e in events if e["kind"] == "user_message")
        first_reply = next(e["seq"] for e in events if e["kind"] == "agent_message")
        # From your own message: the message is left out and the branch is ready for a new one.
        mine = self.engine.store.get(self.engine.fork(task, {"message_seq": follow_up})["id"])
        texts = [e["data"].get("text") for e in mine["events"] if e["kind"] == "user_message"]
        self.assertEqual((texts, mine["state"], mine["title"]), ([], "completed", "Hello (branch)"))
        self.assertEqual([e["data"]["content"] for e in mine["events"] if e["kind"] == "agent_message"], ["First answer."])
        # From an agent reply: the reply is kept.
        theirs = self.engine.store.get(self.engine.fork(task, {"message_seq": first_reply})["id"])
        self.assertEqual([e["data"]["content"] for e in theirs["events"] if e["kind"] == "agent_message"], ["First answer."])
        self.assertEqual(theirs["events"][-1]["data"]["from_seq"], first_reply)
        # The branch takes follow-ups like any conversation.
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script([ModelResponse("Branch answer.", [])])):
            self.engine.send(mine["id"], {"objective": "Something else", **AGENT})
            self.assertEqual(self.idle(mine["id"])["state"], "completed")
        for bad in ({"message_seq": events[0]["seq"]}, {"message_seq": "3"}, {"message_seq": first_reply, "upto_seq": first_reply}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.fork(task, bad)

    # --- attachments ------------------------------------------------------------------------------
    def test_attachments_reach_the_agent(self):
        text = self.engine.attachments.add({"name": "notes.txt", "type": "text/plain", "data": base64.b64encode(b"Use integers only.").decode()})
        image = self.engine.attachments.add({"name": "screen.png", "type": "image/png", "data": base64.b64encode(PNG).decode()})
        for bad in ({"name": "../x", "type": "text/plain", "data": "eA=="}, {"name": "a.png", "type": "image/png", "data": base64.b64encode(b"not png").decode()},
                    {"name": "a", "type": "text/plain", "data": "!!"}, {"name": "big", "type": "text/plain", "data": base64.b64encode(b"x" * (3 * 1024 * 1024 + 1)).decode()}):
            with self.subTest(bad=bad["name"]), self.assertRaises(ValueError):
                self.engine.attachments.add(bad)
        model = Script([ModelResponse("Seen.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.idle(self.engine.submit({"objective": "Read these", **AGENT, "attachments": [text["id"], image["id"]]}))
        content = model.seen[0][0][-1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("Use integers only.", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual([f["name"] for f in self.kinds(data, "attachments")[0]["files"]], ["notes.txt", "screen.png"])
        with self.assertRaisesRegex(ValueError, "missing"):
            self.engine.submit({"objective": "x", **AGENT, "attachments": ["0" * 32]})

    # --- subagents ----------------------------------------------------------------------------------
    def test_delegate_to_a_read_only_subagent(self):
        (self.root / ".dikdik" / "agents").mkdir(parents=True)
        (self.root / ".dikdik" / "agents" / "tester.md").write_text("---\nname: tester\ndescription: Checks tests\n---\nLook at the tests.")
        self.assertEqual([a["name"] for a in load_subagents(self.root, self.data / "agents")], ["explorer", "tester"])
        parent = Script([call(1, "delegate", agent="explorer", task="Where is add?"), ModelResponse("It is in calc.py.", [])])
        child = Script([call(2, "read_file", path="calc.py"), ModelResponse("add is defined in calc.py line 1.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[parent, child]):
            data = self.idle(self.engine.submit({"objective": "Find add", **AGENT}))
        self.assertIn("delegate", parent.seen[0][1])
        self.assertEqual(child.seen[0][1], ["list_files", "read_file", "search_text"])  # read-only helper
        ends = [e for e in self.kinds(data, "subagent") if e["phase"] == "end"]
        self.assertEqual(ends[0]["findings"], "add is defined in calc.py line 1.")
        self.assertIn("explorer", parent.seen[0][0][0]["content"])

    # --- settings -------------------------------------------------------------------------------------
    def test_app_settings(self):
        self.assertEqual(self.engine.app_settings(), {"confirmation": "never", "condenser": True, "git_name": "", "git_email": "", "sounds": False, "router": "", "title_model": "auto", "event_retention_seconds": 0})
        self.assertEqual(self.engine.update_app_settings({"confirmation": "risky"})["confirmation"], "risky")
        for bad in ({"confirmation": "sometimes"}, {"condenser": "yes"}, {"theme": "dark"}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.update_app_settings(bad)

    def test_model_profile_override_must_exist(self):
        request = {"objective": "Hi", "mode": "agent", "verification": "syntax", "approve_external": True, "model_profile": "missing"}
        with self.assertRaisesRegex(ValueError, "model profile no longer exists"):
            self.engine._validate(request)
        with self.engine.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO profiles(id) VALUES('p1')")
        self.assertEqual(self.engine._validate({**request, "model_profile": "p1"})["model_profile"], "p1")
