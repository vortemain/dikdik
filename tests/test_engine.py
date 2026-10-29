import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik.engine import Engine, TERMINAL, candidate_diff
from dikdik.agent import ModelResponse, ToolCall
from dikdik.store import Store


def request(**changes):
    return {"objective": "Fix addition", "mode": "offline", "verification": "syntax", "edits": [{"path": "calculator.py", "before": "a - b", "after": "a + b"}], **changes}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        (self.root / "test_calculator.py").write_text("import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3),5)\n")
        self.engine = Engine(self.root, self.base / "data")
        self.addCleanup(self.engine.close)

    def wait(self, task):
        # FIFO barrier avoids repeatedly contending with the worker's SQLite writes.
        self.engine.pool.submit(lambda: None).result(timeout=20)
        data = self.engine.store.get(task)
        self.assertIn(data["state"], TERMINAL, data)
        return data

    def test_real_workflow_generates_diff_snapshot_and_durable_evidence(self):
        original = (self.root / "calculator.py").read_bytes()
        task = self.engine.submit(request())
        result = self.wait(task)
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["result"]["tests_verified"])
        self.assertFalse(result["result"]["source_modified"])
        self.assertIn("+    return a + b", result["result"]["diff"])
        self.assertEqual((self.root / "calculator.py").read_bytes(), original)
        self.assertIn("a + b", (self.base / "data" / "artifacts" / task / "calculator.py").read_text())
        kinds = [event["kind"] for event in result["events"]]
        self.assertEqual(kinds, ["queued", "running", "hook_event", "hook_event", "inspected", "skills", "proposal", "syntax", "hook_event", "completed"])
        lifecycle = [event["data"]["event"] for event in result["events"] if event["kind"] == "hook_event"]
        self.assertEqual(lifecycle, ["task.started", "context.building", "task.completed"])
        reopened = Store(self.base / "data" / "state.sqlite3")
        self.assertEqual(reopened.get(task), result)

    def test_execution_and_disclosure_require_actual_boolean_approval(self):
        for change in [{"verification": "docker"}, {"verification": "docker", "approve_execution": "yes"}, {"mode": "model"}, {"mode": "model", "approve_external": "true"}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "approval"):
                self.engine.submit(request(**change))

    def test_docker_block_is_not_success(self):
        with patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "blocked", "exit_code": 125}) as runner:
            result = self.wait(self.engine.submit(request(verification="docker", approve_execution=True)))
        self.assertEqual(result["state"], "blocked")
        self.assertFalse(result["result"]["tests_verified"])
        runner.assert_called_once()

    def test_test_failure_is_not_success(self):
        with patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "failed", "exit_code": 1}):
            result = self.wait(self.engine.submit(request(verification="docker", approve_execution=True)))
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["result"]["tests_verified"])

    def test_syntax_failure_prevents_execution(self):
        bad = [{"path": "calculator.py", "before": "a - b", "after": "("}]
        with patch("dikdik.engine.run_tests") as runner:
            result = self.wait(self.engine.submit(request(edits=bad, verification="docker", approve_execution=True)))
        self.assertEqual(result["state"], "failed")
        runner.assert_not_called()

    def test_no_matching_edit_fails(self):
        result = self.wait(self.engine.submit(request(edits=[{"path": "calculator.py", "before": "not here", "after": "x"}])))
        self.assertEqual(result["state"], "failed")
        self.assertIn("exactly once", result["result"]["error"])

    def test_cancel_and_single_worker_limit(self):
        entered, release = threading.Event(), threading.Event()

        def delayed(*args):
            entered.set()
            release.wait(3)
            return request()["edits"], {"model_calls": 0}

        with patch("dikdik.engine.propose", side_effect=delayed):
            task = self.engine.submit(request())
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(ValueError, "One task"):
                    self.engine.submit(request())
                self.assertTrue(self.engine.cancel(task))
            finally:
                release.set()
            self.assertEqual(self.wait(task)["state"], "cancelled")

    def test_data_lock_prevents_duplicate_service(self):
        with self.assertRaisesRegex(ValueError, "already in use"):
            Engine(self.root, self.base / "data")

    def test_workspace_binding_scopes_conversations(self):
        """One data directory serves many projects; each sees only its own tasks."""
        store = Store(self.base / "other.sqlite3")
        store.bind_workspace(self.root)
        task = store.create(request())
        store.bind_workspace(self.base / "different")
        self.assertEqual(store.list(), [])
        with self.assertRaises(KeyError):
            store.get(task)
        with self.assertRaises(KeyError):
            store.update_meta(task, {"pinned": True})

    def test_state_storage_cannot_be_inside_source(self):
        with self.assertRaises(ValueError):
            Engine(self.root, self.root / ".state")

    def test_arbitrary_request_fields_are_not_persisted(self):
        task = self.engine.submit(request(api_key="DO_NOT_PERSIST"))
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(self.wait(task)))

    def test_interrupted_tasks_are_not_replayed(self):
        task = self.engine.store.create(request())
        self.engine.store.state(task, "running")
        self.engine.store.recover()
        data = self.engine.store.get(task)
        self.assertEqual(data["state"], "interrupted")
        self.assertIn("exact resume is unsupported", data["result"]["error"])

    def test_invalid_requests_rejected(self):
        for value in [None, [], {}, request(objective=""), request(mode="unknown"), request(verification="host"), request(edits=[])]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.engine.submit(value)

    def test_diff_handles_missing_final_newline(self):
        diff = candidate_diff({"app.py": "x=1"}, {"app.py": "x=2"})
        self.assertIn("-x=1\n\\ No newline at end of file\n+x=2\n", diff)

    def test_recovery_attempts_only_previously_approved_container_cleanup(self):
        data = self.base / "recovery"
        data.mkdir()
        store = Store(data / "state.sqlite3")
        store.bind_workspace(self.root)
        task = store.create(request(verification="docker", approve_execution=True))
        with patch("dikdik.engine.cleanup_container", return_value="Daemon unavailable") as cleanup:
            recovered = Engine(self.root, data)
        try:
            cleanup.assert_called_once_with("dikdik-" + task)
            result = recovered.store.get(task)
            self.assertEqual(result["state"], "interrupted")
            self.assertEqual(result["events"][-2]["kind"], "recovery_cleanup")
        finally:
            recovered.close()

    def test_malformed_credential_never_reaches_results_or_events(self):
        credential = "DUMMY_REVIEW_CREDENTIAL\nINVALID"
        env = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "configured", "DIKDIK_API_KEY": credential}
        with patch.dict(os.environ, env), patch("urllib.request.build_opener") as network:
            result = self.wait(self.engine.submit(request(mode="model", approve_external=True)))
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("DUMMY_REVIEW_CREDENTIAL", json.dumps(result))
        network.assert_not_called()

    def test_agent_mode_requires_disclosure_and_execution_approval_for_docker(self):
        for changes in [
            {"mode": "agent"},
            {"mode": "agent", "verification": "docker", "approve_external": True},
            {"mode": "agent", "verification": "docker", "approve_execution": True},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.engine.submit(request(**changes))

    def test_syntax_only_agent_can_finish_but_never_claims_tests(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Fix", [ToolCall("e", "edit_file", {"path": "calculator.py", "before": "a - b", "after": "a + b"})]),
            ModelResponse("Check", [ToolCall("t", "run_tests", {})]),
            ModelResponse("Done", [ToolCall("f", "finish", {})]),
        ]
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests") as runner:
            result = self.wait(self.engine.submit(request(mode="agent", approve_external=True)))
        runner.assert_not_called()
        self.assertEqual(result["state"], "completed", result["result"])
        self.assertFalse(result["result"]["tests_verified"])
        self.assertIn("Syntax checked only", result["result"]["limitations"])
        tools = [t["function"]["name"] for t in model.respond.call_args_list[0].args[1]]
        self.assertNotIn("run_command", tools)  # no shell without execution approval

    def test_original_agent_integrates_repair_artifacts_and_persisted_evidence(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Inspect first", [ToolCall("read", "read_file", {"path": "calculator.py"})]),
            ModelResponse("Establish failure", [ToolCall("before", "run_tests", {})]),
            ModelResponse("Repair", [ToolCall("edit", "edit_file", {"path": "calculator.py", "before": "a - b", "after": "a + b"})]),
            ModelResponse("Verify", [ToolCall("after", "run_tests", {})]),
            ModelResponse("Finish", [ToolCall("done", "finish", {})]),
        ]

        def verification(path, cancel, **kwargs):
            # Instrumented verifier contract, not actual repository test execution.
            fixed = "a + b" in (path / "calculator.py").read_text()
            return {"kind": "unittest", "status": "passed" if fixed else "failed", "exit_code": 0 if fixed else 1}

        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests", side_effect=verification) as runner:
            data = self.wait(self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True, edits=[])))
        self.assertEqual(data["state"], "completed")
        self.assertTrue(data["result"]["tests_verified"])
        self.assertEqual(data["result"]["agent"]["repair_attempts"], 1)
        self.assertEqual(runner.call_count, 2)
        self.assertIn("a - b", (self.root / "calculator.py").read_text())
        self.assertIn("+    return a + b", data["result"]["diff"])
        self.assertTrue(any(e["kind"] == "agent_repair" for e in data["events"]))

    def test_agent_blocked_docker_cannot_be_completed(self):
        model = Mock()
        model.respond.return_value = ModelResponse("Verify", [ToolCall("test", "run_tests", {})])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "blocked"}):
            data = self.wait(self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True)))
        self.assertEqual(data["state"], "blocked")
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(model.respond.call_count, 1)

    def test_accepted_cancellation_during_final_diff_cannot_be_ignored(self):
        entered, release = threading.Event(), threading.Event()
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Edit", [ToolCall("edit", "edit_file", {"path": "calculator.py", "before": "a - b", "after": "a + b"})]),
            ModelResponse("Test", [ToolCall("test", "run_tests", {})]),
            ModelResponse("Finish", [ToolCall("finish", "finish", {})]),
        ]

        def delayed_diff(original, changed):
            entered.set()
            release.wait(3)
            return candidate_diff(original, changed)

        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "passed"}), patch("dikdik.engine.candidate_diff", side_effect=delayed_diff):
            task = self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True))
            self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(self.engine.cancel(task))
            finally:
                release.set()
            data = self.wait(task)
        self.assertEqual(data["state"], "cancelled")
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(data["events"][-1]["kind"], "cancelled")
        self.assertFalse(self.engine.cancel(task))

    def test_agent_creates_reviewable_production_and_test_files_without_source_writes(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Create feature", [ToolCall("create", "create_file", {"path": "feature/service.py", "content": "value = 2\n"})]),
            ModelResponse("Add coverage", [ToolCall("test-file", "create_file", {"path": "test_feature.py", "content": "import unittest\nfrom feature.service import value\nclass FeatureTest(unittest.TestCase):\n def test_value(self): self.assertEqual(value,2)\n"})]),
            ModelResponse("Fix existing bug", [ToolCall("edit", "edit_file", {"path": "calculator.py", "before": "a - b", "after": "a + b"})]),
            ModelResponse("Verify", [ToolCall("test", "run_tests", {})]),
            ModelResponse("Finish", [ToolCall("finish", "finish", {})]),
        ]

        def instrumented_verifier(candidate, *args, **kwargs):
            self.assertEqual((candidate / "feature" / "service.py").read_text(), "value = 2\n")
            self.assertIn("test_value", (candidate / "test_feature.py").read_text())
            return {"kind": "unittest", "status": "passed", "note": "Contract verifier only; no live model or Docker run"}

        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests", side_effect=instrumented_verifier):
            data = self.wait(self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True)))
        self.assertEqual(data["state"], "completed")
        self.assertEqual([(item["path"], item["status"]) for item in data["result"]["file_changes"]], [("calculator.py", "modified"), ("feature/service.py", "added"), ("test_feature.py", "added")])
        self.assertEqual(data["result"]["changed_files"], ["calculator.py", "feature/service.py", "test_feature.py"])
        self.assertIn("+++ b/feature/service.py", data["result"]["diff"])
        self.assertEqual({p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)

    def test_agent_cannot_create_ignored_or_existing_unindexed_paths(self):
        (self.root / ".gitignore").write_text("generated/\n")
        (self.root / "binary.py").write_bytes(b"\xff\x00")
        for path in ["generated/new.py", "binary.py"]:
            model = Mock()
            model.respond.side_effect = [ModelResponse("Create", [ToolCall("create", "create_file", {"path": path, "content": "value = 1\n"})])]
            with self.subTest(path=path), patch("dikdik.engine.CompatibleAgentModel", return_value=model):
                data = self.wait(self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True)))
            self.assertEqual(data["state"], "failed")
            self.assertEqual(data["result"]["changed_files"], [])
            self.assertFalse((self.base / "data" / "artifacts" / data["id"] / path).exists())
        self.assertEqual((self.root / "binary.py").read_bytes(), b"\xff\x00")

    def test_new_file_candidate_reaches_actual_unavailable_docker_boundary(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Create", [ToolCall("create", "create_file", {"path": "feature.py", "content": "value = 1\n"})]),
            ModelResponse("Verify", [ToolCall("test", "run_tests", {})]),
        ]
        # Real sandbox runner, with only the missing CLI simulated. No host fallback.
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.sandbox.subprocess.Popen", side_effect=FileNotFoundError):
            data = self.wait(self.engine.submit(request(mode="agent", verification="docker", approve_external=True, approve_execution=True)))
        self.assertEqual(data["state"], "blocked")
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(data["result"]["file_changes"][0]["status"], "added")
        self.assertFalse((self.root / "feature.py").exists())
