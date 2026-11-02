import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    def __init__(self, cwd, *extra):
        self.port = free_port()
        self.process = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--port", str(self.port), *extra],
                                        cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("server.py exited during start-up")
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise AssertionError("server.py did not listen on its --port")

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, kind, text = response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as error:
            status, kind, text = error.code, error.headers.get("Content-Type", ""), error.read()
        if status == 204:
            return status, None
        assert kind.startswith("application/json"), f"{method} {path} answered {kind!r}, not JSON"
        return status, json.loads(text.decode("utf-8"))

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


class Oracle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.data = self.dir / "store.json"
        self.server = Server(self.dir, "--data", str(self.data))
        self.addCleanup(lambda: self.server.stop())

    def test_create_read_update_delete(self):
        status, note = self.server.call("POST", "/notes", {"title": "Groceries", "body": "milk"})
        self.assertEqual(status, 201)
        self.assertIsInstance(note["id"], int)
        self.assertEqual((note["title"], note["body"]), ("Groceries", "milk"))
        self.assertIn("created", note)
        self.assertEqual(self.server.call("GET", f"/notes/{note['id']}"), (200, note))
        status, updated = self.server.call("PUT", f"/notes/{note['id']}", {"body": "milk, eggs"})
        self.assertEqual((status, updated["title"], updated["body"]), (200, "Groceries", "milk, eggs"))
        self.assertEqual(self.server.call("GET", f"/notes/{note['id']}")[1]["body"], "milk, eggs")
        self.assertEqual(self.server.call("DELETE", f"/notes/{note['id']}"), (204, None))
        self.assertEqual(self.server.call("GET", f"/notes/{note['id']}")[0], 404)
        self.assertEqual(self.server.call("GET", "/notes"), (200, []))

    def test_body_defaults_to_empty_and_unicode_round_trips(self):
        status, note = self.server.call("POST", "/notes", {"title": "Café ✓ 日本"})
        self.assertEqual((status, note["title"], note["body"]), (201, "Café ✓ 日本", ""))

    def test_list_is_newest_first(self):
        ids = [self.server.call("POST", "/notes", {"title": f"n{i}"})[1]["id"] for i in range(3)]
        status, listed = self.server.call("GET", "/notes")
        self.assertEqual(status, 200)
        self.assertEqual([n["id"] for n in listed], list(reversed(ids)))
        self.assertEqual([n["title"] for n in listed], ["n2", "n1", "n0"])

    def test_invalid_input_is_400_with_an_error(self):
        for body in ({}, {"title": ""}, {"title": 7}, {"title": "x" * 201}, {"title": "ok", "body": 5}):
            with self.subTest(body=body):
                status, answer = self.server.call("POST", "/notes", body)
                self.assertEqual(status, 400)
                self.assertIsInstance(answer.get("error"), str)
        status, answer = self.server.call("POST", "/notes", raw=b"{not json")
        self.assertEqual((status, isinstance(answer.get("error"), str)), (400, True))
        self.assertEqual(self.server.call("POST", "/notes", {"title": "x" * 200})[0], 201)
        note = self.server.call("POST", "/notes", {"title": "keep"})[1]
        self.assertEqual(self.server.call("PUT", f"/notes/{note['id']}", {"title": ""})[0], 400)
        self.assertEqual(self.server.call("GET", f"/notes/{note['id']}")[1]["title"], "keep")

    def test_unknown_notes_are_404(self):
        for method, body in (("GET", None), ("PUT", {"title": "x"}), ("DELETE", None)):
            with self.subTest(method=method):
                self.assertEqual(self.server.call(method, "/notes/999", body)[0], 404)
        self.assertEqual(self.server.call("GET", "/notes/abc")[0], 404)

    def test_notes_survive_a_restart_and_ids_are_not_reused(self):
        first = self.server.call("POST", "/notes", {"title": "first"})[1]
        second = self.server.call("POST", "/notes", {"title": "second"})[1]
        self.server.call("DELETE", f"/notes/{second['id']}")
        self.server.stop()
        self.server = Server(self.dir, "--data", str(self.data))
        self.assertEqual(self.server.call("GET", "/notes"), (200, [first]))
        third = self.server.call("POST", "/notes", {"title": "third"})[1]
        self.assertNotIn(third["id"], (first["id"], second["id"]))

    def test_default_data_file_is_in_the_current_folder(self):
        with tempfile.TemporaryDirectory() as other:
            server = Server(other)
            try:
                self.assertEqual(server.call("POST", "/notes", {"title": "here"})[0], 201)
            finally:
                server.stop()
            self.assertTrue((Path(other) / "notes.json").is_file())


if __name__ == "__main__":
    unittest.main()
