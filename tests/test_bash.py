import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.bash_service import BashService, CHUNK_SIZE, MAX_INFLIGHT
from dikdik.engine import Engine
from dikdik.server import make_server


def approved_request(approved=True):
    return {
        "objective": "terminal fixture", "mode": "offline", "verification": "docker",
        "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}],
        "approve_execution": approved,
    }


class BashServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.data = base / "repo", base / "data"
        self.root.mkdir()
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create(approved_request())

    @staticmethod
    def fake_shell(_workdir, files, command, cancel, **kwargs):
        assert files["app.py"].replace("\r\n", "\n") == "x = 1\n"
        assert command == "printf hello"
        assert kwargs["name"].startswith("dikdik-") and len(kwargs["name"]) == 39
        kwargs["on_chunk"]("hel")
        kwargs["on_chunk"]("lo\n")
        return {"status": "passed", "exit_code": 0, "output": "hello\n", "changed": {}}

    def test_execute_persists_command_chunks_and_task_stream_events(self):
        with patch("dikdik.bash_service.run_shell", side_effect=self.fake_shell):
            result = self.engine.bash.execute(self.task, {"command": "printf hello", "timeout": 4})
        self.assertEqual((result["exit_code"], result["status"], result["output"]), (0, "passed", "hello\n"))
        events = self.engine.bash.search(self.task, {"limit": "20"})["items"]
        self.assertEqual([event["kind"] for event in events], ["command", "output", "output"])
        self.assertEqual(events[1]["output"], "hello\n")
        self.assertTrue(events[-1]["final"])
        streamed = [event["kind"] for event in self.engine.store.get(self.task)["events"]]
        self.assertEqual(streamed[-3:], ["terminal_command", "terminal_output", "terminal_output"])
        self.assertEqual(self.engine.bash.get(self.task, events[1]["id"])["output"], "hello\n")

    def test_execution_needs_per_task_approval_and_valid_input(self):
        denied = self.engine.store.create(approved_request(False))
        with self.assertRaisesRegex(PermissionError, "explicit"):
            self.engine.bash.start(denied, {"command": "true"})
        for body, message in [({}, "Command"), ({"command": "true", "timeout": 0}, "timeout"),
                              ({"command": "true", "cwd": "../outside"}, "inside")]:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, message):
                self.engine.bash.start(self.task, body)

    def test_pending_command_queue_is_bounded(self):
        with patch.object(self.engine.bash._pool, "submit", return_value=None):
            for index in range(MAX_INFLIGHT):
                self.engine.bash.start(self.task, {"command": f"echo {index}"})
            with self.assertRaisesRegex(ValueError, str(MAX_INFLIGHT)):
                self.engine.bash.start(self.task, {"command": "one too many"})

    def test_large_output_is_chunked_paged_and_reassembled(self):
        output = "x" * (CHUNK_SIZE + 17)
        def large(_workdir, _files, _command, _cancel, **kwargs):
            kwargs["on_chunk"](output)
            return {"status": "passed", "exit_code": 0, "output": output, "changed": {}}
        with patch("dikdik.bash_service.run_shell", side_effect=large):
            result = self.engine.bash.execute(self.task, {"command": "large output"})
        self.assertEqual(result["output"], output)
        events = self.engine.bash.search(self.task, {"kind": "output", "limit": "10"})["items"]
        self.assertEqual([len(event["output"]) for event in events], [CHUNK_SIZE, 17, 0])
        first = self.engine.bash.search(self.task, {"limit": "1"})
        second = self.engine.bash.search(self.task, {"after": str(first["next_after"]), "limit": "1"})
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])

    def test_async_stop_is_scoped_idempotent_and_clear_isolated(self):
        entered = threading.Event()

        def waiting(_workdir, _files, _command, cancel, **_kwargs):
            entered.set()
            cancel.wait(2)
            return {"status": "cancelled", "exit_code": -15, "output": "", "changed": {}}

        other = self.engine.store.create(approved_request())
        with patch("dikdik.bash_service.run_shell", side_effect=waiting):
            command = self.engine.bash.start(self.task, {"command": "sleep 30"})
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.engine.bash.stop(self.task, command["id"])["stop_requested"])
            done = self.engine.bash._done[f"{self.task}:{command['id']}"]
            self.assertTrue(done.wait(5), "cancelled sandbox command did not persist its final event")
        self.assertFalse(self.engine.bash.stop(self.task, command["id"])["stop_requested"])
        self.assertEqual(self.engine.bash.search(other, {})["items"], [])
        self.assertGreater(self.engine.bash.clear(self.task)["cleared_count"], 0)
        self.assertEqual(self.engine.bash.search(self.task, {})["items"], [])
        self.assertFalse(any(event["kind"].startswith("terminal_") for event in self.engine.store.get(self.task)["events"]))

    def test_history_survives_engine_restart(self):
        with patch("dikdik.bash_service.run_shell", side_effect=self.fake_shell):
            self.engine.bash.execute(self.task, {"command": "printf hello"})
        self.engine.bash.close()
        self.engine.bash = BashService(self.engine)
        events = self.engine.bash.search(self.task, {})["items"]
        self.assertEqual((events[0]["command"], events[-1]["status"]), ("printf hello", "passed"))

    def test_task_delete_refuses_active_command_then_purges_history(self):
        entered = threading.Event()

        def waiting(_workdir, _files, _command, cancel, **_kwargs):
            entered.set()
            cancel.wait(2)
            return {"status": "cancelled", "exit_code": -15, "output": "", "changed": {}}

        with patch("dikdik.bash_service.run_shell", side_effect=waiting):
            command = self.engine.bash.start(self.task, {"command": "sleep 30"})
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(ValueError, "terminal commands"):
                self.engine.delete(self.task)
            self.engine.bash.stop(self.task, command["id"])
            self.assertTrue(self.engine.bash._done[f"{self.task}:{command['id']}"].wait(5))
        self.engine.delete(self.task)
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bash_events WHERE task_id=?", (self.task,)).fetchone()[0], 0)
        with self.assertRaises(KeyError):
            self.engine.store.get(self.task)

    def test_agent_shell_is_mirrored_live_into_terminal_history(self):
        class Model:
            replies = [
                ModelResponse("", [ToolCall("shell", "run_command", {"command": "printf live"})]),
                ModelResponse("", [ToolCall("tests", "run_tests", {})]),
                ModelResponse("", [ToolCall("finish", "finish", {})]),
            ]
            def respond(self, _messages, _tools):
                return self.replies.pop(0)

        def shell(_workdir, _files, command, _cancel, **kwargs):
            self.assertEqual(command, "printf live")
            kwargs["on_chunk"]("live\n")
            return {"status": "passed", "exit_code": 0, "output": "live\n",
                    "changed": {"app.py": "x = 2\n"}, "deleted_ignored": []}

        with patch("dikdik.engine.CompatibleAgentModel", return_value=Model()), \
                patch("dikdik.engine.run_shell", side_effect=shell), \
                patch("dikdik.engine.run_tests", return_value={"kind": "unittest", "status": "passed"}):
            task = self.engine.submit({"objective": "change x", "mode": "agent", "verification": "docker",
                                       "approve_execution": True, "approve_external": True})
            for _ in range(300):
                data = self.engine.store.get(task)
                if data["state"] in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
                    break
                time.sleep(.01)
        self.assertEqual(data["state"], "completed", data.get("result"))
        terminal = [event for event in data["events"] if event["kind"].startswith("terminal_")]
        self.assertEqual([event["kind"] for event in terminal], ["terminal_command", "terminal_output", "terminal_output"])
        self.assertEqual((terminal[1]["data"]["output"], terminal[-1]["data"]["status"]), ("live\n", "passed"))


class BashHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create(approved_request())
        self.server = make_server(self.engine, 0, "token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def call(self, method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": "Bearer token"} if auth else {}
        raw = json.dumps(body) if body is not None else None
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_authenticated_execute_start_search_get_stop_and_clear(self):
        def fake(_workdir, _files, _command, _cancel, **kwargs):
            kwargs["on_chunk"]("ok\n")
            return {"status": "passed", "exit_code": 0, "output": "ok\n", "changed": {}}

        prefix = f"/api/tasks/{self.task}/bash"
        self.assertEqual(self.call("GET", prefix + "/events", auth=False)[0], 401)
        with patch("dikdik.bash_service.run_shell", side_effect=fake):
            status, executed = self.call("POST", prefix + "/execute", {"command": "echo ok"})
            self.assertEqual((status, executed["output"]), (200, "ok\n"))
            status, started = self.call("POST", prefix + "/start", {"command": "echo ok"})
            self.assertEqual(status, 202)
            for _ in range(100):
                events = self.call("GET", prefix + f"/events?command_id={started['id']}&limit=10")[1]["items"]
                if events and events[-1].get("final"):
                    break
                time.sleep(.01)
        one = self.call("GET", prefix + "/events?limit=1")[1]
        self.assertIsNotNone(one["next_after"])
        next_page = self.call("GET", prefix + f"/events?after={one['next_after']}&limit=1")[1]
        self.assertNotEqual(one["items"][0]["id"], next_page["items"][0]["id"])
        event_id = self.call("GET", prefix + "/events?limit=20")[1]["items"][0]["id"]
        self.assertEqual(self.call("GET", prefix + "/events/" + event_id)[0], 200)
        self.assertEqual(self.call("POST", prefix + f"/commands/{started['id']}/stop", {})[0], 200)
        # stop() sets the cancel event but returns before the worker pops the
        # command from _active; wait for it to wind down before clearing.
        for _ in range(100):
            if not self.engine.bash.busy(self.task):
                break
            time.sleep(.01)
        self.assertEqual(self.call("DELETE", prefix + "/events")[0], 200)
        self.assertEqual(self.call("GET", prefix + "/events")[1]["items"], [])

    def test_routes_reject_unknown_bad_ids_and_unapproved_tasks(self):
        prefix = f"/api/tasks/{self.task}/bash"
        self.assertEqual(self.call("GET", "/api/tasks/not-a-task/bash/events")[0], 400)
        self.assertEqual(self.call("GET", "/api/tasks/" + "0" * 32 + "/bash/events")[0], 404)
        denied = self.engine.store.create(approved_request(False))
        self.assertEqual(self.call("POST", f"/api/tasks/{denied}/bash/start", {"command": "true"})[0], 403)
        self.assertEqual(self.call("POST", prefix + "/commands/not-a-uuid/stop", {})[0], 400)
