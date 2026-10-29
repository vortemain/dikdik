"""Persistent, task-scoped Chromium pages. All Playwright calls stay on one thread."""
from concurrent.futures import Future
import importlib.util
import os
import queue
import threading
from urllib.parse import urljoin

from .browser_network import fetch_public, public_url
from .host_browser import PublicProxy, find_browser, page_text


def available():
    return importlib.util.find_spec("playwright") is not None


def web_fetch(url):
    """Bounded public GET with redirect validation and no browser/account cookies."""
    import base64
    url = public_url(url)
    for _ in range(6):
        response = fetch_public(url)
        if 300 <= response["status"] < 400 and response["headers"].get("location"):
            url = public_url(urljoin(url, response["headers"]["location"]))
            continue
        mime = response["headers"]["content-type"]
        if not (mime.startswith("text/") or "json" in mime or "xml" in mime):
            raise ValueError("web_fetch supports text, HTML, XML and JSON resources")
        raw = base64.b64decode(response["body"]).decode("utf-8", errors="replace")
        title, text = page_text(raw) if "html" in mime else ("", raw[:20000])
        return {"url": url, "status": response["status"], "title": title, "text": text,
                "content_type": mime, "untrusted": True, "truncated": len(raw) > 20000}
    raise ValueError("Too many web redirects")


ELEMENTS = """() => Array.from(document.querySelectorAll('a[href],button,input,textarea,select,[role=button],[contenteditable=true]')).filter(e=>e.getClientRects().length).slice(0,100).map(e=>{
 const path=[]; let n=e;
 while(n && n.nodeType===1){ if(n.id){path.unshift('#'+CSS.escape(n.id));break;}
 const tag=n.tagName.toLowerCase(); let i=1,p=n.previousElementSibling;while(p){if(p.tagName===n.tagName)i++;p=p.previousElementSibling;}path.unshift(tag+':nth-of-type('+i+')');n=n.parentElement;}
 return {selector:path.join(' > '),tag:e.tagName.toLowerCase(),type:e.type||'',text:(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||'').slice(0,160)};
})"""


class BrowserSessions:
    def __init__(self):
        self._queue = queue.Queue(maxsize=16)
        self._thread = None
        self._lock = threading.Lock()
        self._closed = False

    def execute(self, task, action, arguments, cancel=None):
        if not available():
            raise ValueError("Interactive browser support is not installed. Install Dikdik desktop support.")
        with self._lock:
            if self._closed:
                raise ValueError("Browser service is closed")
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="dikdik-browser", daemon=True)
                self._thread.start()
            future = Future()
            try:
                self._queue.put_nowait((future, task, action, arguments, cancel))
            except queue.Full:
                raise ValueError("Browser is busy; wait for the current action") from None
        return future.result(timeout=75)

    def close(self):
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread:
            self._queue.put(None, timeout=75)
            thread.join(timeout=80)
            if thread.is_alive():
                raise RuntimeError("Interactive browser did not stop")

    def discard(self, task):
        if self._thread is not None and not self._closed:
            self.execute(task, "close", {})

    def _work(self):
        playwright = browser = proxy = None
        pages = {}
        try:
            while (job := self._queue.get()) is not None:
                future, task, action, args, cancel = job
                try:
                    if action == "close":
                        state = pages.pop(task, None)
                        if state: state["context"].close()
                        future.set_result({"status": "passed"})
                        continue
                    if cancel is not None and cancel.is_set():
                        future.set_result({"status": "cancelled", "error": "Browser action cancelled"})
                        continue
                    if playwright is None:
                        import playwright as playwright_package
                        from pathlib import Path
                        packaged = Path(playwright_package.__file__).parent / "driver" / "package" / ".local-browsers"
                        import sys
                        if getattr(sys, "frozen", False):
                            executable_dir = Path(sys.executable).parent
                            packaged = executable_dir.parent / "Resources" / "browser" if sys.platform == "darwin" else executable_dir / "browser"
                            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(packaged))
                        if packaged.is_dir():
                            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
                        from playwright.sync_api import sync_playwright
                        playwright = sync_playwright().start()
                    if browser is None:
                        if proxy:
                            proxy.close()
                        proxy = PublicProxy(allow_http_writes=True)
                        # A packaged Chromium is preferred. Source installations may use Edge/Chrome.
                        from pathlib import Path
                        shell_installed = any(packaged.glob("chromium_headless_shell-*"))
                        executable = None if Path(playwright.chromium.executable_path).is_file() or shell_installed else find_browser()
                        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                            **({"executable_path": executable} if executable else {}),
                            proxy={"server": proxy.address, "bypass": "<-loopback>"},
                            args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                                  "--proxy-bypass-list=<-loopback>"])
                    if task not in pages:
                        if action != "navigate":
                            raise ValueError("Open a page with browse_url before interacting with it")
                        if len(pages) >= 2:
                            old = pages.pop(next(iter(pages)))
                            old["context"].close()
                        context = browser.new_context(viewport=self._viewport(args.get("viewport")),
                            accept_downloads=False, service_workers="block")
                        state = {"context": context, "page": context.new_page(), "writes": False}
                        def route(request_route, state=state):
                            try:
                                public_url(request_route.request.url)
                                if request_route.request.method not in {"GET", "HEAD", "OPTIONS"} and not state["writes"]:
                                    return request_route.abort()
                                request_route.continue_()
                            except Exception:
                                request_route.abort()
                        context.route("**/*", route)
                        context.route_web_socket("**/*", lambda socket: socket.close())
                        state["page"].set_default_timeout(10000)
                        state["page"].on("dialog", lambda dialog: dialog.dismiss())
                        # Popups stay inside the managed page, never in another desktop window.
                        def popup(page, state=state):
                            state["page"] = page
                            if len(state["context"].pages) > 4:
                                state["context"].pages[0].close()
                            page.set_default_timeout(10000)
                            page.on("dialog", lambda dialog: dialog.dismiss())
                        context.on("page", popup)
                        pages[task] = state
                    state = pages[task]
                    page = state["page"]
                    if action in {"click", "fill", "type", "press", "select"}:
                        state["writes"] = True
                    selector = args.get("selector")
                    if selector is not None and (not isinstance(selector, str) or not 0 < len(selector) <= 2048):
                        raise ValueError("Provide a CSS selector of at most 2048 characters")
                    if action == "navigate":
                        if args.get("viewport"):
                            page.set_viewport_size(self._viewport(args["viewport"]))
                        page.goto(public_url(args.get("url")), wait_until="domcontentloaded", timeout=45000)
                    elif action == "resize":
                        page.set_viewport_size(self._viewport(args))
                    elif action == "click":
                        if selector:
                            page.locator(selector).click()
                        else:
                            size = page.viewport_size
                            page.mouse.click(self._number(args, "x", 0, size["width"] - 1), self._number(args, "y", 0, size["height"] - 1))
                    elif action == "fill":
                        if not selector:
                            raise ValueError("A CSS selector is required for fill")
                        page.locator(selector).fill(self._text(args, "value"))
                    elif action == "type":
                        page.keyboard.insert_text(self._text(args, "text"))
                    elif action == "press":
                        key = self._text(args, "key", 80)
                        if key not in {"Enter", "Tab", "Shift+Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", "ControlOrMeta+A"}:
                            raise ValueError("Unsupported browser key")
                        page.keyboard.press(key)
                    elif action == "select":
                        page.locator(selector).select_option(self._text(args, "value"))
                    elif action == "scroll":
                        page.mouse.wheel(self._number(args, "x", -3000, 3000), self._number(args, "y", -3000, 3000))
                    elif action == "back":
                        page.go_back(wait_until="domcontentloaded", timeout=30000)
                    elif action == "forward":
                        page.go_forward(wait_until="domcontentloaded", timeout=30000)
                    elif action == "reload":
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                    elif action != "screenshot":
                        raise ValueError("Unsupported browser action")
                    if cancel is not None and cancel.is_set():
                        state["context"].close(); pages.pop(task, None)
                        future.set_result({"status": "cancelled", "error": "Browser action cancelled"})
                        continue
                    page = state["page"]
                    page.wait_for_timeout(120)
                    url = public_url(page.url)
                    png = page.locator(selector).screenshot(timeout=10000) if action == "screenshot" and selector else page.screenshot(timeout=10000)
                    if len(png) > 8 * 1024 * 1024:
                        raise ValueError("Browser screenshot exceeded 8 MiB")
                    future.set_result({"status": "passed", "url": url, "title": page.title()[:300],
                        "text": page.locator("body").inner_text(timeout=5000)[:20000],
                        "elements": page.evaluate(ELEMENTS), "png": png,
                        "interactive": not (action == "screenshot" and selector), "untrusted": True})
                except Exception as exc:
                    if browser and not browser.is_connected():
                        browser = None; pages.clear()
                    future.set_result({"status": "failed", "error": str(exc)[:1500]})
        finally:
            if browser:
                browser.close()
            if playwright:
                playwright.stop()
            if proxy:
                proxy.close()

    @staticmethod
    def _viewport(value):
        if value is None:
            return {"width": 1440, "height": 900}
        if not isinstance(value, dict):
            raise ValueError("Provide viewport width and height")
        return {"width": int(BrowserSessions._number(value, "width", 320, 1920)),
                "height": int(BrowserSessions._number(value, "height", 240, 1200))}

    @staticmethod
    def _text(args, key, limit=8000):
        value = args.get(key)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{key} must be text of at most {limit} characters")
        return value

    @staticmethod
    def _number(args, key, low, high):
        value = args.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        return value
