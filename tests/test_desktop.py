"""Native hosting keeps authenticated HTTP and always releases its server."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from dikdik import desktop
from dikdik.server import main as server_main


class DesktopTests(unittest.TestCase):
    def make_server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Dikdik")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        return server

    def test_gui_uses_local_authenticated_link_and_stops_server(self):
        server = self.make_server()
        renderer = SimpleNamespace(settings={}, create_window=MagicMock())
        link = f"http://127.0.0.1:{server.server_port}/#token=private-token"

        def gui_start(**options):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request("GET", "/")
            self.assertEqual(connection.getresponse().read(), b"Dikdik")
            connection.close()
            self.assertFalse(options["private_mode"])
            self.assertFalse(options["debug"])

        renderer.start = Mock(side_effect=gui_start)
        with tempfile.TemporaryDirectory() as data:
            desktop.run(server, link, Path(data), renderer)
            self.assertTrue((Path(data) / "desktop").is_dir())
            self.assertEqual(renderer.start.call_args.kwargs["storage_path"], str(Path(data) / "desktop"))
        renderer.create_window.assert_called_once()
        self.assertTrue(Path(renderer.start.call_args.kwargs["icon"]).is_file())
        self.assertEqual(renderer.create_window.call_args.kwargs["url"], link)
        self.assertNotIn("js_api", renderer.create_window.call_args.kwargs)
        self.assertTrue(renderer.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])
        self.assertFalse(renderer.settings["ALLOW_FILE_URLS"])
        self.assertTrue(server._BaseServer__is_shut_down.is_set())

    def test_renderer_failure_stops_server(self):
        server = self.make_server()
        renderer = SimpleNamespace(settings={}, create_window=MagicMock(), start=Mock(side_effect=RuntimeError("renderer unavailable")))
        with tempfile.TemporaryDirectory() as data:
            with self.assertRaisesRegex(RuntimeError, "renderer unavailable"):
                desktop.run(server, f"http://127.0.0.1:{server.server_port}/", Path(data), renderer)
        self.assertTrue(server._BaseServer__is_shut_down.is_set())

    def test_native_notification_service_starts_with_gui_and_stops_on_exit(self):
        server = self.make_server()
        server.projects = SimpleNamespace(engine=Mock())
        renderer = SimpleNamespace(settings={}, create_window=MagicMock())
        center = Mock()
        def start(**options):
            self.assertIs(server.desktop_notifications, center)
            options["func"]()
        renderer.start = Mock(side_effect=start)
        with tempfile.TemporaryDirectory() as data, patch("dikdik.notifications.DesktopNotifications", return_value=center):
            desktop.run(server, f"http://127.0.0.1:{server.server_port}/", Path(data), renderer)
        center.start.assert_called_once()
        center.close.assert_called_once()
        self.assertIsNone(server.desktop_notifications)

    def test_desktop_refuses_remote_and_wrong_port_before_starting(self):
        server = self.make_server()
        renderer = Mock()
        for link in ("https://example.com/", f"http://127.0.0.1:{server.server_port + 1}/"):
            with self.subTest(link=link), self.assertRaisesRegex(ValueError, "local Dikdik"):
                desktop.run(server, link, Path("unused"), renderer)
        renderer.create_window.assert_not_called()

    def test_missing_extra_has_install_instructions(self):
        with patch("dikdik.desktop.importlib.import_module", side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(ValueError, r"\.\[desktop\]"):
                desktop.load_webview()

    def test_conflicting_launch_modes_are_refused_before_engine_init(self):
        with patch("sys.argv", ["dikdik", "--desktop", "--no-browser"]), patch("sys.stderr", io.StringIO()), patch("dikdik.server.Engine") as engine:
            with self.assertRaises(SystemExit) as exit:
                server_main()
        self.assertEqual(exit.exception.code, 2)
        engine.assert_not_called()

    def test_cli_launches_desktop_with_session_token_and_closes_current_engine(self):
        server = Mock(server_port=8787)
        renderer = Mock()
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as workspace:
            with (patch("sys.argv", ["dikdik", "--desktop", workspace, "--data", data]),
                  patch("sys.stdout", io.StringIO()), patch("dikdik.server.Engine"),
                  patch("dikdik.server.session_token", return_value="test-private-token"),
                  patch("dikdik.server.socket.socket") as socket,
                  patch("dikdik.server.make_server", return_value=server) as make_server,
                  patch("dikdik.desktop.load_webview", return_value=renderer),
                  patch("dikdik.desktop.run") as run,
                  patch("dikdik.server.webbrowser.open") as browser):
                socket.return_value.connect_ex.return_value = 1
                server_main()
        self.assertEqual(make_server.call_args.args[1:3], (8787, "test-private-token"))
        self.assertEqual(run.call_args.args[1], "http://127.0.0.1:8787/#token=test-private-token")
        self.assertIs(run.call_args.args[3], renderer)
        browser.assert_not_called()
        server.server_close.assert_called_once()
        server.projects.engine.close.assert_called_once()

    def test_desktop_first_launch_opens_project_picker_and_cancel_is_clean(self):
        with (patch("sys.argv", ["dikdik", "--desktop"]),
              patch("dikdik.desktop.load_webview"),
              patch("dikdik.server.socket.socket") as socket,
              patch("dikdik.server.check_location", side_effect=ValueError("Choose a project folder")),
              patch("dikdik.server.last_project", return_value=None),
              patch("dikdik.server.pick_folder", return_value=None) as pick,
              patch("dikdik.server.Engine") as engine):
            socket.return_value.connect_ex.return_value = 1
            with self.assertRaises(SystemExit) as exit:
                server_main()
        self.assertEqual(exit.exception.code, 0)
        pick.assert_called_once()
        engine.assert_not_called()

    def test_gui_entrypoint_surfaces_errors_without_console(self):
        with patch("sys.stderr", None), patch("dikdik.server.main", side_effect=SystemExit(2)), patch("dikdik.desktop._show_error") as show:
            with self.assertRaises(SystemExit):
                desktop.main()
        show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
