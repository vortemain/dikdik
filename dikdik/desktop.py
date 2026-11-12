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


_PATCHED = "_dikdik_context_menus"


def _wrap_once(owner, name: str, make):
    """Replace owner.name with make(original) unless already wrapped. Missing hooks are ignored."""
    try:
        original = getattr(owner, name)
    except Exception:
        return False
    if getattr(original, _PATCHED, False):
        return True
    wrapped = functools.wraps(original)(make(original))
    setattr(wrapped, _PATCHED, True)
    setattr(owner, name, wrapped)
    return True


def _edgechromium_menus(edgechromium) -> None:
    """WebView2 (Windows): pywebview turns default menus on only in debug mode."""
    def make(original):
        def on_webview_ready(view, sender, args):
            original(view, sender, args)
            try:
                sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
            except Exception:
                pass  # Initialization failed or a newer pywebview owns this setting.
        return on_webview_ready
    # pywebview 6 names the class EdgeChrome; older releases used BrowserView.
    for name in ("EdgeChrome", "BrowserView"):
        owner = getattr(edgechromium, name, None)
        if owner is not None and _wrap_once(owner, "on_webview_ready", make):
            return


def _gtk_menus(gtk) -> None:
    """WebKitGTK (Linux): pywebview connects a handler that swallows every right click."""
    def make(original):
        def __init__(view, *args, **kwargs):
            original(view, *args, **kwargs)
            try:
                from gi.repository import GObject
                webview = view.webview
                signal = GObject.signal_lookup("context-menu", type(webview))
                while signal:
                    handler = GObject.signal_handler_find(webview, GObject.SignalMatchType.ID,
                                                          signal, 0, None, None, None)
                    if not handler:
                        break
                    webview.disconnect(handler)
            except Exception:
                pass
        return __init__
    owner = getattr(gtk, "BrowserView", None)
    if owner is not None:
        _wrap_once(owner, "__init__", make)


def _qt_menus(qt) -> None:
    """Qt WebEngine (Linux release builds): pywebview sets NoContextMenu outside debug mode,
    and its own menu adds an Inspect Element entry. Show the standard menu without it."""
    owner = getattr(qt, "BrowserView", None)
    view_class = getattr(owner, "WebView", None)
    if owner is None or view_class is None:
        return
    debug = lambda: bool(getattr(qt, "_state", {}).get("debug"))

    def make_init(original):
        def __init__(window, *args, **kwargs):
            original(window, *args, **kwargs)
            if debug():
                return
            try:
                window.webview.setContextMenuPolicy(qt.QtCore.Qt.DefaultContextMenu)
            except Exception:
                pass
        return __init__

    def make_event(original):
        def contextMenuEvent(view, event):
            if debug():
                return original(view, event)
            try:
                create = getattr(view, "createStandardContextMenu", None) or view.page().createStandardContextMenu
                menu = create()
                for action in list(menu.actions()):
                    if "inspect" in (action.text() or "").lower():
                        menu.removeAction(action)
                if menu.actions():
                    (getattr(menu, "exec", None) or menu.exec_)(event.globalPos())
            except Exception:
                pass
        return contextMenuEvent

    _wrap_once(owner, "__init__", make_init)
    _wrap_once(view_class, "contextMenuEvent", make_event)


_PLATFORM_FIXES = {
    "webview.platforms.edgechromium": _edgechromium_menus,
    "webview.platforms.gtk": _gtk_menus,
    "webview.platforms.qt": _qt_menus,
}


def _fix_loaded_platforms() -> None:
    for name, fix in _PLATFORM_FIXES.items():
        module = sys.modules.get(name)
        if module is not None:
            try:
                fix(module)
            except Exception:
                pass  # A right-click menu is never worth failing the launch for.


def _enable_native_context_menus(webview=None) -> None:
    """Keep the right-click menu (cut, copy, paste, select all) in non-debug desktop mode.

    pywebview disables the renderer's context menu unless debug=True, on every
    platform it supports, so paste by right-click is unavailable in release
    builds. The renderer module is imported only when pywebview starts, so wrap
    its initializer and fix whichever renderer it loads. Debug-only extras
    (devtools, F12, status bar, Inspect Element) stay off.
    """
    _fix_loaded_platforms()
    if webview is None:
        return
    def make(original):
        def initialize(*args, **kwargs):
            guilib = original(*args, **kwargs)
            _fix_loaded_platforms()
            return guilib
        return initialize
    _wrap_once(webview, "initialize", make)


def run(server, link: str, data: Path, webview=None) -> None:
    """Run the native GUI on the main thread; stop its server when it exits.

    The caller owns closing the server socket and the current project's engine.
    A stable loopback origin retains browser storage between desktop launches.
    """
    target = urlsplit(link)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != server.server_port:
        raise ValueError("The desktop window requires its own local Dikdik server")
    webview = webview or load_webview()
    _enable_native_context_menus(webview)
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
                except Exception:  # older pywebview builds lack this window method
                    pass
            if sys.platform == "win32":
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    hwnd = user32.FindWindowW(None, "Dikdik")
                    if hwnd:
                        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                        user32.SetForegroundWindow(hwnd)
                except Exception:  # maximizing is cosmetic; the window is already open
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
