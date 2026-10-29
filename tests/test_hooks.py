"""Hook contracts, denial precedence, isolation, deadlines and runtime coverage."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.engine import Engine
from dikdik.hooks import Decision, HookEngine, HookEvent, HookResult, document_rules, load_hook_engine
from dikdik.plugins import read_manifest


def rule(name="rule", event="tool.before", decision="block", **kwargs):
    return {"id": name, "event": event, "decision": decision,
            "reason": "Policy says no" if decision == "block" else "", **kwargs}


class Script:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.seen = []

    def respond(self, messages, tools):
        self.seen.append((messages, tools))
        return next(self.responses)


def tool(name, arguments=None, ident="call"):
    return ModelResponse("", [ToolCall(ident, name, arguments or {})])


class HookTests(unittest.TestCase):
    def payload(self):
        return {"tool": "read_file", "call_id": "c1", "arguments": {"path": "app.py"}}

    def test_priority_stable_ties_and_sticky_denial(self):
        hooks, order = HookEngine(), []
        for name, priority, decision in (("late", 10, "allow"), ("block", -1, "block"), ("tie", -1, "allow")):
            def callback(_, name=name, decision=decision):
                order.append(name)
                return {"decision": decision, "reason": "denied" if decision == "block" else ""}
            hooks.register_host(name, "tool.before", callback, priority=priority)
        hooks.register_rule("plugin sample", rule("plugin-allow", decision="allow", priority=-10000))
        report = hooks.dispatch("tool.before", "task", self.payload())
        self.assertEqual(order, ["block", "tie", "late"])
        self.assertEqual(report.decision, Decision.BLOCK)
        self.assertEqual(report.reasons, ("denied",))

    def test_snapshots_and_payloads_are_isolated(self):
        hooks = HookEngine()
        def mutate(context):
            context.payload["arguments"]["path"] = "outside"
            return HookResult()
        hooks.register_host("mutate", "tool.before", mutate)
        snapshot = hooks.fork()
        hooks.register_rule("plugin later", rule())
        source = self.payload()
        self.assertTrue(snapshot.dispatch("tool.before", "task", source).allowed)
        self.assertEqual(source["arguments"]["path"], "app.py")
        self.assertFalse(hooks.dispatch("tool.before", "task", source).allowed)

    def test_rule_cannot_be_mutated_after_registration(self):
        hooks, source = HookEngine(), rule()
        hooks.register_rule("plugin sample", source)
        source["decision"] = "allow"
        self.assertFalse(hooks.dispatch("tool.before", "task", self.payload()).allowed)

    def test_invalid_output_and_exceptions_fail_closed_without_secret_leak(self):
        invalid = [None, True, {}, {"decision": "yes"}, {"decision": "block"},
                   {"decision": "allow", "permissions": "all"},
                   {"decision": "allow", "data": {"x": float("nan")}},
                   {"decision": "allow", "data": {"x": "x" * 20000}}]
        for value in invalid:
            with self.subTest(value=str(value)[:60]):
                hooks = HookEngine()
                hooks.register_host("bad", "tool.before", lambda _, v=value: v)
                self.assertFalse(hooks.dispatch("tool.before", "task", self.payload()).allowed)
        hooks = HookEngine()
        def broken(_):
            raise RuntimeError("PRIVATE_SECRET")
        hooks.register_host("broken", "tool.before", broken)
        report = hooks.dispatch("tool.before", "task", self.payload())
        self.assertNotIn("PRIVATE_SECRET", repr(report))
        self.assertEqual(report.executions[0]["status"], "error")

    def test_timeout_ignores_late_allow_and_records_status(self):
        release = threading.Event()
        hooks = HookEngine()
        def slow(_):
            release.wait(2)
            return HookResult()
        hooks.register_host("slow", "tool.before", slow, timeout=.02)
        started = time.monotonic()
        try:
            report = hooks.dispatch("tool.before", "task", self.payload())
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse(report.allowed)
            self.assertEqual(report.executions[0]["status"], "timed_out")
        finally:
            release.set()

    def test_callback_capacity_exhaustion_is_bounded_and_blocks(self):
        from dikdik.hooks import _CALLBACK_SLOTS
        # Reserve slots without creating permanently sleeping workers.
        acquired = []
        try:
            for _ in range(8):
                acquired.append(_CALLBACK_SLOTS.acquire(timeout=2))
            self.assertTrue(all(acquired))
            hooks = HookEngine()
            hooks.register_host("overflow", "tool.before", lambda _: HookResult())
            report = hooks.dispatch("tool.before", "task", self.payload())
            self.assertFalse(report.allowed)
            self.assertEqual(report.executions[0]["status"], "error")
        finally:
            for success in acquired:
                if success:
                    _CALLBACK_SLOTS.release()

    def test_pathological_regex_is_killed_on_timeout(self):
        hooks = HookEngine()
        hooks.register_rule("plugin regex", rule(match={"pattern": "(a+)+$"}, timeout_seconds=.15))
        started = time.monotonic()
        report = hooks.dispatch("tool.before", "task", {**self.payload(), "arguments": {"x": "a" * 10000 + "!"}})
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(report.allowed)
        self.assertEqual(report.executions[0]["status"], "timed_out")

    def test_regex_legacy_semantics_and_nonmatching_rules(self):
        hooks = HookEngine()
        for entry in document_rules({"block": [{"tool": "edit_file", "pattern": r"app\.py", "message": "frozen"}]}):
            hooks.register_rule("project", entry)
        self.assertTrue(hooks.dispatch("tool.before", "task", self.payload()).allowed)
        report = hooks.dispatch("tool.before", "task", {**self.payload(), "tool": "edit_file"})
        self.assertEqual(report.reasons, ("frozen",))

    def test_invalid_patterns_fail_closed(self):
        hooks = HookEngine()
        hooks.register_rule("project", rule(match={"pattern": "["}))
        self.assertFalse(hooks.dispatch("tool.before", "task", self.payload()).allowed)

    def test_strict_registration_schema_and_duplicate_ids(self):
        invalid = [rule(event="unknown"), rule(priority=True), rule(timeout_seconds=0),
                   rule(timeout_seconds=float("nan")), rule(priority=10001), rule(timeout_seconds=31),
                   rule(command="echo bypass"), rule(callback="module:run"), rule(match={"permissions": "all"}),
                   rule(match={"tool": 12}), rule(decision="allow", data=[])]
        for entry in invalid:
            with self.subTest(entry=str(entry)), self.assertRaises((ValueError, TypeError)):
                HookEngine().register_rule("plugin sample", entry)
        hooks = HookEngine()
        hooks.register_rule("plugin sample", rule())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            hooks.register_rule("plugin sample", rule())

    def test_runtime_payload_contracts_are_enforced(self):
        for event, payload in (("unknown", {}), ("task.started", {}), ("context.building", {"objective": 3, "mode": "agent"}),
                               ("tool.before", {}), ("tool.after", self.payload()), ("task.completed", {"state": False})):
            with self.subTest(event=event), self.assertRaises(ValueError):
                HookEngine().dispatch(event, "task", payload)

    def test_traces_correlate_and_do_not_include_raw_payload(self):
        traces, hooks = [], HookEngine(lambda kind, data: traces.append((kind, data)))
        hooks.register_rule("plugin sample", rule(decision="allow", data={"label": "audited"}))
        payload = self.payload()
        payload["arguments"]["secret"] = "DO_NOT_LOG"
        report = hooks.dispatch("tool.before", "task", payload)
        self.assertEqual([k for k, _ in traces], ["hook_execution", "hook_event"])
        self.assertTrue(all(d["dispatch_id"] == report.dispatch_id for _, d in traces))
        self.assertTrue(all(d["run_id"] == hooks.run_id for _, d in traces))
        self.assertEqual(traces[0][1]["data"], {"label": "audited"})
        self.assertNotIn("DO_NOT_LOG", json.dumps(traces))

    def test_audit_failure_prevents_execution(self):
        def cannot_record(*_):
            raise RuntimeError("audit unavailable")
        hooks = HookEngine(cannot_record)
        with self.assertRaisesRegex(RuntimeError, "audit persistence failed"):
            hooks.dispatch("tool.before", "task", self.payload())

    def test_corrupt_configuration_blocks_every_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".dikdik").mkdir()
            path = root / ".dikdik" / "hooks.json"
            for document in ("{broken", '{"block": "invalid"}', '{"hooks":[{}]}'):
                path.write_text(document)
                hooks = load_hook_engine(HookEngine(), root)
                for event in HookEvent:
                    payload = {**self.payload(), "objective": "test", "mode": "agent", "state": "completed", "error": None, "result": {}}
                    self.assertFalse(hooks.dispatch(event, "task", payload).allowed)

    def test_all_six_events_in_the_tool_loop(self):
        traces = []
        hooks = HookEngine(lambda k, d: traces.append((k, d)))
        model = Script(tool("read_file", {"path": "app.py"}, "ok"),
                       tool("read_file", {"path": "missing"}, "bad"), ModelResponse("Done", []))
        result = run_agent({"app.py": "x=1\n"}, "Inspect", model, None, lambda *_: None,
                           threading.Event(), hooks=hooks, task_id="task")
        self.assertEqual(result.state, "completed")
        events = [d["event"] for k, d in traces if k == "hook_event"]
        self.assertEqual(events, ["task.started", "context.building", "tool.before", "tool.after",
                                  "tool.before", "tool.failed", "task.completed"])

    def test_allow_cannot_skip_confirmation_or_create_policy(self):
        hooks, calls = HookEngine(), []
        hooks.register_rule("plugin allow", rule(decision="allow"))
        model = Script(tool("create_file", {"path": "new.py", "content": "x=1"}), ModelResponse("Done", []))
        result = run_agent({}, "Create", model, None, lambda *_: None, threading.Event(), hooks=hooks,
                           confirm=lambda call: (calls.append(call.name) or False, "User denied"),
                           creation_policy=lambda _: True)
        self.assertEqual(calls, ["create_file"])
        self.assertEqual(result.files, {})
        model = Script(tool("create_file", {"path": "new.py", "content": "x=1"}), ModelResponse("Done", []))
        result = run_agent({}, "Create", model, None, lambda *_: None, threading.Event(), hooks=hooks,
                           creation_policy=lambda _: False)
        self.assertEqual(result.files, {})

    def test_before_block_prevents_effect_and_post_block_stops_progress(self):
        for event in ("tool.before", "tool.after"):
            with self.subTest(event=event):
                hooks = HookEngine()
                hooks.register_rule("project", rule(event=event))
                model = Script(tool("edit_file", {"path": "app.py", "before": "1", "after": "2"}),
                               ModelResponse("Done", []))
                result = run_agent({"app.py": "x=1\n"}, "Edit", model, None, lambda *_: None,
                                   threading.Event(), hooks=hooks)
                self.assertEqual(result.files["app.py"], "x=1\n" if event == "tool.before" else "x=2\n")
                if event == "tool.after":
                    self.assertEqual(result.state, "blocked")
                    self.assertEqual(len(model.seen), 1)

    def test_completion_block_is_not_success(self):
        hooks = HookEngine()
        hooks.register_rule("project", rule(event="task.completed"))
        result = run_agent({}, "Explain", Script(ModelResponse("Done", [])), None, lambda *_: None,
                           threading.Event(), hooks=hooks)
        self.assertEqual(result.state, "blocked")
        self.assertFalse(result.tests_verified)


class EngineHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("x=1\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)

    def wait(self, task):
        self.engine.pool.submit(lambda: None).result(timeout=30)
        return self.engine.store.get(task)

    def test_task_gate_blocks_before_inventory_and_model(self):
        self.engine.hooks.register_host("deny", "task.started", lambda _: HookResult(Decision.BLOCK, "maintenance"))
        with patch("dikdik.engine.CompatibleAgentModel") as model:
            data = self.wait(self.engine.submit({"objective": "Explain", "mode": "agent", "verification": "syntax", "approve_external": True}))
        model.assert_not_called()
        self.assertEqual(data["state"], "blocked")
        self.assertFalse(any(e["kind"] == "inspected" for e in data["events"]))
        self.assertEqual([e["data"]["event"] for e in data["events"] if e["kind"] == "hook_event"],
                         ["task.started", "task.completed"])

    def test_plugin_trust_then_declarative_registration_and_corruption(self):
        folder = self.root / ".dikdik" / "plugins" / "sample"
        folder.mkdir(parents=True)
        manifest = {"name": "sample", "hooks": {"hooks": [rule(event="task.started")]}}
        path = folder / "plugin.json"
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.engine.plugins.hook_documents(), [])
        self.engine.plugins.set_enabled("sample", {"enabled": True, "trust": True})
        data = self.wait(self.engine.submit({"objective": "Explain", "mode": "agent", "verification": "syntax", "approve_external": True}))
        self.assertEqual(data["state"], "blocked")
        path.write_text('{"name":"sample","hooks":{"hooks":[{"command":"bypass"}]}}')
        hooks = load_hook_engine(self.engine.hooks, self.root, self.engine.plugins.hook_documents())
        self.assertFalse(hooks.dispatch("task.started", "task", {"objective": "test", "mode": "agent"}).allowed)
        with self.assertRaises(ValueError):
            read_manifest(folder)

    def test_planning_tools_are_hooked_and_completed_once(self):
        self.engine.hooks.register_host("deny-plan", "tool.before", lambda _: HookResult(Decision.BLOCK, "locked"))
        model = Script(tool("write_plan", {"content": "Should not be saved"}), ModelResponse("Done", []))
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            data = self.wait(self.engine.submit({"objective": "Plan", "mode": "plan", "verification": "syntax", "approve_external": True}))
        self.assertFalse(self.engine.planning.get(data["id"])["content"])
        events = [e["data"]["event"] for e in data["events"] if e["kind"] == "hook_event"]
        self.assertIn("tool.before", events)
        self.assertIn("tool.failed", events)
        self.assertEqual(events.count("task.completed"), 1)

    def test_completion_denial_prevents_critic_and_goal_success_path(self):
        self.engine.hooks.register_host("deny-completion", "task.completed", lambda _: HookResult(Decision.BLOCK, "review needed"))
        profile = self.engine.agents.create({"name": "With critic", "critic": True})
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script(ModelResponse("Done", []))), \
                patch.object(self.engine, "_critic") as critic, patch.object(self.engine, "_after_goal_turn") as goal:
            data = self.wait(self.engine.submit({"objective": "Explain", "mode": "agent", "verification": "syntax",
                                                 "approve_external": True, "agent_profile": profile["id"]}))
        self.assertEqual(data["state"], "blocked")
        critic.assert_not_called()
        self.assertEqual(goal.call_args.args[-1], "blocked")

    def test_audit_write_failure_commits_failure_without_running_the_model(self):
        original = self.engine.store.event
        def event(task, kind, data):
            if kind.startswith("hook_"):
                raise OSError("audit database write failed")
            return original(task, kind, data)
        with patch.object(self.engine.store, "event", side_effect=event), \
                patch("dikdik.engine.CompatibleAgentModel") as model, patch("dikdik.engine.traceback.print_exc"):
            data = self.wait(self.engine.submit({"objective": "Explain", "mode": "agent", "verification": "syntax", "approve_external": True}))
        model.assert_not_called()
        self.assertEqual(data["state"], "failed")
        self.assertIn("audit persistence failed", data["result"]["error"])

    def test_acp_launch_is_blocked_before_process_or_credentials(self):
        self.engine.hooks.register_host("deny-acp", "tool.before", lambda _: HookResult(Decision.BLOCK, "external execution locked"))
        hooks = self.engine.hooks.fork()
        task = self.engine.store.create({"objective": "Work"})
        self.engine._task_hooks[task] = hooks
        try:
            with patch.object(self.engine.acp_agents, "launch_command") as launch:
                self.engine._acp_workflow(task, {"acp_agent": "test"}, {}, threading.Event(), time.monotonic(), {})
            launch.assert_not_called()
            self.assertEqual(self.engine.store.get(task)["state"], "blocked")
        finally:
            self.engine._task_hooks.pop(task, None)
            self.engine._hook_completions.discard(task)

    def test_delegated_tools_use_the_same_policy_and_have_child_trace_ids(self):
        self.engine.hooks.register_rule("project", rule(match={"tool": "read_file"}))
        parent = Script(tool("delegate", {"agent": "explorer", "task": "Read app.py"}), ModelResponse("Done", []))
        child = Script(tool("read_file", {"path": "app.py"}, "child-read"), ModelResponse("Read blocked", []))
        with patch("dikdik.engine.CompatibleAgentModel", side_effect=[parent, child]):
            data = self.wait(self.engine.submit({"objective": "Inspect", "mode": "agent", "verification": "syntax", "approve_external": True}))
        executions = [e["data"] for e in data["events"] if e["kind"] == "hook_execution"]
        child_execution = next(e for e in executions if e["call_id"] == "child-read")
        self.assertEqual(child_execution["decision"], "block")
        self.assertEqual(child_execution["task_id"], data["id"] + "/delegate/explorer")
        parent_execution = next(e for e in executions if e["tool"] == "delegate")
        self.assertEqual(parent_execution["run_id"], child_execution["run_id"])


if __name__ == "__main__":
    unittest.main()
