import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik.sandbox import RESULT_PREFIX, STARTED, _supervisor_program, _test_program, docker_command, run_tests


NONCE = "0" * 32


def receipt(**changes):
    counts = {"discovered": 1, "ran": 1, "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0, **changes}
    return RESULT_PREFIX + NONCE + ":" + json.dumps(counts) + "\n"


class SandboxTests(unittest.TestCase):
    def setUp(self):
        nonce = patch("dikdik.sandbox.secrets.token_hex", return_value=NONCE)
        nonce.start()
        self.addCleanup(nonce.stop)

    def test_command_has_required_boundaries(self):
        command = docker_command(Path("candidate"), "dikdik-test")
        for value in ["--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m", "--cpus=1", "--user=65534:65534", "--pull=never"]:
            self.assertIn(value, command)
        self.assertNotIn("--privileged", command)
        self.assertIn("readonly", command[command.index("--mount") + 1])
        self.assertIn("n and result.wasSuccessful() and not result.skipped", command[-1])

    def test_missing_docker_is_blocked(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            result = run_tests(Path("candidate"), threading.Event())
        self.assertEqual(result["status"], "blocked")

    def test_daemon_failure_is_blocked_and_cleanup_recorded(self):
        proc = Mock(stdout=io.BytesIO(b"daemon unavailable"), returncode=125)
        proc.poll.return_value = 125
        with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=1, stderr=b"daemon unavailable")) as cleanup:
            result = run_tests(Path("candidate"), threading.Event())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["exit_code"], 125)
        self.assertIsNotNone(result["cleanup_warning"])
        self.assertEqual(cleanup.call_args.args[0][:3], ["docker", "rm", "-f"])

    def test_timeout_cleans_container_and_kills_client(self):
        proc = Mock(stdout=io.BytesIO(b""), returncode=-1)
        proc.poll.return_value = None
        with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)), patch("time.monotonic", side_effect=[0, 2, 3]):
            result = run_tests(Path("candidate"), threading.Event(), timeout=1)
        self.assertEqual(result["status"], "timed_out")
        proc.kill.assert_called_once()

    def test_cancellation_cleans_container(self):
        cancel = threading.Event()
        cancel.set()
        proc = Mock(stdout=io.BytesIO(b""), returncode=-1)
        proc.poll.return_value = None
        with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)):
            result = run_tests(Path("candidate"), cancel)
        self.assertEqual(result["status"], "cancelled")

    def test_output_is_bounded(self):
        proc = Mock(stdout=io.BytesIO(b"x" * 100000), returncode=0)
        proc.poll.return_value = 0
        with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)):
            result = run_tests(Path("candidate"), threading.Event())
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(result["output"]), 65536)
        self.assertNotEqual(result["status"], "passed")

    def test_windows_daemon_exit_one_is_blocked_not_test_failed(self):
        proc = Mock(stdout=io.BytesIO(b"failed to connect to the docker API"), returncode=1)
        proc.poll.return_value = 1
        with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=1, stderr=b"daemon unavailable")):
            result = run_tests(Path("candidate"), threading.Event())
        self.assertEqual(result["status"], "blocked")

    def test_started_container_exit_status_is_classified(self):
        for code, expected in [(0, "passed"), (1, "failed")]:
            proc = Mock(stdout=io.BytesIO((STARTED + "\n" + receipt()).encode()), returncode=code)
            proc.poll.return_value = code
            with self.subTest(code=code), patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)):
                result = run_tests(Path("candidate"), threading.Event())
            self.assertEqual(result["status"], expected)

    def test_exit_zero_without_a_completed_test_receipt_is_not_a_pass(self):
        for text in [STARTED, STARTED + "\n" + receipt(ran=0, discovered=0), STARTED + "\n" + receipt(skipped=1), STARTED + "\n" + receipt().replace(NONCE, "wrong-nonce")]:
            proc = Mock(stdout=io.BytesIO(text.encode()), returncode=0)
            proc.poll.return_value = 0
            with self.subTest(text=text), patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)):
                result = run_tests(Path("candidate"), threading.Event())
            self.assertEqual(result["status"], "failed")

    def test_actual_trusted_supervisor_detects_discovery_exit_zero(self):
        # Controlled QA fixtures only. Product repository execution remains Docker-only.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "test_app.py").write_text("import unittest,app\nclass TestApp(unittest.TestCase):\n def test_value(self): self.assertEqual(app.value,1)\n")
            for source, expected in [("value = 1\n", "passed"), ("import os; os._exit(0)\n", "failed")]:
                (root / "app.py").write_text(source)
                program = _supervisor_program(_test_program(str(root), NONCE))
                completed = subprocess.run([sys.executable, "-I", "-B", "-c", program], capture_output=True, timeout=10)
                self.assertEqual(completed.returncode, 0)
                proc = Mock(stdout=io.BytesIO(completed.stdout + completed.stderr), returncode=completed.returncode)
                proc.poll.return_value = completed.returncode
                with patch("subprocess.Popen", return_value=proc), patch("subprocess.run", return_value=Mock(returncode=0)):
                    result = run_tests(root, threading.Event())
                self.assertEqual(result["status"], expected, result)


@unittest.skipUnless(os.environ.get("DIKDIK_RUN_DOCKER") == "1", "Set DIKDIK_RUN_DOCKER=1 with Linux Docker and the preloaded Python image")
class LiveDockerTests(unittest.TestCase):
    def test_real_test_run_and_readonly_network_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            # Actual repository code runs only in Docker, never on the host.
            (path / "test_boundary.py").write_text(
                "import os,socket,unittest\n"
                "class Boundary(unittest.TestCase):\n"
                " def test_nonroot(self): self.assertNotEqual(os.getuid(),0)\n"
                " def test_readonly(self):\n"
                "  with self.assertRaises(OSError): open('/workspace/escape','w')\n"
                " def test_host_secret_absent(self): self.assertFalse(os.path.exists('/host'))\n"
                " def test_network(self):\n"
                "  with self.assertRaises(OSError): socket.create_connection(('1.1.1.1',443),timeout=1)\n"
            )
            result = run_tests(path, threading.Event())
            self.assertEqual(result["status"], "passed", result)
