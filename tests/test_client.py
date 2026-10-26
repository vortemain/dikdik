import io
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.client import DikdikClient, DikdikError, main
from dikdik.engine import Engine
from dikdik.server import make_server, session_token

EDIT = [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_bytes(b"x = 1\n")
        self.data = base / "data"
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        token = session_token(self.data / "web.token")
        self.server = make_server(self.engine, 0, token)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = DikdikClient(self.url, data=self.data)

    def test_submit_follow_stream_apply(self):
        task = self.client.submit("Change x", mode="offline", edits=EDIT)
        events = list(self.client.events(task))
        self.assertEqual(events[-1], ("end", {"state": "completed"}))
        self.assertIn("completed", [data["kind"] for kind, data in events if kind == "task"])
        self.assertFalse(self.client.send(task, "Again", mode="offline", edits=[{"path": "app.py", "before": "x = 2", "after": "x = 3"}]))
        result = self.client.wait(task, timeout=20)
        self.assertIn("+x = 3", result["result"]["diff"])
        self.client.rename(task, "Bump x")
        self.assertEqual(self.client.tasks()[0]["title"], "Bump x")
        self.assertEqual(self.client.apply(task)["applied"], ["app.py"])
        self.assertEqual((self.root / "app.py").read_bytes(), b"x = 3\n")
        self.assertEqual(self.client.project()["name"], "repo")
        self.assertFalse(self.client.model()["configured"])

    def test_errors_are_readable(self):
        with self.assertRaises(DikdikError) as caught:
            self.client.submit("x", mode="agent")  # no approval to share code
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("disclosure approval", str(caught.exception))
        with self.assertRaises(DikdikError) as caught:
            DikdikClient(self.url, token="wrong").tasks()
        self.assertEqual(caught.exception.status, 401)
        with self.assertRaises(DikdikError) as caught:
            DikdikClient("http://127.0.0.1:9", token="t").tasks()
        self.assertIn("not running", str(caught.exception))

    def test_command_line(self):
        out = io.StringIO()
        code = main(["--url", self.url, "--data", str(self.data), "ask", "Change x", "--mode", "model"], out=io.StringIO())
        self.assertEqual(code, 1)  # model mode without --share-code is refused
        task = self.client.submit("Change x", mode="offline", edits=EDIT)
        self.client.wait(task, timeout=20)
        self.assertEqual(main(["--url", self.url, "--data", str(self.data), "tasks"], out=out), 0)
        self.assertIn(task[:8], out.getvalue())
        out = io.StringIO()
        main(["--url", self.url, "--data", str(self.data), "show", task], out=out)
        self.assertIn("+x = 2", out.getvalue())
        self.assertIn("changed: app.py", out.getvalue())
        out = io.StringIO()
        self.assertEqual(main(["--url", self.url, "--data", str(self.data), "apply", task], out=out), 2)
        self.assertIn("--yes", out.getvalue())
        out = io.StringIO()
        self.assertEqual(main(["--url", self.url, "--data", str(self.data), "apply", task, "--yes"], out=out), 0)
        self.assertIn("applied: app.py", out.getvalue())
