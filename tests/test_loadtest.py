"""Load tests drive a real server; repetition classifies stable, failing and flaky commands."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import engineering, loadtest

SERVER = '''
import sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(0.05)
        status = 500 if self.path == "/fail" else 200
        self.send_response(status); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


class ValidationTests(unittest.TestCase):
    def test_arguments(self):
        for bad in ({"command": "serve"}, {"command": "s {port}", "path": "http://evil.example/"}, {"command": "s {port}", "path": "//x"},
                    {"command": "s {port}", "method": "TRACE"}, {"command": "s {port}", "requests": 0},
                    {"command": "s {port}", "concurrency": 51}, {"command": "s {port}", "requests": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                loadtest.validate(bad)
        self.assertEqual(loadtest.validate({"command": "s {port}"})["method"], "GET")


class RealServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "server.py").write_text(SERVER, encoding="utf-8")
        cls.command = f'"{Path(sys.executable).as_posix()}" server.py {{port}}'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def load(self, **arguments):
        return loadtest.run(self.root, {"command": self.command, **arguments}, threading.Event())

    def test_a_healthy_endpoint(self):
        result = self.load(path="/ok", requests=120, concurrency=8)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual((result["sent"], result["completed"], result["status_counts"]), (120, 120, {"200": 120}))
        self.assertGreater(result["throughput_rps"], 0)
        self.assertLessEqual(result["p50_ms"], result["p95_ms"])
        self.assertLessEqual(result["p95_ms"], result["p99_ms"])

    def test_server_errors_count_against_the_error_budget(self):
        result = self.load(path="/fail", requests=20, concurrency=4)
        self.assertEqual((result["status"], result["error_rate"], result["status_counts"]), ("failed", 1.0, {"500": 20}))
        self.assertEqual(result["results"][-1]["status"], "ERRORS")

    def test_latency_budget(self):
        result = self.load(path="/slow", requests=20, concurrency=4, p95_budget_ms=5)
        self.assertGreaterEqual(result["p95_ms"], 50)  # the sleep is really inside the measurement
        self.assertEqual(result["results"][0]["status"], "OVER_BUDGET")
        self.assertEqual(self.load(path="/slow", requests=10, concurrency=5, p95_budget_ms=5000)["status"], "passed")

    def test_a_server_that_never_listens_fails_cleanly(self):
        quiet = f'"{Path(sys.executable).as_posix()}" -c "import sys; sys.exit(3)" {{port}}'
        result = loadtest.run(self.root, {"command": quiet}, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("exited before becoming ready", result["error"])


class RepeatTests(unittest.TestCase):
    def runner(self, statuses):
        outcomes = iter(statuses)
        return lambda files, command, timeout: {"status": next(outcomes), "exit_code": 0, "output": "", "changed": {}}

    def test_classification(self):
        self.assertEqual(engineering.repeat(self.runner(["passed"] * 3), {}, {"command": "t", "runs": 3})["verdict"], "STABLE_PASS")
        self.assertEqual(engineering.repeat(self.runner(["failed"] * 2), {}, {"command": "t", "runs": 2})["verdict"], "STABLE_FAIL")
        flaky = engineering.repeat(self.runner(["passed", "failed", "passed"]), {}, {"command": "t", "runs": 3})
        self.assertEqual((flaky["verdict"], flaky["passed"]), ("FLAKY", 2))
        self.assertIn("output_tail", flaky["outcomes"][1])
        writer = lambda *a: {"status": "passed", "changed": {"x": "y"}}
        with self.assertRaises(ValueError):
            engineering.repeat(writer, {}, {"command": "t", "runs": 2})

    def test_stability_gate(self):
        flaky = engineering.repeat(self.runner(["passed", "failed"]), {}, {"command": "t", "runs": 2})
        report = engineering.release_gate([], [], False, False, {"a.py": "2"}, {"a.py": "1"}, stability=flaky)
        self.assertEqual(next(g for g in report["gates"] if g["gate"] == "stability")["status"], "RISK")


if __name__ == "__main__":
    unittest.main()
