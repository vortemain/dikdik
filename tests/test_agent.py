"""Trusted scripted-loop tests; these do not run a sandbox or a real model."""

import json
import threading
import unittest
from unittest.mock import patch

from dikdik.agent import AgentBudget, ModelResponse, ToolCall, run_agent


class ScriptedModel:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.messages = []

    def respond(self, messages, tools):
        self.messages.append(messages)
        return ModelResponse("", next(self.batches), 5)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.original = {"app.py": "value = 1\n", "tests/test_app.py": "assert True\n"}
        self.cancel = threading.Event()
        self.events = []
        self.snapshots = []
        self.sequence = 0

    def call(self, name, **args):
        self.sequence += 1
        return ToolCall(str(self.sequence), name, args)

    def edit(self, before="1", after="2", path="app.py"):
        return self.call("edit_file", path=path, before=before, after=after)

    def verify(self, files):
        self.snapshots.append(dict(files))
        return {"kind": "unittest", "status": "passed"}

    def run_script(self, batches, verify=None, budget=None, creation_policy=None):
        model = ScriptedModel(batches)
        result = run_agent(self.original, "Change the value", model, verify or self.verify,
                           lambda kind, data: self.events.append((kind, data)), self.cancel, budget, creation_policy=creation_policy)
        return result, model

    def test_failed_edit_pass_finish_and_protocol(self):
        def verifier(files):
            self.snapshots.append(dict(files))
            return {"kind": "unittest", "status": "passed" if "2" in files["app.py"] else "failed"}
        result, model = self.run_script([
            [self.call("run_tests")], [self.edit()],
            [self.call("run_tests")], [self.call("finish")]], verifier)
        self.assertEqual(result.state, "completed")
        self.assertTrue(result.tests_verified)
        self.assertEqual((result.model_calls, result.tool_calls, result.repair_attempts, result.usage_tokens), (4, 4, 1, 20))
        self.assertEqual(self.original["app.py"], "value = 1\n")
        self.assertEqual(len(self.snapshots), 2)
        self.assertTrue(any(kind == "repair" for kind, _ in self.events))
        for messages in model.messages[1:]:
            for i, message in enumerate(messages):
                if message["role"] == "tool":
                    prior = next(m for m in reversed(messages[:i]) if m["role"] == "assistant")
                    self.assertIn(message["tool_call_id"], [c["id"] for c in prior["tool_calls"]])
                    json.loads(prior["tool_calls"][0]["function"]["arguments"])

    def test_stale_verification_requires_recheck(self):
        result, model = self.run_script([
            [self.edit()], [self.call("run_tests")], [self.edit("2", "3")],
            [self.call("finish")], [self.call("run_tests")], [self.call("finish")]])
        self.assertEqual(result.state, "completed")
        self.assertEqual(len(self.snapshots), 2)
        self.assertIn("before finish", model.messages[4][-1]["content"])

    def test_syntax_failure_does_not_reach_verifier(self):
        result, _ = self.run_script([
            [self.edit("1", "(")], [self.call("run_tests")],
            [self.edit("(", "2")], [self.call("run_tests")], [self.call("finish")]])
        self.assertEqual(result.state, "completed")
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(result.checks[0]["status"], "failed")

    def test_protected_tests_and_repeated_identical_failure(self):
        result, _ = self.run_script([[self.edit("True", "False", "tests/test_app.py")],
                                     [self.edit("True", "False", "tests/test_app.py")]])
        self.assertEqual(result.state, "failed")
        self.assertIn("Repeated", result.error)
        self.assertEqual(result.files, self.original)
        self.assertEqual(self.snapshots, [])

    def test_only_run_tests_can_reach_callback(self):
        result, model = self.run_script([
            [self.call("list_files"), self.call("read_file", path="app.py"),
             self.call("search_text", query="value"), self.edit()],
            [self.call("finish")]], budget=AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.tests_verified)
        self.assertEqual(self.snapshots, [])

    def test_finish_with_edit_rejects_entire_batch(self):
        result, _ = self.run_script([[self.call("finish"), self.edit()]],
                                    budget=AgentBudget(max_model_calls=1))
        self.assertEqual(result.files, self.original)
        self.assertFalse(result.tests_verified)

    def test_no_net_change_cannot_finish(self):
        result, _ = self.run_script([[self.call("run_tests")], [self.call("finish")]],
                                    budget=AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.tests_verified)

    def test_strict_arguments(self):
        bad = [self.call("read_file", path="app.py", max_lines=True),
               self.call("read_file", path="app.py", max_lines=201),
               self.call("run_tests", command="anything"),
               self.call("edit_file", path="app.py", before="1"),
               self.call("unknown"), self.call("list_files", query=3)]
        result, model = self.run_script([bad], budget=AgentBudget(max_model_calls=1))
        self.assertEqual(result.files, self.original)
        self.assertEqual(self.snapshots, [])
        self.assertEqual(sum(d.get("failed", False) for k, d in self.events if k == "tool"), 6)

    def test_duplicate_ids_and_batch_limit(self):
        for batch in ([ToolCall("same", "run_tests", {}), ToolCall("same", "finish", {})],
                      [self.call("run_tests") for _ in range(9)]):
            with self.subTest(batch=len(batch)):
                result, _ = self.run_script([batch])
                self.assertEqual(result.state, "failed")
                self.assertEqual(result.tool_calls, 0)

    def test_id_reuse_across_responses(self):
        call = self.call("list_files")
        result, _ = self.run_script([[call], [call]])
        self.assertIn("duplicate", result.error)
        self.assertEqual(result.tool_calls, 1)

    def test_budgets_and_partial_files(self):
        for budget, expected in [
            (AgentBudget(max_model_calls=0), 0),
            (AgentBudget(max_tool_calls=0), 0),
            (AgentBudget(max_total_tokens=1), 0),
            (AgentBudget(deadline_seconds=0), 0),
            (AgentBudget(max_model_calls=1), 1)]:
            with self.subTest(budget=budget):
                result, _ = self.run_script([[self.edit()]], budget=budget)
                self.assertEqual(result.state, "failed")
                self.assertEqual(result.tool_calls, expected)
                self.assertFalse(result.tests_verified)

    def test_repair_budget(self):
        result, _ = self.run_script([[self.call("run_tests")], [self.edit()]],
                                    lambda _: {"kind": "unittest", "status": "failed"},
                                    AgentBudget(max_repair_attempts=0))
        self.assertIn("Repair budget", result.error)
        self.assertEqual(result.files, self.original)

    def test_cancellation_before_start_and_between_calls(self):
        self.cancel.set()
        result, _ = self.run_script([[self.edit()]])
        self.assertEqual((result.state, result.model_calls), ("cancelled", 0))
        self.cancel.clear()
        def verifier(files):
            self.cancel.set()
            return {"kind": "unittest", "status": "passed"}
        result, _ = self.run_script([[self.edit(), self.call("run_tests"), self.call("list_files")]], verifier)
        self.assertEqual((result.state, result.tool_calls), ("cancelled", 2))
        self.assertFalse(result.tests_verified)
        self.assertNotEqual(result.files, self.original)

    def test_blocked_inconclusive_and_non_test_pass(self):
        for status in ("blocked", "inconclusive", "not_run"):
            with self.subTest(status=status):
                result, _ = self.run_script([[self.call("run_tests")], [self.edit()]],
                                            lambda _, s=status: {"kind": "unittest", "status": s})
                self.assertEqual((result.state, result.model_calls), ("blocked", 1))
                self.assertFalse(result.tests_verified)
        result, _ = self.run_script([[self.edit()], [self.call("run_tests")], [self.call("finish")]],
                                    lambda _: {"kind": "syntax", "status": "passed"},
                                    AgentBudget(max_model_calls=3))
        self.assertFalse(result.tests_verified)

    def test_callback_exception_is_generic_and_blocked(self):
        def verifier(_):
            raise RuntimeError("credential-do-not-leak")
        result, _ = self.run_script([[self.call("run_tests")]], verifier)
        self.assertEqual(result.state, "blocked")
        self.assertNotIn("credential-do-not-leak", repr(result) + repr(self.events))

    def test_model_exception_is_generic(self):
        class BrokenModel:
            def respond(self, messages, tools):
                raise RuntimeError("credential-do-not-leak")
        result = run_agent(self.original, "objective", BrokenModel(), self.verify,
                           lambda *args: None, self.cancel)
        self.assertEqual(result.error, "Model request failed")
        self.assertNotIn("credential-do-not-leak", repr(result))

    def test_observation_and_context_limits(self):
        self.original["big.txt"] = "x" * 100000
        result, model = self.run_script([[self.call("read_file", path="big.txt")],
                                         [self.edit()]], budget=AgentBudget(max_model_calls=2))
        observation = model.messages[1][-1]["content"]
        self.assertLessEqual(len(observation.encode()), 65536)
        self.assertTrue(json.loads(observation)["truncated"])
        result = run_agent(self.original, "x" * 131073, model, self.verify,
                           lambda *args: None, self.cancel)
        self.assertEqual(result.model_calls, 0)
        self.assertIn("context", result.error)

    def test_deadline_checked_after_model(self):
        model = ScriptedModel([[self.edit()]])
        with patch("dikdik.agent.time.monotonic", side_effect=[0, 0, 181]):
            result = run_agent(self.original, "objective", model, self.verify,
                               lambda *args: None, self.cancel)
        self.assertEqual((result.state, result.tool_calls), ("failed", 0))

    def test_verifier_mutation_cannot_change_candidate(self):
        def verifier(files):
            files["app.py"] = "mutated"
            return {"kind": "unittest", "status": "passed"}
        result, _ = self.run_script([[self.edit()], [self.call("run_tests")], [self.call("finish")]], verifier)
        self.assertEqual(result.state, "blocked")
        self.assertFalse(result.tests_verified)
        self.assertEqual(result.files["app.py"], "value = 2\n")

    def test_persistence_failure_stops_before_model_or_tool_execution(self):
        model = ScriptedModel([[self.edit()]])

        def broken_emitter(*args):
            raise OSError("disk unavailable")

        with self.assertRaisesRegex(RuntimeError, "event persistence failed"):
            run_agent(self.original, "objective", model, self.verify, broken_emitter, self.cancel)
        self.assertEqual(model.messages, [])
        self.assertEqual(self.snapshots, [])

    def test_adapter_cannot_bypass_core_argument_size_limit(self):
        edit = self.call("edit_file", path="app.py", before="1", after="x" * 32769)
        result, _ = self.run_script([[edit]])
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.files, self.original)
        self.assertIn("size limit", result.error)

    def test_literal_search_returns_bounded_hits(self):
        self.original["many.txt"] = "value here\n" * 100
        result, model = self.run_script([[self.call("search_text", query="value")], [self.edit()]], budget=AgentBudget(max_model_calls=2))
        observation = json.loads(model.messages[1][-1]["content"])
        self.assertEqual(len(observation["matches"]), 40)
        self.assertTrue(observation["truncated"])

    def test_failed_unchanged_candidate_is_not_blindly_executed_again(self):
        checks = []

        def failed(files):
            checks.append(dict(files))
            return {"kind": "unittest", "status": "failed", "seconds": len(checks)}

        result, _ = self.run_script([[self.call("run_tests")], [self.call("run_tests")], [self.call("run_tests")]], failed)
        self.assertEqual(result.state, "failed")
        self.assertEqual(len(checks), 1)
        self.assertIn("Repeated", result.error)

    def test_creates_production_and_test_files_with_current_candidate_verification(self):
        result, _ = self.run_script([
            [self.call("create_file", path="feature/service.py", content="value = 2\n")],
            [self.call("create_file", path="test_feature.py", content="assert 2 == 2\n")],
            [self.call("run_tests")],
            [self.call("edit_file", path="test_feature.py", before="assert 2 == 2", after="assert 2 == 2\nassert isinstance(2, int)")],
            [self.call("finish")], [self.call("run_tests")], [self.call("finish")],
        ], creation_policy=lambda path: True)
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.files["test_feature.py"], "assert 2 == 2\nassert isinstance(2, int)\n")
        self.assertEqual(result.files["feature/service.py"], "value = 2\n")
        self.assertEqual(len(self.snapshots), 2)
        self.assertNotIn("feature/service.py", self.original)
        self.assertEqual(result.files["tests/test_app.py"], self.original["tests/test_app.py"])

    def test_creation_requires_explicit_policy_and_preserves_passed_evidence_on_rejection(self):
        for policy in [None, lambda path: False, lambda path: "yes"]:
            with self.subTest(policy=policy):
                result, _ = self.run_script([[self.call("create_file", path="feature.py", content="pass\n")]], budget=AgentBudget(max_model_calls=1), creation_policy=policy)
                self.assertEqual(result.files, self.original)
                self.assertFalse(result.tests_verified)

    def test_creation_policy_failure_is_sanitized_and_cannot_write_candidate(self):
        def broken_policy(path):
            raise RuntimeError("private-configuration-do-not-expose")
        result, model = self.run_script([[self.call("create_file", path="feature.py", content="pass\n")]], budget=AgentBudget(max_model_calls=1), creation_policy=broken_policy)
        self.assertEqual(result.files, self.original)
        self.assertNotIn("private-configuration-do-not-expose", repr(self.events) + repr(model.messages))

    def test_creation_after_failure_obeys_the_repair_budget(self):
        result, _ = self.run_script([[self.call("run_tests")], [self.call("create_file", path="feature.py", content="pass\n")]], verify=lambda files: {"kind": "unittest", "status": "failed"}, budget=AgentBudget(max_repair_attempts=0), creation_policy=lambda path: True)
        self.assertIn("Repair budget", result.error)
        self.assertEqual(result.files, self.original)


if __name__ == "__main__":
    unittest.main()
