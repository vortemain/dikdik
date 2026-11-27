"""Benchmark tasks have known outcomes, and the harness judges by hidden oracles, not agent claims."""

import functools
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_tasks", ROOT / "scripts" / "benchmark_tasks.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
TASKS = sorted(p for p in bench.TASKS.iterdir() if p.is_dir())


@functools.cache
def chromium() -> bool:
    """Whether the todo-web oracle can launch Chromium here: the Playwright package alone is not enough."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
    except Error:
        return False
    return True


# Plausible-but-wrong builds of the build-from-scratch tasks: each must fail its oracle.
BROKEN = {
    "notes-api": ("server.py", "if not isinstance(title, str) or not title or len(title) > 200:",
                  "if not isinstance(title, str) or not title:"),  # no title length limit
    "todo-cli": ("todo.py", 'task = {"id": data["next_id"],',
                 'task = {"id": max([t["id"] for t in data["tasks"]], default=0) + 1,'),  # reuses ids
    "todo-web": ("app.js", "function save() {\n  localStorage.setItem(KEY, JSON.stringify(todos));\n}",
                 "function save() {}"),  # forgets everything on reload
}


def solved(task, fixture):
    shutil.copytree(task / "repo", fixture)
    shutil.copytree(task / "solution", fixture, dirs_exist_ok=True)


class TaskValidityTests(unittest.TestCase):
    def test_every_oracle_detects_the_bug_and_accepts_the_reference_solution(self):
        self.assertEqual({t.name for t in TASKS}, {"repair", "pagination", "validation", "sql-injection",
                                                   "notes-api", "todo-cli", "todo-web", "parallel-fix"})
        for task in TASKS:
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as temp:
                fixture = Path(temp) / "fixture"
                shutil.copytree(task / "repo", fixture)
                self.assertFalse(bench.oracle(fixture, task)["passed"], "the oracle must fail on the unfixed fixture")
                if task.name == "todo-web" and not chromium():
                    continue  # its oracle needs Playwright Chromium (it fails closed without it, as just shown)
                shutil.copytree(task / "solution", fixture, dirs_exist_ok=True)
                self.assertTrue(bench.oracle(fixture, task)["passed"], bench.oracle(fixture, task).get("output_tail"))

    def test_build_from_scratch_tasks_start_empty_and_reject_broken_builds(self):
        for name, (path, old, new) in BROKEN.items():
            task = bench.TASKS / name
            self.assertEqual([p.name for p in (task / "repo").iterdir()], ["README.md"])  # nothing to build on
            if name == "todo-web" and not chromium():
                continue
            with self.subTest(task=name), tempfile.TemporaryDirectory() as temp:
                fixture = Path(temp) / "fixture"
                solved(task, fixture)
                text = (fixture / path).read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1)
                (fixture / path).write_text(text.replace(old, new), encoding="utf-8")
                self.assertFalse(bench.oracle(fixture, task)["passed"], "a broken build must fail the oracle")

    def test_the_oracle_stops_what_its_tests_leave_running(self):
        # An oracle whose set-up fails after starting its server never reaches tearDownClass: the server outlives
        # the oracle, keeps the judged folder open (Windows refuses to remove it) and holds its port.
        leaky = ("import os, subprocess, sys, time, unittest\n"
                 "from pathlib import Path\n"
                 "SERVER = ('import socket, sys, time\\n'\n"
                 "          's = socket.socket(); s.bind((\"127.0.0.1\", 0)); s.listen()\\n'\n"
                 "          'open(sys.argv[1], \"w\").write(str(s.getsockname()[1]))\\n'\n"
                 "          'time.sleep(600)\\n')\n"
                 "class Oracle(unittest.TestCase):\n"
                 "    def test_server(self):\n"
                 "        marker = Path(os.environ['DIKDIK_TEST_MARKER'])\n"
                 "        subprocess.Popen([sys.executable, '-c', SERVER, str(marker)], cwd=Path(__file__).parent,\n"
                 "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                 "        deadline = time.monotonic() + 20\n"
                 "        while not marker.exists() or not marker.read_text():\n"
                 "            self.assertLess(time.monotonic(), deadline)\n"
                 "            time.sleep(0.05)\n")
        with tempfile.TemporaryDirectory() as temp:
            task, workspace, marker = Path(temp) / "task", Path(temp) / "workspace", Path(temp) / "port"
            (task / "oracle").mkdir(parents=True)
            (task / "oracle" / "test_oracle.py").write_text(leaky, encoding="utf-8")
            workspace.mkdir()
            with patch.dict(os.environ, {"DIKDIK_TEST_MARKER": str(marker)}):
                result = bench.oracle(workspace, task)
            self.assertTrue(result["passed"], result.get("output_tail"))
            with self.assertRaises(OSError, msg="the oracle's server is still running"):
                socket.create_connection(("127.0.0.1", int(marker.read_text())), timeout=2).close()

    def test_ui_tasks_let_the_agent_use_the_browser(self):
        seen = []

        class Engine:
            def __init__(self, *args):
                self.store = self

            def set_trusted(self, value):
                pass

            def submit(self, request):
                seen.append(request)
                return "t"

            def status(self, ident):
                return "completed"

            def get(self, ident, include_events=False):
                return {"state": "completed", "result": {}, "events": []}

            def close(self):
                pass

        with patch.object(bench, "Engine", Engine), patch.object(bench, "oracle", return_value={"passed": False}):
            bench.run_task(bench.TASKS / "todo-web")
            bench.run_task(bench.TASKS / "todo-cli")
        self.assertEqual([r.get("approve_browser") for r in seen], [True, None])

        # A passing oracle is not a success when the agent wrote outside its workspace (here: "Dikdik's checkout").
        with tempfile.TemporaryDirectory() as checkout:
            (Path(checkout) / "engine.py").write_text("x = 1\n", encoding="utf-8")

            class Escaping(Engine):
                def submit(self, request):
                    (Path(checkout) / "engine.py").write_text("x = 22\n", encoding="utf-8")
                    return "t"

            with patch.object(bench, "Engine", Escaping), patch.object(bench, "oracle", return_value={"passed": True}), \
                    patch.object(bench, "watched", return_value=[Path(checkout)]):
                result = bench.run_task(bench.TASKS / "todo-cli")
        self.assertEqual(result["outside_changes"], [str(Path(checkout) / "engine.py")])
        self.assertTrue(result["oracle_passed"])
        self.assertFalse(result["success"])

    def test_version_changes_with_task_content(self):
        before = bench.version(TASKS)
        self.assertEqual(before, bench.version(TASKS))
        self.assertRegex(before, r"^[0-9a-f]{16}$")


class HarnessTests(unittest.TestCase):
    """The harness end to end through the real HTTP provider path, against the scripted stand-in."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.stub = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "stub_model.py"), "--port", str(cls.port),
                                     "--key", "sk-bench", "--delay", "0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.stub.terminate()
        cls.stub.wait(timeout=10)

    def run_task(self, name):
        env = {"DIKDIK_MODEL_URL": f"http://127.0.0.1:{self.port}/v1", "DIKDIK_MODEL": "stub-model", "DIKDIK_API_KEY": "sk-bench"}
        # Outside-write detection has its own test; here a developer editing the checkout must not fail the run.
        with patch.dict(os.environ, env), patch.object(bench, "watched", return_value=[]):
            return bench.run_task(bench.TASKS / name, timeout=120)

    def test_a_solved_task_passes_its_hidden_oracle(self):
        result = self.run_task("repair")
        self.assertEqual((result["state"], result["oracle_passed"], result["success"]), ("completed", True, True))
        self.assertIn("run_tests", [step.get("tool") for step in result["timeline"]])  # diagnosable afterwards

    def test_an_agent_claim_does_not_count_without_the_oracle(self):
        # The stand-in cannot fix pagination; the visible test still passes, so the agent claims success.
        result = self.run_task("pagination")
        self.assertTrue(result["agent_claimed_tests_verified"])
        self.assertFalse(result["oracle_passed"])
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
