import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.runners import IMAGES, STARTED, container_command, detect_runners, run_container, run_project_tests, run_shell


class FakeProcess:
    def __init__(self, output: bytes, code: int):
        self.stdout, self.returncode = io.BytesIO(output), code

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode


class DetectionTests(unittest.TestCase):
    def test_detects_each_stack(self):
        self.assertEqual([r["kind"] for r in detect_runners({"app.py": "", "test_app.py": ""})], ["unittest"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": '{"scripts": {"test": "bun test"}}'})], ["bun_test"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": "{}", "bun.lock": ""})], ["bun_test"])
        self.assertEqual([r["kind"] for r in detect_runners({"package.json": '{"scripts": {"test": "vitest"}}'})], ["npm_test"])
        self.assertEqual(detect_runners({"package.json": "{}"}), [])  # no test script, no bun
        self.assertEqual([r["kind"] for r in detect_runners({"Cargo.toml": "[package]"})], ["cargo_test"])
        mixed = detect_runners({"Cargo.toml": "", "package.json": "{}", "bunfig.toml": "", "tests/test_x.py": ""})
        self.assertEqual([r["kind"] for r in mixed], ["unittest", "bun_test", "cargo_test"])
        self.assertEqual(detect_runners({"package.json": "not json"}), [])


class ContainerTests(unittest.TestCase):
    def test_containers_are_locked_down(self):
        command = container_command(Path("/w"), "dikdik-" + "a" * 32, IMAGES["bun"], ["bun", "test"])
        for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534", "--pull=never"):
            self.assertIn(flag, command)
        self.assertIn(f"type=bind,source={Path('/w')},target=/workspace,readonly", command)
        writable = container_command(Path("/w"), "dikdik-" + "a" * 32, IMAGES["python"], ["true"], writable=True)
        self.assertIn(f"type=bind,source={Path('/w')},target=/workspace", writable)

    def run_fake(self, output, code, cancel=None):
        with patch("dikdik.runners.subprocess.Popen", return_value=FakeProcess(output, code)), \
                patch("dikdik.runners.cleanup_container", return_value=None):
            return run_container(["docker"], "dikdik-" + "b" * 32, cancel or threading.Event(), 5)

    def test_status_comes_from_the_marker_and_exit_code(self):
        passed = self.run_fake(f"{STARTED}\n3 pass\n".encode(), 0)
        self.assertEqual((passed["status"], passed["output"]), ("passed", "3 pass\n"))
        self.assertEqual(self.run_fake(f"{STARTED}\n1 fail\n".encode(), 1)["status"], "failed")
        self.assertEqual(self.run_fake(b"Unable to find image\n", 125)["status"], "blocked")
        self.assertEqual(self.run_fake(b"docker: error\n", 1)["status"], "blocked")  # never started
        with patch("dikdik.runners.subprocess.Popen", side_effect=OSError):
            self.assertEqual(run_container(["docker"], "x", threading.Event(), 5)["status"], "blocked")

    def test_container_stream_callback_excludes_internal_start_marker(self):
        chunks = []
        with patch("dikdik.runners.subprocess.Popen", return_value=FakeProcess(f"{STARTED}\nhello\n".encode(), 0)), \
                patch("dikdik.runners.cleanup_container", return_value=None):
            result = run_container(["docker"], "dikdik-stream", threading.Event(), 5, on_chunk=chunks.append)
        self.assertEqual(result["status"], "passed")
        self.assertEqual("".join(chunks), "hello\n")

    def test_project_tests_combine_every_suite(self):
        files = {"test_a.py": "", "package.json": '{"scripts": {"test": "bun test"}}'}
        with patch("dikdik.runners.run_python_tests", return_value={"kind": "unittest", "status": "passed", "output": "ok"}), \
                patch("dikdik.runners.run_container", return_value={"status": "failed", "output": "1 fail"}) as container:
            result = run_project_tests(Path("/snap"), files, threading.Event())
        self.assertEqual((result["kind"], result["status"]), ("tests", "failed"))
        self.assertEqual([run["label"] for run in result["runs"]], ["Python unittest", "bun test"])
        command = container.call_args.args[0]
        self.assertEqual(command[command.index("timeout"):command.index("timeout") + 4], ["timeout", "-k", "5", "125"])
        self.assertEqual(run_project_tests(Path("/s"), {"a.txt": ""}, threading.Event())["status"], "not_run")


class ShellTests(unittest.TestCase):
    def test_shell_runs_on_a_copy_and_reports_changed_text_files(self):
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp) / "work"
            seen = {}

            def fake_container(command, name, cancel, timeout, limit):
                source = next(part for part in command if part.startswith("type=bind,source="))
                root = Path(source.split("source=")[1].split(",target")[0])
                seen["mount"], seen["command"] = source, command
                (root / "app.py").write_bytes(b"value = 2\n")
                (root / "notes.md").write_bytes(b"# new\n")
                (root / "old.py").unlink()
                return {"status": "passed", "exit_code": 0, "output": "done"}

            files = {"app.py": "value = 1\n", "old.py": "x = 1\n"}
            with patch("dikdik.runners.run_container", side_effect=fake_container):
                result = run_shell(workdir, files, "python fix.py", threading.Event(), image="python", timeout=30)
            self.assertNotIn(",readonly", seen["mount"])
            self.assertIn("sh", seen["command"])
            self.assertEqual(result["changed"], {"app.py": "value = 2\n", "notes.md": "# new\n"})
            self.assertEqual(result["deleted_ignored"], ["old.py"])
            self.assertFalse(workdir.exists())  # the copy is thrown away
            with self.assertRaisesRegex(ValueError, "Image"):
                run_shell(workdir, files, "ls", threading.Event(), image="ubuntu")
            with self.assertRaisesRegex(ValueError, "Command"):
                run_shell(workdir, files, "  ", threading.Event())


class AgentShellTests(unittest.TestCase):
    def test_run_command_keeps_allowed_changes_and_protects_original_tests(self):
        original = {"app.py": "value = 1\n", "tests/test_app.py": "assert True\n"}

        def shell(files, command, timeout, image):
            self.assertEqual((command, timeout, image), ("make fix", 60, "python"))
            return {"status": "passed", "exit_code": 0, "output": "ok",
                    "changed": {"app.py": "value = 2\n", "tests/test_app.py": "assert False\n", "new.py": "x = 1\n", "blocked.py": "y\n"}}

        class Model:
            calls = [
                ModelResponse("", [ToolCall("1", "run_command", {"command": "make fix"})]),
                ModelResponse("", [ToolCall("2", "run_tests", {})]),
                ModelResponse("", [ToolCall("3", "finish", {})]),
            ]
            seen = []

            def respond(self, messages, tools):
                self.seen.append([t["function"]["name"] for t in tools])
                return self.calls.pop(0)

        events = []
        model = Model()
        result = run_agent(original, "Fix", model, lambda files: {"kind": "unittest", "status": "passed"},
                           lambda kind, data: events.append((kind, data)), threading.Event(),
                           creation_policy=lambda path: path == "new.py", shell=shell)
        self.assertIn("run_command", model.seen[0])
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(result.files["app.py"], "value = 2\n")
        self.assertEqual(result.files["new.py"], "x = 1\n")
        self.assertEqual(result.files["tests/test_app.py"], "assert True\n")
        self.assertNotIn("blocked.py", result.files)
        observation = next(d["observation"] for k, d in events if k == "tool" and d["phase"] == "end" and d["name"] == "run_command")
        self.assertEqual(observation["changed_files"], ["app.py", "new.py"])
        self.assertEqual(observation["rejected_changes"], ["blocked.py", "tests/test_app.py"])
