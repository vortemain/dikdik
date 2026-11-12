"""Native hosting keeps authenticated HTTP and always releases its server."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import sys
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
        # The page can reach only the title bar's window buttons, and nothing through them.
        api = renderer.create_window.call_args.kwargs.get("js_api")
        if sys.platform == "win32":
            self.assertIsInstance(api, desktop.WindowControls)
            self.assertEqual({name for name in dir(api) if not name.startswith("_")},
                             {"window_state", "minimize", "toggle_maximize", "close"})
        else:
            self.assertIsNone(api)
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

    def test_right_click_context_menu_enabled_outside_debug_mode(self):
        class Settings:
            AreDefaultContextMenusEnabled = False

        settings = Settings()
        core = SimpleNamespace(Settings=settings)
        native = SimpleNamespace(CoreWebView2=core)
        ready = []
        # pywebview 6 names the WebView2 host class EdgeChrome.
        class EdgeChrome:
            def on_webview_ready(self, sender, args):
                ready.append(sender)

        stub = SimpleNamespace(EdgeChrome=EdgeChrome)
        desktop._edgechromium_menus(stub)
        EdgeChrome().on_webview_ready(native, None)
        self.assertEqual(ready, [native])
        self.assertTrue(settings.AreDefaultContextMenusEnabled)
        # A broken native attribute does not break window readiness.
        settings.AreDefaultContextMenusEnabled = False
        native.CoreWebView2 = SimpleNamespace(Settings=SimpleNamespace())
        EdgeChrome().on_webview_ready(native, None)
        self.assertEqual(ready.count(native), 2)
        # Idempotent: wrapping twice does not stack calls.
        desktop._edgechromium_menus(stub)
        EdgeChrome().on_webview_ready(native, None)
        self.assertEqual(len(ready), 3)

    def test_context_menus_fixed_for_the_renderer_pywebview_loads(self):
        class EdgeChrome:
            def on_webview_ready(self, sender, args):
                pass
        platform = SimpleNamespace(EdgeChrome=EdgeChrome)
        renderer = SimpleNamespace(initialize=lambda gui=None: "guilib")
        desktop._enable_native_context_menus(renderer)
        with patch.dict("sys.modules", {"webview.platforms.edgechromium": platform}):
            self.assertEqual(renderer.initialize("edgechromium"), "guilib")
        self.assertTrue(getattr(EdgeChrome.on_webview_ready, desktop._PATCHED))
        first = renderer.initialize
        desktop._enable_native_context_menus(renderer)
        self.assertIs(renderer.initialize, first)

    def test_qt_context_menu_restored_without_inspect_element(self):
        state = {"debug": False}
        class Action:
            def __init__(self, text):
                self._text = text
            def text(self):
                return self._text
        class Menu:
            def __init__(self):
                self.items = [Action("Copy"), Action("Paste"), Action("Inspect")]
                self.shown = None
            def actions(self):
                return list(self.items)
            def removeAction(self, action):
                self.items.remove(action)
            def exec(self, pos):
                self.shown = pos
        menu = Menu()
        class WebView:
            def createStandardContextMenu(self):
                return menu
            def contextMenuEvent(self, event):
                raise AssertionError("pywebview's menu adds Inspect Element")
        class BrowserView:
            def __init__(self, window):
                self.webview = Mock()
                self.webview.setContextMenuPolicy("none")
        BrowserView.WebView = WebView
        Qt = SimpleNamespace(DefaultContextMenu="default")
        qt = SimpleNamespace(BrowserView=BrowserView, _state=state, QtCore=SimpleNamespace(Qt=Qt))
        desktop._qt_menus(qt)
        window = BrowserView(None)
        window.webview.setContextMenuPolicy.assert_called_with("default")
        WebView().contextMenuEvent(SimpleNamespace(globalPos=lambda: (4, 2)))
        self.assertEqual([a.text() for a in menu.items], ["Copy", "Paste"])
        self.assertEqual(menu.shown, (4, 2))
        state["debug"] = True
        with self.assertRaises(AssertionError):
            WebView().contextMenuEvent(None)

    def test_gtk_menu_blocker_is_removed(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("WebKit2", "4.1")
            from gi.repository import WebKit2
            webview = WebKit2.WebView()
        except Exception:
            self.skipTest("WebKitGTK is not available")
        calls = []
        class BrowserView:
            def __init__(self, window):
                self.webview = webview
                webview.connect("context-menu", lambda *a: calls.append(a) or True)
        desktop._gtk_menus(SimpleNamespace(BrowserView=BrowserView))
        BrowserView(None)
        from gi.repository import GObject
        signal = GObject.signal_lookup("context-menu", type(webview))
        self.assertEqual(GObject.signal_handler_find(webview, GObject.SignalMatchType.ID, signal, 0, None, None, None), 0)

    def test_title_bar_becomes_the_pages_only_when_webview2_supports_drag_regions(self):
        class Settings:
            AreDefaultContextMenusEnabled = False
            IsNonClientRegionSupportEnabled = False
        handle = SimpleNamespace(ToInt64=lambda: 4242)
        class EdgeChrome:
            def __init__(self): self.form = SimpleNamespace(Handle=handle)
            def on_webview_ready(self, sender, args): pass
        chrome = {"custom": False, "hwnd": None}
        native = SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=Settings()))
        with patch.object(desktop, "_remove_caption") as remove, patch.object(desktop, "_keep_maximized_in_work_area") as fit,                 patch.object(desktop, "_TITLE_BAR", chrome):
            desktop._edgechromium_menus(SimpleNamespace(EdgeChrome=EdgeChrome))
            EdgeChrome().on_webview_ready(native, None)
            remove.assert_called_once_with(4242)
            fit.assert_called_once_with(4242)
        self.assertTrue(native.CoreWebView2.Settings.IsNonClientRegionSupportEnabled)
        self.assertEqual(chrome, {"custom": True, "hwnd": 4242, "caption_removed": True})
        # An older WebView2 runtime without drag regions keeps the native title bar.
        class OldSettings:
            AreDefaultContextMenusEnabled = False
            def __setattr__(self, name, value):
                if name == "IsNonClientRegionSupportEnabled":
                    raise AttributeError(name)
                object.__setattr__(self, name, value)
        class OldEdgeChrome:
            def __init__(self): self.form = SimpleNamespace(Handle=handle)
            def on_webview_ready(self, sender, args): pass
        old = {"custom": False, "hwnd": None}
        with patch.object(desktop, "_remove_caption") as remove, patch.object(desktop, "_TITLE_BAR", old):
            desktop._edgechromium_menus(SimpleNamespace(EdgeChrome=OldEdgeChrome))
            OldEdgeChrome().on_webview_ready(SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=OldSettings())), None)
            remove.assert_not_called()
        self.assertFalse(old["custom"])
        self.assertEqual(desktop.WindowControls(old).window_state(), {"custom_title_bar": False, "maximized": False})

    def test_the_caption_goes_before_the_first_frame_and_returns_on_an_old_runtime(self):
        """No native title bar flashes at launch; a runtime without drag regions gets it back."""
        window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 99)))
        for supported in (True, False):
            with self.subTest(supported=supported):
                class Settings:
                    AreDefaultContextMenusEnabled = False
                    def __setattr__(self, name, value):
                        if name == "IsNonClientRegionSupportEnabled" and not supported:
                            raise AttributeError(name)
                        object.__setattr__(self, name, value)
                class EdgeChrome:
                    def __init__(self): self.form = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 99))
                    def on_webview_ready(self, sender, args): pass
                chrome = {"custom": False, "hwnd": None, "caption_removed": False}
                with patch.object(desktop, "_set_caption") as caption, patch.object(desktop, "_keep_maximized_in_work_area") as fit,                         patch.object(desktop, "_TITLE_BAR", chrome):
                    desktop._prepare_window(window, chrome)  # before_show
                    caption.assert_called_once_with(99, False)
                    fit.assert_called_once_with(99)
                    desktop._edgechromium_menus(SimpleNamespace(EdgeChrome=EdgeChrome))
                    EdgeChrome().on_webview_ready(SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=Settings())), None)
                    self.assertEqual(chrome["custom"], supported)
                    if supported:
                        caption.assert_called_once()  # not removed twice
                    else:
                        caption.assert_called_with(99, True)  # the native title bar is back
                    fit.assert_called_once()

    def test_maximized_windows_keep_the_taskbar_visible(self):
        # A 1920x1080 monitor with a 48 px taskbar at the bottom; an 8 px invisible resize border.
        monitor, work, frame = (0, 0, 1920, 1080), (0, 0, 1920, 1032), (-8, -8, 8, 8)
        self.assertEqual(desktop.maximized_bounds(work, monitor, frame), ((-8, -8), (1936, 1048)))
        # The content (window minus the border) fills the work area exactly and stops at the taskbar.
        (x, y), (w, h) = desktop.maximized_bounds(work, monitor, frame)
        self.assertEqual((x + 8, y + 8, x + w - 8, y + h - 8), work)
        # A taskbar on the left of a second monitor: positions are relative to that monitor.
        self.assertEqual(desktop.maximized_bounds((2000, 0, 3840, 1080), (1920, 0, 3840, 1080), frame), ((72, -8), (1856, 1096)))

    def test_window_background_matches_the_system_theme(self):
        self.assertIn(desktop.window_background(), {"#FBFBFA", "#1A1A19"})

    def test_window_buttons_act_only_on_their_own_window(self):
        user32 = Mock()
        user32.IsZoomed.return_value = 0
        controls = desktop.WindowControls({"custom": True, "hwnd": 77})
        with patch.object(desktop.WindowControls, "_user32", return_value=user32):
            controls.minimize()
            self.assertTrue(controls.toggle_maximize())
            controls.close()
            self.assertEqual(controls.window_state(), {"custom_title_bar": True, "maximized": False})
        user32.ShowWindow.assert_any_call(77, 6)
        user32.ShowWindow.assert_any_call(77, 3)
        user32.PostMessageW.assert_called_once_with(77, 0x0010, 0, 0)
        with patch.object(desktop.WindowControls, "_user32", return_value=user32):
            desktop.WindowControls({"custom": False, "hwnd": None}).close()  # no window yet: nothing happens
        user32.PostMessageW.assert_called_once()

    def test_context_menus_hook_absent_renderer_is_harmless(self):
        renderer = SimpleNamespace(settings={}, create_window=MagicMock())
        self.assertIsNone(desktop._enable_native_context_menus(renderer))
        desktop._edgechromium_menus(SimpleNamespace())
        desktop._qt_menus(SimpleNamespace())
        desktop._gtk_menus(SimpleNamespace())

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
