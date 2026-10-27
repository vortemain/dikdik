import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse
from dikdik.engine import Engine, TERMINAL
from dikdik.profiles import ApiError
from dikdik.routing import choose
from dikdik.runners import Redactor, container_command
from dikdik.server_routes import build_routes

AGENT = {"mode": "agent", "verification": "syntax", "approve_external": True}


class Stub(BaseHTTPRequestHandler):
    """A local chat-completions server recording what it was asked."""
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        raw = json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.routes = build_routes(self.engine)

    def call(self, method, path, body=None):
        return self.routes(method, path, body)

    def ok(self, method, path, body=None, status=(200, 201)):
        code, data = self.call(method, path, body)
        self.assertIn(code, status if isinstance(status, tuple) else (status,), data)
        return data

    def idle(self, task, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.engine.store.get(task)
            if data["state"] in TERMINAL and task not in self.engine.active:
                return data
            time.sleep(0.05)
        self.fail("task did not finish")


class ConnectionTests(Base):
    def test_connection_lifecycle_and_linked_profile(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        conn = self.ok("POST", "/api/connections", {"name": "Lab", "provider": "local", "api_key": "sk-first-key", "endpoint": endpoint})["id"]
        listed = self.ok("GET", "/api/connections")
        self.assertEqual((listed[0]["name"], listed[0]["key_set"], listed[0]["models"]), ("Lab", True, 0))
        self.assertNotIn("sk-first-key", json.dumps(listed))
        self.assertNotIn(f"conn.{conn}", json.dumps(self.ok("GET", "/api/secrets")))  # managed keys stay out of API keys
        profile = self.ok("POST", "/api/profiles", {"name": "Lab model", "provider": "local", "model": "m1", "connection_id": conn, "temperature": 0.3})["id"]
        self.assertEqual(self.ok("GET", "/api/connections")[0]["models"], 1)

        Stub.seen.clear()
        result = self.ok("POST", f"/api/profiles/{profile}/test")
        self.assertEqual((result["ok"], result["reply"], result["model"]), (True, "OK", "m1"))
        self.assertEqual(Stub.seen[-1]["auth"], "Bearer sk-first-key")
        self.assertEqual(Stub.seen[-1]["body"]["temperature"], 0.3)

        self.ok("PATCH", f"/api/connections/{conn}", {"api_key": "sk-rotated-key", "name": "Lab 2"})
        self.ok("POST", f"/api/profiles/{profile}/test")
        self.assertEqual(Stub.seen[-1]["auth"], "Bearer sk-rotated-key")
        self.ok("PATCH", f"/api/connections/{conn}", {"api_key": ""})  # blank keeps the key
        self.ok("POST", f"/api/profiles/{profile}/test")
        self.assertEqual(Stub.seen[-1]["auth"], "Bearer sk-rotated-key")

        code, data = self.call("DELETE", f"/api/connections/{conn}")
        self.assertEqual(code, 409)
        self.assertIn("“Lab model”", data["error"])
        self.ok("PATCH", f"/api/profiles/{profile}", {"connection_id": None})
        self.ok("DELETE", f"/api/connections/{conn}")
        self.assertEqual(self.ok("GET", "/api/connections"), [])

    def test_connection_validation(self):
        for body in ({"name": "", "provider": "openai", "api_key": "k"}, {"name": "x", "provider": "nope", "api_key": "k"},
                     {"name": "x", "provider": "openai", "api_key": "has space"}, {"name": "x", "provider": "openai"},
                     {"name": "x" * 129, "provider": "openai", "api_key": "k"}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/api/connections", body)[0], 400)
        self.ok("POST", "/api/connections", {"name": "Dup", "provider": "openai", "api_key": "k1"})
        self.assertEqual(self.call("POST", "/api/connections", {"name": "Dup", "provider": "openai", "api_key": "k2"})[0], 400)
        self.assertEqual(self.call("POST", "/api/profiles", {"name": "p", "provider": "openai", "model": "m", "connection_id": "missing"})[0], 400)

    def test_catalog_and_duplicate_routes(self):
        catalog = self.ok("GET", "/api/model-catalog")
        self.assertIn("claude-sonnet-5-5", catalog["providers"]["anthropic"]["models"])
        profile = self.ok("POST", "/api/profiles", {"name": "Main", "provider": "openai", "model": "gpt-5"})["id"]
        self.assertEqual(self.ok("POST", f"/api/profiles/{profile}/duplicate")["name"], "Main-copy")


class RoutingTests(Base):
    def test_choose_rules(self):
        router = {"strategy": "first_turn", "strong_profile_id": "S", "fast_profile_id": "F", "threshold": None}
        self.assertEqual(choose(router, "x", "agent", False)[0], "S")
        self.assertEqual(choose(router, "x", "agent", True)[0], "F")
        router = {**router, "strategy": "long_message", "threshold": 10}
        self.assertEqual(choose(router, "short", "agent", True)[0], "F")
        self.assertEqual(choose(router, "a much longer message", "agent", True)[0], "S")
        router = {**router, "strategy": "agent_mode"}
        self.assertEqual((choose(router, "x", "agent", True)[0], choose(router, "x", "plan", True)[0]), ("S", "F"))

    def test_router_crud_and_activation(self):
        strong = self.ok("POST", "/api/profiles", {"name": "Strong", "provider": "openai", "model": "big"})["id"]
        fast = self.ok("POST", "/api/profiles", {"name": "Fast", "provider": "openai", "model": "small"})["id"]
        self.assertEqual(self.call("POST", "/api/routers", {"name": "R", "strategy": "long_message", "strong_profile_id": strong, "fast_profile_id": fast})[0], 400)
        router = self.ok("POST", "/api/routers", {"name": "R", "strategy": "first_turn", "strong_profile_id": strong, "fast_profile_id": fast})["id"]
        listed = self.ok("GET", "/api/routers")
        self.assertEqual((listed["routers"][0]["strong_name"], listed["routers"][0]["fast_name"]), ("Strong", "Fast"))
        self.assertIn("first_turn", listed["strategies"])
        self.engine.update_app_settings({"router": router})
        with self.assertRaises(ValueError):
            self.engine.update_app_settings({"router": "missing"})
        task = self.engine.store.create({"objective": "Hi", **AGENT})
        self.assertEqual(self.engine._model_for(task, {"objective": "Hi", "mode": "agent"}, None), strong)
        self.engine.store.event(task, "completed", {})
        self.assertEqual(self.engine._model_for(task, {"objective": "More", "mode": "agent"}, None), fast)
        routed = [e["data"] for e in self.engine.store.get(task)["events"] if e["kind"] == "model_routed"]
        self.assertEqual([(r["profile"], r["reason"]) for r in routed], [("Strong", "first message"), ("Fast", "follow-up message")])
        self.assertEqual(self.engine._model_for(task, {"objective": "x", "mode": "agent", "model_profile": fast}, None), fast)  # an explicit pick wins
        self.ok("DELETE", f"/api/routers/{router}")
        self.assertIsNone(self.engine._model_for(task, {"objective": "x", "mode": "agent"}, None))


class SecretTests(Base):
    def test_rotation_undo_rename_and_env(self):
        self.ok("POST", "/api/secrets", {"ref": "gh", "plaintext": "first-value"})
        self.ok("POST", "/api/secrets", {"ref": "gh", "plaintext": "second-value"})
        listed = self.ok("GET", "/api/secrets")[0]
        self.assertIsNotNone(listed["previous_set_at"])
        self.ok("POST", "/api/secrets/gh/restore")
        self.assertEqual(self.engine.models.secrets.reveal("gh"), b"first-value")
        self.assertEqual(self.call("POST", "/api/secrets/gh/restore")[0], 400)  # nothing left to restore
        self.ok("POST", "/api/secrets", {"ref": "gh", "plaintext": "third-value"})  # counters never repeat after an undo
        self.assertEqual(self.engine.models.secrets.reveal("gh"), b"third-value")

        profile = self.ok("POST", "/api/profiles", {"name": "P", "provider": "openai", "model": "m", "credential_secret_ref": "gh"})["id"]
        self.ok("POST", "/api/secrets/gh/rename", {"to": "github"})
        self.assertEqual(self.engine.models.secrets.reveal("github"), b"third-value")
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute("SELECT credential_secret_ref FROM profiles WHERE id=?", (profile,)).fetchone()[0], "github")

        self.ok("PATCH", "/api/secrets/github", {"env": "GITHUB_TOKEN"})
        self.assertEqual(self.ok("GET", "/api/secrets")[0]["env"], "GITHUB_TOKEN")
        for bad in ("PATH", "lower", "1BAD"):
            self.assertEqual(self.call("PATCH", "/api/secrets/github", {"env": bad})[0], 400)
        self.assertEqual(self.engine.models.secrets.sandbox_values(), {"GITHUB_TOKEN": "third-value"})
        self.assertEqual(self.engine.models.secrets.sandbox_values({"other"}), {})
        self.assertEqual(self.call("POST", "/api/secrets", {"ref": "empty", "plaintext": "  "})[0], 400)
        self.assertEqual(self.call("POST", "/api/secrets", {"ref": "conn.x", "plaintext": "v"})[0], 400)

    def test_redaction_across_chunks(self):
        redactor = Redactor({"TOKEN": "s3cr3t-value"})
        out = redactor.feed("token=s3cr") + redactor.feed("3t-value done") + redactor.flush()
        self.assertEqual(out, "token=[redacted TOKEN] done")
        self.assertEqual(redactor.clean("a s3cr3t-value b"), "a [redacted TOKEN] b")

    def test_values_never_reach_the_docker_command_line(self):
        command = container_command(Path("/w"), "n", "img", ["true"], env_names=("GITHUB_TOKEN",))
        self.assertIn("--env=GITHUB_TOKEN", command)
        self.assertFalse(any("third-value" in part for part in command))

    def test_sandbox_secrets_need_task_approval(self):
        self.engine.models.secrets.set("gh", b"value-123")
        self.engine.models.secrets.set_env("gh", "GH")
        task = self.engine.store.create({"objective": "x"})
        self.assertEqual(self.engine._sandbox_secrets(task, {"approve_execution": True}, None), {})
        self.assertEqual(self.engine._sandbox_secrets(task, {"approve_execution": True, "approve_secrets": True}, None), {"GH": "value-123"})
        self.assertEqual(self.engine._sandbox_secrets(task, {"approve_execution": True, "approve_secrets": True}, {"secret_refs": []}), {})
        names = [e["data"]["names"] for e in self.engine.store.get(task)["events"] if e["kind"] == "sandbox_secrets"]
        self.assertEqual(names, [["GH"]])
        self.assertNotIn("value-123", json.dumps(self.engine.store.get(task)["events"]))


class BehaviourTests(Base):
    def test_critic_and_memory(self):
        (self.root / ".dikdik").mkdir()
        (self.root / ".dikdik" / "memory.md").write_text("Always run the tests.", encoding="utf-8")
        agent = self.engine.agents.create({"name": "Careful", "critic": True, "memory": True})["id"]
        seen = []

        class Model:
            def __init__(self, *args, **kwargs):
                pass

            def respond(self, messages, tools):
                seen.append(messages)
                return ModelResponse("Nothing to change.", [])
        with patch.dict(os.environ, {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}), \
                patch("dikdik.engine.CompatibleAgentModel", Model), \
                patch("dikdik.engine.judge", return_value={"achieved": True, "reason": "Looks done", "next": ""}):
            task = self.engine.submit({"objective": "Check", **AGENT, "agent_profile": agent})
            self.idle(task)
            deadline = time.time() + 5
            while time.time() < deadline and not any(e["kind"] == "critic" for e in self.engine.store.get(task)["events"]):
                time.sleep(0.05)
        self.assertTrue(any("Always run the tests." in str(m.get("content")) for m in seen[0]))
        critic = [e["data"] for e in self.engine.store.get(task)["events"] if e["kind"] == "critic"]
        self.assertEqual(critic, [{"achieved": True, "reason": "Looks done", "next": ""}])
        self.assertEqual(self.engine.agents.get(agent)["secret_refs"], None)
        with self.assertRaises(ValueError):
            self.engine.agents.update(agent, {"secret_refs": ["bad ref"]})


if __name__ == "__main__":
    unittest.main()
