"""Fault injection between two real loopback services: a resilient client passes, fragile ones fail."""

import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dikdik import faults

DEPENDENCY = '''
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''

APP = '''
import sys, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port, dependency, style = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
broken = []
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if broken:
                raise OSError("circuit open forever")
            timeout = None if style == "fragile" else 1
            urllib.request.urlopen(f"http://127.0.0.1:{dependency}/", timeout=timeout).read()
            status = 200
        except Exception:
            if style == "sticky":
                broken.append(True)
            status = 500 if style == "fragile" else 503
        self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


class ValidationTests(unittest.TestCase):
    def test_arguments(self):
        good = {"command": "app {port} {dependency_port}", "dependency_command": "dep {port}"}
        self.assertEqual([s["fault"] for s in faults.validate(good)["scenarios"]], ["latency", "reset", "blackhole", "down"])
        for bad in ({**good, "command": "app {port}"}, {**good, "dependency_command": "dep"}, {**good, "path": "http://evil.example/"},
                    {**good, "scenarios": [{"fault": "meteor"}]}, {**good, "scenarios": [{"fault": "latency", "latency_ms": -1}]},
                    {**good, "scenarios": [{"fault": "reset", "extra": 1}]}, {**good, "max_ms": 10}, {**good, "degraded_statuses": ["503"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                faults.validate(bad)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = socket.socket()
        self.upstream.bind(("127.0.0.1", 0))
        self.upstream.listen(8)
        threading.Thread(target=self.echo, daemon=True).start()
        self.proxy = faults.FaultProxy(self.upstream.getsockname()[1]).start()

    def tearDown(self):
        self.proxy.close()
        self.upstream.close()

    def echo(self):
        while True:
            try:
                conn, _ = self.upstream.accept()
            except OSError:
                return
            data = conn.recv(100)
            conn.sendall(data.upper())
            conn.close()

    def exchange(self, connect_timeout=1):
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=connect_timeout) as sock:
            sock.settimeout(1)
            sock.sendall(b"hello")
            return sock.recv(100)

    def test_modes(self):
        self.assertEqual(self.exchange(), b"HELLO")
        self.proxy.set("latency", 300)
        started = time.monotonic()
        self.assertEqual(self.exchange(), b"HELLO")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.proxy.set("close")
        self.assertEqual(self.exchange(), b"")
        self.proxy.set("reset")
        with self.assertRaises(ConnectionError):
            self.exchange()
        self.proxy.set("blackhole")
        with self.assertRaises(socket.timeout):
            self.exchange()
        self.proxy.set("down")
        with self.assertRaises(ConnectionRefusedError):
            self.exchange(connect_timeout=10)  # Windows retries a refused loopback connect for ~2 s
        self.proxy.set("none")
        self.assertEqual(self.exchange(), b"HELLO")


class RealServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "dependency.py").write_text(DEPENDENCY, encoding="utf-8")
        (cls.root / "app.py").write_text(APP, encoding="utf-8")
        cls.python = Path(sys.executable).as_posix()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_faults(self, style, **extra):
        arguments = {"command": f'"{self.python}" app.py {{port}} {{dependency_port}} {style}',
                     "dependency_command": f'"{self.python}" dependency.py {{port}}', "max_ms": 3000, "recovery_seconds": 3, **extra}
        return faults.run(self.root, arguments, threading.Event())

    def test_a_client_with_timeouts_and_fallbacks_passes_every_fault(self):
        result = self.run_faults("resilient")
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual([s["during"]["status"] for s in result["scenarios"]], [503, 503, 503, 503])  # 1 s client timeout < 2 s latency

    def test_a_client_without_timeouts_fails_the_partition_and_reports_500s(self):
        result = self.run_faults("fragile")
        self.assertEqual(result["status"], "failed")
        by_fault = {s["fault"]: s for s in result["scenarios"]}
        self.assertEqual(by_fault["latency 2000 ms"]["status"], "PASS")
        self.assertEqual(by_fault["reset"]["during"]["status"], 500)
        self.assertIn("not a handled degradation", by_fault["reset"]["problems"][0])
        self.assertIn("no answer within 3000 ms", by_fault["blackhole"]["problems"][0])

    def test_a_circuit_that_never_closes_fails_recovery(self):
        result = self.run_faults("sticky", scenarios=[{"fault": "reset"}])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["scenarios"][0]["during"]["status"], 503)
        self.assertIn("did not recover", result["scenarios"][0]["problems"][0])

    def test_the_application_must_work_before_faults_are_injected(self):
        result = faults.run(self.root, {
            "command": f'"{self.python}" app.py {{port}} {{dependency_port}} fragile',
            "dependency_command": f'"{self.python}" -c "import sys,socket,time; s=socket.socket(); s.bind((\'127.0.0.1\', int(sys.argv[1]))); s.listen(); time.sleep(60)" {{port}}',
            "max_ms": 500, "recovery_seconds": 1}, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("dependency healthy", result["error"])


if __name__ == "__main__":
    unittest.main()
