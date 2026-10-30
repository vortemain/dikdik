"""Optional native desktop host for the existing authenticated loopback app.

The web interface uses the same HTTP API as the browser version. No Python
functions are exposed to page JavaScript, and pywebview is imported only when
desktop mode is explicitly requested.
"""

import functools
import importlib
import io
from pathlib import Path
import sys
import threading
from contextlib import redirect_stderr
from urllib.parse import urlsplit


def load_webview():
    try:
        return importlib.import_module("webview")
    except ImportError as exc:
        raise ValueError(
            'Desktop support is not installed. Install the desktop extra from your Dikdik wheel '
            'or, in its source checkout, run: python -m pip install -e ".[desktop]". '
            'On Linux also install a renderer, for example "pywebview[qt]". '
            'See docs/DESKTOP.md.'
        ) from exc


def _enable_native_context_menus(edgechromium=None) -> None:
    """Keep the browser right-click menu available in non-debug desktop mode.

    pywebview 6 enables WebView2's default context menus (copy, paste, select)
    only when debug=True, so copy/paste by right-click is silently unavailable
    in release builds. Wrap the EdgeChromium hook so the menus are normally
    available while debug-only extras (devtools, F12, status bar) stay off.
    A missing or renamed upstream hook is harmless; newer releases may fix it.
    """
    if edgechromium is None:
        if sys.platform != "win32":
            return
        try:
            from webview.platforms import edgechromium
        except Exception:
            return
    try:
        original = edgechromium.BrowserView.on_webview_ready
        if getattr(original, "_dikdik_context_menus", False):
            return
    except Exception:
        return

    @functools.wraps(original)
    def on_webview_ready(view, sender, args):
        original(view, sender, args)
        try:
            sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
        except Exception:
            pass  # The menu is an improvement; a newer pywebview owns its own behavior.

    on_webview_ready._dikdik_context_menus = True
    edgechromium.BrowserView.on_webview_ready = on_webview_ready


def run(server, link: str, data: Path, webview=None) -> None:
    """Run the native GUI on the main thread; stop its server when it exits.

    The caller owns closing the server socket and the current project's engine.
    A stable loopback origin retains browser storage between desktop launches.
    """
    target = urlsplit(link)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != server.server_port:
        raise ValueError("The desktop window requires its own local Dikdik server")
    webview = webview or load_webview()
    _enable_native_context_menus()
    # All outbound links in the application use target=_blank. Keep them in the
    # system browser, including provider sign-in, without enabling a Python API.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["REMOTE_DEBUGGING_PORT"] = None
    storage = Path(data) / "desktop"
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                              name="dikdik-desktop-http", daemon=True)
    worker.start()
    notifications = None
    try:
        main_window = webview.create_window("Dikdik", url=link, width=1440, height=940,
                              min_size=(760, 560), background_color="#1F3D31",
                              text_select=True, zoomable=True)
        # Persistent, private-to-Dikdik storage retains theme, drafts and the
        # HttpOnly session cookie. The token remains in a URL fragment only.
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dikdik.Desktop")
        icon = Path(__file__).parent / "web" / ("icon.ico" if sys.platform == "win32" else "icon.png")
        if getattr(server, "projects", None) is not None:
            from .notifications import DesktopNotifications
            notifications = DesktopNotifications(server, main_window, f"http://127.0.0.1:{server.server_port}")
            server.desktop_notifications = notifications
        def _started():
            # The EdgeChromium host can open behind the previous window on Windows; bring it forward,
            # and never leave it minimized after the launcher exits. Maximize on launch.
            for method in ("restore", "show"):
                try:
                    getattr(main_window, method)()
                except Exception:
                    pass
            if sys.platform == "win32":
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    hwnd = user32.FindWindowW(None, "Dikdik")
                    if hwnd:
                        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                        user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            if notifications:
                notifications.start()
        webview.start(func=_started,
                      private_mode=False, storage_path=str(storage), debug=False, icon=str(icon),
                      **({"gui": "edgechromium"} if sys.platform == "win32" else {}))
    finally:
        try:
            if notifications:
                notifications.close()
        finally:
            server.desktop_notifications = None
            server.shutdown()
            worker.join(timeout=5)


def _show_error(message: str) -> None:
    """Windows GUI entry points have no stderr; make launch failures visible."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Dikdik could not start", 0x10)
    else:
        try:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            try:
                messagebox.showerror("Dikdik could not start", message, parent=root)
            finally:
                root.destroy()
        except Exception:
            # A headless desktop launch still exits unsuccessfully.
            pass


def main():
    """Installed GUI entry point (pythonw on Windows), also usable with -m."""
    from .server import main as server_main
    if sys.stderr is not None:
        return server_main(desktop=True)
    errors = io.StringIO()
    with redirect_stderr(errors):
        try:
            return server_main(desktop=True)
        except SystemExit as exc:
            if exc.code:
                _show_error(errors.getvalue().strip() or "Dikdik could not start.")
            raise
        except Exception as exc:
            _show_error(str(exc) or type(exc).__name__)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
