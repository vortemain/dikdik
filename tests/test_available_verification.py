"""Available checks are useful evidence, but must never be labelled passed tests."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dikdik.agent import AgentBudget, ModelResponse, ToolCall, run_agent
from dikdik.engine import Engine
from dikdik.local_exec import run_tests
from dikdik.runners import detect_local_checks


def call(identity, name, **args):
    return ModelResponse("", [ToolCall(str(identity), name, args)])


class Script:
    def __init__(self, *replies):
        self.replies = iter(replies)

    def respond(self, *_):
        return next(self.replies)


class CheckDetectionTests(unittest.TestCase):
    def test_missing_compiler_is_unavailable_before_any_project_command_runs(self):
        with tempfile.TemporaryDirectory() as temp, patch("dikdik.local_exec.shutil.which", return_value=None), patch("dikdik.local_exec.run") as execute:
            result = run_tests(Path(temp), [{"kind": "typecheck", "label": "typecheck", "command": "npm run typecheck",
                                             "requires": [{"tool": "tsc", "cwd": ""}]}], threading.Event())
        execute.assert_not_called()
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["reason"], "missing_check_tool")

    def test_unavailable_check_permission_is_explicit_and_does_not_survive_a_real_failure(self):
        unavailable = {"kind": "checks", "status": "not_run", "reason": "missing_check_tool", "note": "tsc unavailable"}
        replies = [call(1, "edit_file", path="a.ts", before="1", after="2"), call(2, "run_tests"), call(3, "finish")]
        result = run_agent({"a.ts": "const x=1;"}, "Edit", Script(*replies), lambda _: unavailable, lambda *_: None,
                           threading.Event(), allow_unverified_checks=True)
        self.assertEqual(result.state, "completed")
        self.assertFalse(result.tests_verified)
        self.assertFalse(result.transcript["verified"])
        verify = unittest.mock.Mock(side_effect=[unavailable, {"kind": "typecheck", "status": "failed"}])
        replies = [*replies[:2], call(3, "run_tests"), call(4, "finish")]
        result = run_agent({"a.ts": "const x=1;"}, "Edit", Script(*replies), verify, lambda *_: None,
                           threading.Event(), AgentBudget(max_model_calls=4), allow_unverified_checks=True, allow_static_checks=True)
        self.assertNotEqual(result.state, "completed")
        self.assertFalse(result.transcript["unverified_completion_allowed"])

    def test_pnpm_monorepo_uses_declared_typecheck_when_tests_are_absent(self):
        files = {"package.json": json.dumps({"scripts": {"typecheck": "pnpm -r --if-present run typecheck", "build": "pnpm build"}}),
                 "pnpm-workspace.yaml": "packages: [apps/*]", "apps/mobile/package.json": '{"scripts":{"typecheck":"tsc --noEmit"}}'}
        checks = detect_local_checks(files)
        self.assertEqual([(c["kind"], c["command"], c["cwd"]) for c in checks], [("typecheck", "pnpm run typecheck", "")])

    def test_nested_tests_are_not_missed_or_replaced_with_static_checks(self):
        files = {"package.json": '{"scripts":{"typecheck":"tsc"}}', "pnpm-lock.yaml": "", "apps/mobile/package.json": '{"scripts":{"test":"vitest"}}'}
        checks = detect_local_checks(files)
        self.assertEqual([(c["kind"], c["cwd"]) for c in checks], [("npm_test", "apps/mobile")])
        self.assertEqual(checks[0]["command"], "pnpm run test")

    def test_root_test_script_owns_orchestration_and_manager_is_respected(self):
        files = {"package.json": '{"packageManager":"yarn@4","scripts":{"test":"workspace tests"}}',
                 "apps/mobile/package.json": '{"scripts":{"test":"vitest"}}'}
        checks = detect_local_checks(files)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["command"], "yarn run test")

    def test_nested_check_executes_in_its_directory_and_preserves_its_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "apps" / "mobile").mkdir(parents=True)
            compiler = root / "apps" / "mobile" / "node_modules" / ".bin" / "tsc"
            compiler.parent.mkdir(parents=True)
            compiler.write_text("mock compiler")
            checks = detect_local_checks({"apps/mobile/package.json": '{"scripts":{"typecheck":"tsc"}}'})
            with patch("dikdik.local_exec.run", return_value={"status": "passed", "exit_code": 0, "runs_on": "this computer"}) as execute:
                result = run_tests(root, checks, threading.Event())
            self.assertEqual(execute.call_args.args[0], (root / "apps/mobile").resolve())
            self.assertEqual(result["kind"], "typecheck")

    def test_absent_checks_and_failure_are_not_success(self):
        self.assertEqual(detect_local_checks({"package.json": "{}"}), [])
        self.assertEqual(run_tests(Path("."), [], threading.Event())["status"], "not_run")
        for status in ("failed", "timed_out", "cancelled"):
            with patch("dikdik.local_exec.run", return_value={"status": status, "exit_code": 1}):
                result = run_tests(Path("."), [{"kind": "typecheck", "label": "typecheck", "command": "npm run typecheck"}], threading.Event())
            self.assertEqual(result["status"], status)

    def test_static_checks_require_host_opt_in_and_are_never_tests(self):
        def verify(_):
            return {"kind": "typecheck", "status": "passed"}
        replies = [call(1, "edit_file", path="app.ts", before="1", after="2"), call(2, "run_tests"), call(3, "finish")]
        result = run_agent({"app.ts": "const x = 1;"}, "Edit", Script(*replies), verify, lambda *_: None,
                           threading.Event(), allow_static_checks=True)
        self.assertEqual(result.state, "completed")
        self.assertFalse(result.tests_verified)
        self.assertEqual(result.transcript["verified_kind"], "checks")
        result = run_agent({"app.ts": "const x = 1;"}, "Edit", Script(*replies), verify, lambda *_: None,
                           threading.Event(), budget=AgentBudget(max_model_calls=2))
        self.assertFalse(result.transcript["verified"])
        self.assertFalse(result.tests_verified)


class ResumeCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.ts").write_text('const API = "https://old.example/api/v1";\n')
        (self.root / "package.json").write_text('{"scripts": {"typecheck": "tsc --noEmit"}}')
        compiler = self.root / "node_modules" / ".bin" / "tsc"
        compiler.parent.mkdir(parents=True)
        compiler.write_text("mock compiler")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.request = {"objective": "Change the API host", "mode": "agent", "verification": "local", "approve_external": True, "approve_execution": True}
        env = patch.dict(os.environ, {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "key"})
        env.start()
        self.addCleanup(env.stop)

    def wait(self, task):
        self.engine.pool.submit(lambda: None).result(timeout=30)
        return self.engine.store.get(task)

    def edit(self):
        return call(1, "edit_file", path="app.ts", before="old.example", after="new.example")

    def passed(self):
        return patch("dikdik.local_exec.run", return_value={"status": "passed", "exit_code": 0, "output": "Types checked", "runs_on": "this computer"})

    def test_typecheck_finishes_without_claiming_tests_passed(self):
        with self.passed(), patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit(), call(2, "run_tests"), call(3, "finish"))):
            data = self.wait(self.engine.submit(self.request))
        self.assertEqual(data["state"], "completed", data["result"])
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(data["result"]["verification_level"], "checks")
        self.assertEqual(data["result"]["changed_files"], ["app.ts"])

    def test_project_without_tests_can_report_an_applied_unverified_edit_when_compiler_is_missing(self):
        (self.root / "node_modules" / ".bin" / "tsc").unlink()
        with patch("dikdik.local_exec.shutil.which", return_value=None), patch("dikdik.local_exec.run") as execute, \
                patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit(), call(2, "run_tests"), call(3, "finish"))):
            data = self.wait(self.engine.submit(self.request))
        execute.assert_not_called()
        self.assertEqual(data["state"], "completed", data["result"])
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(data["result"]["verification_level"], "unavailable")
        self.assertEqual(data["result"]["changed_files"], ["app.ts"])

    def test_resume_preserves_diff_checkpoint_and_undo_without_repeating_the_edit(self):
        profile = self.engine.agents.create({"name": "Two calls", "max_model_calls": 2})
        with self.passed(), patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit(), call(2, "run_tests"))):
            first = self.wait(task := self.engine.submit({**self.request, "agent_profile": profile["id"]}))
        checkpoint = first["result"]["checkpoint"]
        self.assertEqual(first["state"], "paused")
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script(call(3, "finish"))):
            self.engine.resume(task, self.request)
            done = self.wait(task)
        self.assertEqual(done["state"], "completed", done["result"])
        self.assertFalse(done["result"]["tests_verified"])
        self.assertEqual(done["result"]["changed_files"], ["app.ts"])
        self.assertEqual(done["result"]["checkpoint"], checkpoint)
        self.assertIn("old.example", done["result"]["diff"])
        self.assertIn("new.example", done["result"]["diff"])
        self.assertTrue(done["result"]["turn_changed"])
        self.assertTrue(done["result"]["source_modified"])
        self.engine.undo(task)
        self.assertIn("old.example", (self.root / "app.ts").read_text())

    def test_legacy_zero_change_resume_recovers_prior_checkpoint(self):
        profile = self.engine.agents.create({"name": "One call", "max_model_calls": 1})
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit())):
            first = self.wait(task := self.engine.submit({**self.request, "agent_profile": profile["id"]}))
        path = self.engine._transcript_path(task)
        transcript = json.loads(path.read_text())
        transcript.pop("direct_checkpoint", None)
        transcript.pop("turn_originals", None)
        path.write_text(json.dumps(transcript))
        self.engine.store.state(task, "blocked", {"direct": True, "checkpoint": None, "changed_files": [], "turn_changed": False})
        with self.passed(), patch("dikdik.engine.CompatibleAgentModel", return_value=Script(call(2, "run_tests"), call(3, "finish"))):
            self.engine.resume(task, self.request)
            done = self.wait(task)
        self.assertEqual(done["state"], "completed", done["result"])
        self.assertEqual(done["result"]["checkpoint"], first["result"]["checkpoint"])
        self.assertEqual(done["result"]["changed_files"], ["app.ts"])

    def test_removing_a_required_check_does_not_bypass_verification(self):
        remove = call(1, "edit_file", path="package.json", before='"typecheck": "tsc --noEmit"', after='"dev": "server"')
        with patch("dikdik.local_exec.run") as execute, patch("dikdik.engine.CompatibleAgentModel", return_value=Script(remove, call(2, "run_tests"))):
            data = self.wait(self.engine.submit(self.request))
        execute.assert_not_called()
        self.assertEqual(data["state"], "blocked")
        self.assertIn("check disappeared", data["result"]["error"])

    def test_resume_cannot_reclassify_a_removed_required_test_as_no_tests(self):
        (self.root / "package.json").write_text('{"scripts": {"test": "node tests.js"}}')
        remove = call(1, "edit_file", path="package.json", before='"test": "node tests.js"', after='"dev": "server"')
        with patch("dikdik.local_exec.run") as execute, patch("dikdik.engine.CompatibleAgentModel", return_value=Script(remove, call(2, "run_tests"))):
            first = self.wait(task := self.engine.submit(self.request))
        self.assertEqual(first["state"], "blocked")
        with patch("dikdik.local_exec.run") as execute, patch("dikdik.engine.CompatibleAgentModel", return_value=Script(call(3, "run_tests"))):
            self.engine.resume(task, self.request)
            second = self.wait(task)
        execute.assert_not_called()
        self.assertEqual(second["state"], "blocked")
        self.assertIn("check disappeared", second["result"]["error"])

    def test_outside_edits_invalidate_cached_checks_even_if_resume_only_finishes(self):
        profile = self.engine.agents.create({"name": "Two calls", "max_model_calls": 2})
        with self.passed(), patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit(), call(2, "run_tests"))):
            self.wait(task := self.engine.submit({**self.request, "agent_profile": profile["id"]}))
        (self.root / "app.ts").write_text("// external edit\n")
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script(call(3, "finish"))):
            self.engine.resume(task, self.request)
            data = self.wait(task)
        self.assertEqual(data["state"], "blocked")
        self.assertFalse(data["result"]["tests_verified"])
        self.assertEqual(data["result"]["verification_level"], "not_verified")
        self.assertEqual((self.root / "app.ts").read_text(), "// external edit\n")

    def test_external_change_after_pause_is_not_overwritten_or_verified(self):
        profile = self.engine.agents.create({"name": "One call", "max_model_calls": 1})
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Script(self.edit())):
            first = self.wait(task := self.engine.submit({**self.request, "agent_profile": profile["id"]}))
        (self.root / "app.ts").write_text("// external user edit\n")
        with patch("dikdik.local_exec.run") as execute, patch("dikdik.engine.CompatibleAgentModel", return_value=Script(call(2, "run_tests"), ModelResponse("Conflict", []))):
            self.engine.resume(task, self.request)
            done = self.wait(task)
        execute.assert_not_called()
        self.assertEqual((self.root / "app.ts").read_text(), "// external user edit\n")
        self.assertFalse(done["result"]["tests_verified"])
        self.assertIn("app.ts", done["result"]["conflicts"])


if __name__ == "__main__":
    unittest.main()
