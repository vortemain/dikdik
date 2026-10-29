"""Native notification policy, durable event delivery, activation and settings."""
import asyncio
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dikdik.engine import Engine
from dikdik.notifications import DesktopNotifications, Notice, notice_for
from dikdik.server import make_server
from dikdik.store import Store


class PolicyTests(unittest.TestCase):
    def task(self, **values):
        return {"id": "a" * 32, "workspace": "/private/project", "state": "completed",
                "title": "Private customer work", "request": {"objective": "API_KEY=secret"}, "result": {}, **values}

    def test_titles_and_generic_copy_do_not_expose_task_contents(self):
        for kind in ("completed", "failed", "blocked", "paused", "confirmation_requested", "question_requested", "secret_requested"):
            task = self.task(state="running" if kind.endswith("requested") else kind)
            notice = notice_for({"kind": kind}, task, {"notifications": "all"})
            self.assertIsNotNone(notice)
            for secret in ("Private customer", "API_KEY", "secret", "/private"):
                self.assertNotIn(secret, notice.title + notice.message)

    def test_previews_are_explicit_and_do_not_include_tool_output(self):
        notice = notice_for({"kind": "completed"}, self.task(result={"output": "PASSWORD=hidden"}), {"notification_previews": True})
        self.assertIn("Private customer work", notice.message)
        self.assertNotIn("PASSWORD", notice.message)

    def test_suppresses_muted_cancelled_superseded_and_helper_sessions(self):
        self.assertIsNone(notice_for({"kind": "completed"}, self.task(), {"notifications": "off"}))
        self.assertIsNone(notice_for({"kind": "cancelled"}, self.task(state="cancelled"), {}))
        self.assertIsNone(notice_for({"kind": "completed"}, self.task(state="running"), {}))
        self.assertIsNone(notice_for({"kind": "completed"}, self.task(result={"browser_session": True}), {}))
        self.assertIsNone(notice_for({"kind": "completed"}, self.task(result={"summary": "Workspace tools ready."}), {}))
        self.assertIsNone(notice_for({"kind": "question_requested"}, self.task(), {}))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.store = Store(base / "state.sqlite3")
        self.store.bind_workspace(base / "project")
        self.settings = {"notifications": "all", "notification_previews": False, "sounds": False}
        self.engine = SimpleNamespace(root=base / "project", store=self.store, app_settings=lambda: self.settings)
        self.server = SimpleNamespace(projects=SimpleNamespace(engine=self.engine))
        self.window = Mock()
        self.window.get_current_url.return_value = "http://127.0.0.1:8787/"
        self.window.evaluate_js.return_value = {"focused": False, "task": None}
        self.center = DesktopNotifications(self.server, self.window, "http://127.0.0.1:8787")
        self.center._notifier = SimpleNamespace(request_authorisation=AsyncMock(return_value=True), send=AsyncMock())
        self.center._sdk = SimpleNamespace(DEFAULT_SOUND="sound")

    def task(self):
        return self.store.create({"objective": "Sensitive task content", "approve_external": True})

    async def test_notifies_background_task_once_and_new_turn_can_notify_again(self):
        task = self.task(); self.store.state(task, "completed", {})
        await self.center.poll_once(); await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.await_count, 1)
        self.store.state(task, "completed", {})  # duplicate durable state in the same turn
        await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.await_count, 1)
        self.store.event(task, "user_message", {"text": "Another turn"})
        self.store.state(task, "completed", {})
        await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.await_count, 2)

    async def test_background_mode_suppresses_viewed_task_but_not_other_tasks(self):
        first, second = self.task(), self.task()
        self.settings["notifications"] = "background"
        self.window.evaluate_js.return_value = {"focused": True, "task": first}
        self.store.state(first, "completed", {}); self.store.state(second, "completed", {})
        await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.await_count, 1)
        callback = self.center._notifier.send.call_args.kwargs["on_clicked"]
        callback()
        self.assertIn(second, self.window.evaluate_js.call_args.args[0])

    async def test_muted_events_are_not_replayed_and_old_history_is_skipped(self):
        task = self.task(); self.store.state(task, "completed", {})
        self.settings["notifications"] = "off"
        await self.center.poll_once()
        self.settings["notifications"] = "all"
        await self.center.poll_once()
        self.center._notifier.send.assert_not_awaited()
        notifier = SimpleNamespace(get_capabilities=AsyncMock(return_value={"title"}), has_authorisation=AsyncMock(return_value=True), send=AsyncMock())
        center = DesktopNotifications(self.server, self.window, "http://127.0.0.1:8787", lambda: (notifier, self.center._sdk))
        center.start(); center.close()
        notifier.send.assert_not_awaited()

    async def test_requests_resolved_before_delivery_are_suppressed(self):
        task = self.task(); self.store.state(task, "running")
        for asked, answered in (("confirmation_requested", "confirmation_resolved"), ("question_requested", "question_answered"), ("secret_requested", "secret_answered")):
            self.store.event(task, asked, {"id": asked})
            self.store.event(task, answered, {"id": asked})
        await self.center.poll_once()
        self.center._notifier.send.assert_not_awaited()
        self.store.event(task, "confirmation_requested", {"id": "new"})
        await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.await_count, 1)
        self.assertEqual(self.center._notifier.send.call_args.kwargs["title"], "Approval needed")

    async def test_project_isolation_and_notification_sound(self):
        task = self.task()
        self.store.state(task, "failed", {"error": "SECRET"})
        self.store.bind_workspace(self.engine.root / "other")
        await self.center.poll_once()
        self.center._notifier.send.assert_not_awaited()
        other = self.task(); self.store.state(other, "completed", {})
        self.settings["sounds"] = True
        await self.center.poll_once()
        self.assertEqual(self.center._notifier.send.call_args.kwargs["sound"], "sound")

    async def test_os_denial_is_reported_without_claiming_delivery(self):
        self.center._notifier.request_authorisation.return_value = False
        task = self.task(); self.store.state(task, "completed", {})
        await self.center.poll_once()
        self.center._notifier.send.assert_not_awaited()
        self.assertEqual(self.center.status()["permission"], "not_allowed")
        self.assertEqual(self.center.status()["sent"], 0)

    def test_activation_only_opens_a_trusted_local_workspace(self):
        notice = Notice("a" * 32, str(self.engine.root), "Done", "Done")
        self.center.activate(notice)
        self.window.restore.assert_called_once(); self.window.show.assert_called_once()
        script = self.window.evaluate_js.call_args.args[0]
        self.assertIn("dikdik-open-notification", script)
        self.assertNotIn("approve", script)
        self.window.get_current_url.return_value = "https://example.com/"
        self.window.evaluate_js.reset_mock()
        self.center.activate(notice)
        self.window.evaluate_js.assert_not_called()


class SettingsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name); root = base / "project"; root.mkdir()
        (root / "app.py").write_text("x=1\n")
        self.engine = Engine(root, base / "data"); self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, auth=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {"Authorization": "Bearer test-token"} if auth else {})
        response = connection.getresponse(); result = response.status, json.loads(response.read()); connection.close()
        return result

    def test_settings_defaults_persistence_and_validation(self):
        self.assertEqual(self.engine.app_settings()["notifications"], "background")
        self.assertFalse(self.engine.app_settings()["notification_previews"])
        self.engine.update_app_settings({"notifications": "off", "notification_previews": True})
        self.assertEqual(self.engine.app_settings()["notifications"], "off")
        for body in ({"notifications": []}, {"notifications": "yes"}, {"notification_previews": 1}):
            with self.subTest(body=body), self.assertRaises(ValueError): self.engine.update_app_settings(body)

    def test_status_and_test_require_authenticated_desktop_session(self):
        self.assertEqual(self.call("GET", "/api/desktop/notifications", auth=False)[0], 401)
        self.assertFalse(self.call("GET", "/api/desktop/notifications")[1]["desktop"])
        self.assertEqual(self.call("POST", "/api/desktop/notifications/test", {}, auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/desktop/notifications/test", {})[0], 400)
        self.server.desktop_notifications = SimpleNamespace(status=lambda: {"desktop": True, "available": True}, test=Mock(return_value={"sent": True}))
        self.assertEqual(self.call("POST", "/api/desktop/notifications/test", {}), (200, {"sent": True}))
        self.assertTrue(self.call("GET", "/api/desktop/notifications")[1]["available"])


if __name__ == "__main__":
    unittest.main()
