"""Optional native desktop host for the existing authenticated loopback app.

The web interface uses the same HTTP API as the browser version, and pywebview is
imported only when desktop mode is explicitly requested. On Windows the window has
an integrated title bar, as editors do: the native caption is removed (the resizable
frame stays), the page's title bar becomes the drag region through WebView2's
non-client region support (drag, Snap, double-click to maximize, system menu), and
the page draws its own minimize, maximize and close buttons. Those three buttons are
the only Python the page can call (`WindowControls`); nothing else is exposed.
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


# Win32 window styles for the integrated title bar.
GWL_STYLE = -16
WS_CAPTION = 0x00C00000  # title bar (WS_BORDER | WS_DLGFRAME)
WS_THICKFRAME = 0x00040000  # resizable edges, kept
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_FRAME = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010  # FRAMECHANGED, NOMOVE, NOSIZE, NOZORDER, NOACTIVATE


class WindowControls:
    """The page's title bar buttons: the only Python the page can call. It holds no
    reference the page could walk (pywebview exposes public members recursively)."""

    def __init__(self, chrome: dict):
        self._chrome = chrome

    def _user32(self):
        import ctypes
        return ctypes.windll.user32

    def window_state(self) -> dict:
        hwnd = self._chrome.get("hwnd")
        return {"custom_title_bar": bool(self._chrome.get("custom") and hwnd),
                "maximized": bool(hwnd and self._user32().IsZoomed(hwnd))}

    def minimize(self) -> None:
        if self._chrome.get("hwnd"):
            self._user32().ShowWindow(self._chrome["hwnd"], 6)  # SW_MINIMIZE

    def toggle_maximize(self) -> bool:
        hwnd = self._chrome.get("hwnd")
        if not hwnd:
            return False
        maximized = bool(self._user32().IsZoomed(hwnd))
        self._user32().ShowWindow(hwnd, 9 if maximized else 3)  # SW_RESTORE / SW_MAXIMIZE
        return not maximized

    def close(self) -> None:
        if self._chrome.get("hwnd"):
            self._user32().PostMessageW(self._chrome["hwnd"], 0x0010, 0, 0)  # WM_CLOSE: the usual close path


def _user32_signatures():
    """user32 with pointer-sized signatures for the calls below (64-bit safe)."""
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    return user32


def _set_caption(hwnd: int, shown: bool) -> None:
    """Drop (or restore) the native title bar. The resizable frame, Snap and the taskbar
    title stay either way."""
    import ctypes
    user32 = _user32_signatures()
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
    style = style | WS_CAPTION if shown else style & ~WS_CAPTION
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FRAME)


def _remove_caption(hwnd: int) -> None:
    _set_caption(hwnd, False)


WM_GETMINMAXINFO = 0x0024
GWLP_WNDPROC = -4
_SUBCLASSED: list = []  # window procedures stay referenced for the life of the process


def maximized_bounds(work, monitor, frame):
    """Where a captionless window goes when maximized, relative to its monitor: the work area
    (so the taskbar stays visible), widened by the window's invisible resize border so its
    content fills the work area exactly. Rectangles are (left, top, right, bottom); ``frame``
    is the window rectangle around an empty client area (negative left and top)."""
    position = (work[0] - monitor[0] + frame[0], work[1] - monitor[1] + frame[1])
    size = (work[2] - work[0] + frame[2] - frame[0], work[3] - work[1] + frame[3] - frame[1])
    return position, size


def _keep_maximized_in_work_area(hwnd: int) -> None:
    """Windows maximizes a window without a caption over the whole monitor, taskbar included.
    Answer WM_GETMINMAXINFO with the work area instead (after the usual handling)."""
    import ctypes
    from ctypes import wintypes
    user32 = _user32_signatures()

    class MINMAXINFO(ctypes.Structure):
        _fields_ = [("reserved", wintypes.POINT), ("max_size", wintypes.POINT), ("max_position", wintypes.POINT),
                    ("min_track", wintypes.POINT), ("max_track", wintypes.POINT)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    def fit(window, lparam):
        info = MONITORINFO(size=ctypes.sizeof(MONITORINFO))
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(window, 2), ctypes.byref(info)):  # NEAREST
            return
        frame = wintypes.RECT(0, 0, 0, 0)
        style = user32.GetWindowLongPtrW(window, GWL_STYLE) & 0xFFFFFFFF
        extended = user32.GetWindowLongPtrW(window, -20) & 0xFFFFFFFF
        dpi = user32.GetDpiForWindow(window) if hasattr(user32, "GetDpiForWindow") else 0
        if not (dpi and user32.AdjustWindowRectExForDpi(ctypes.byref(frame), style, False, extended, dpi)):
            user32.AdjustWindowRectEx(ctypes.byref(frame), style, False, extended)
        rect = lambda r: (r.left, r.top, r.right, r.bottom)
        position, size = maximized_bounds(rect(info.work), rect(info.monitor), rect(frame))
        limits = MINMAXINFO.from_address(lparam)
        limits.max_position.x, limits.max_position.y = position
        limits.max_size.x, limits.max_size.y = size

    previous = None

    @ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
    def procedure(window, message, wparam, lparam):
        result = user32.CallWindowProcW(previous, window, message, wparam, lparam)
        if message == WM_GETMINMAXINFO:
            try:
                fit(window, lparam)
            except Exception:
                pass  # the default maximized size still applies
        return result

    previous = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(procedure, ctypes.c_void_p).value)
    _SUBCLASSED.append(procedure)


def _prepare_window(window, chrome: dict) -> None:
    """Before the window first shows: drop the native caption, so the first frame already has
    Dikdik's own title bar, and keep maximizing inside the work area."""
    try:
        hwnd = int(window.native.Handle.ToInt64())
    except Exception:
        return  # no native window yet: the WebView2 hook falls back to doing this later
    try:
        _keep_maximized_in_work_area(hwnd)
        _remove_caption(hwnd)
        chrome.update(hwnd=hwnd, caption_removed=True)
    except Exception:
        pass


def window_background() -> str:
    """The colour the window shows before the page paints: the app's own background for the
    system theme, so launching never flashes a different colour."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light = winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
        return "#FBFBFA" if light else "#1A1A19"
    except Exception:
        return "#1A1A19"


def _ready_hook(edgechromium):
    """pywebview's WebView2 class: EdgeChrome in pywebview 6 (BrowserView in older releases)."""
    for name in ("EdgeChrome", "BrowserView"):
        cls = getattr(edgechromium, name, None)
        if cls is not None and hasattr(cls, "on_webview_ready"):
            return cls
    return None


def _enable_native_context_menus(edgechromium=None, chrome: dict | None = None) -> None:
    """Keep the browser right-click menu available in non-debug desktop mode and, given
    ``chrome``, turn the window's title bar into the page's own.

    pywebview 6 enables WebView2's default context menus (copy, paste, select)
    only when debug=True, so copy/paste by right-click is silently unavailable
    in release builds. Wrap the EdgeChromium hook so the menus are normally
    available while debug-only extras (devtools, F12, status bar) stay off.
    A missing or renamed upstream hook is harmless; newer releases may fix it.

    The title bar is replaced only once WebView2 accepts non-client regions: without
    them the page could not move the window, so it keeps the native caption.
    """
    if edgechromium is None:
        if sys.platform != "win32":
            return
        try:
            from webview.platforms import edgechromium
        except Exception:
            return
    cls = _ready_hook(edgechromium)
    if cls is None:
        return
    original = cls.on_webview_ready
    if getattr(original, "_dikdik_context_menus", False):
        return

    @functools.wraps(original)
    def on_webview_ready(view, sender, args):
        original(view, sender, args)
        try:
            sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
        except Exception:
            pass  # The menu is an improvement; a newer pywebview owns its own behavior.
        if chrome is None or chrome.get("custom"):
            return
        try:
            sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True  # CSS app-region: drag
        except Exception:
            # An older WebView2 runtime: the page cannot move the window, so it keeps the native caption.
            chrome["custom"] = False
            if chrome.get("caption_removed") and chrome.get("hwnd"):
                try:
                    _set_caption(chrome["hwnd"], True)
                except Exception:
                    pass
            return
        try:
            if not chrome.get("caption_removed"):  # the window showed before Dikdik could prepare it
                hwnd = int(view.form.Handle.ToInt64())
                _keep_maximized_in_work_area(hwnd)
                _remove_caption(hwnd)
                chrome.update(hwnd=hwnd, caption_removed=True)
            chrome["custom"] = True
        except Exception:
            chrome["custom"] = False

    on_webview_ready._dikdik_context_menus = True
    cls.on_webview_ready = on_webview_ready


def run(server, link: str, data: Path, webview=None) -> None:
    """Run the native GUI on the main thread; stop its server when it exits.

    The caller owns closing the server socket and the current project's engine.
    A stable loopback origin retains browser storage between desktop launches.
    """
    target = urlsplit(link)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != server.server_port:
        raise ValueError("The desktop window requires its own local Dikdik server")
    webview = webview or load_webview()
    chrome: dict = {"custom": False, "hwnd": None, "caption_removed": False}
    _enable_native_context_menus(chrome=chrome if sys.platform == "win32" else None)
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
                              min_size=(760, 560), background_color=window_background(),
                              text_select=True, zoomable=True,
                              **({"js_api": WindowControls(chrome)} if sys.platform == "win32" else {}))
        # Persistent, private-to-Dikdik storage retains theme, drafts and the
        # HttpOnly session cookie. The token remains in a URL fragment only.
        if sys.platform == "win32":
            events = getattr(main_window, "events", None)
            if events is not None and hasattr(events, "before_show"):
                events.before_show += lambda window: _prepare_window(window, chrome)
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
                    hwnd = chrome.get("hwnd") or user32.FindWindowW(None, "Dikdik")
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
