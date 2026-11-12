"""Persistent, task-scoped Chromium pages. All Playwright calls stay on one thread.

The agent and the Browser panel share one page per task. An element is named by a ref from
``snapshot`` (the page's accessibility tree), by role and name, label, placeholder or text, or
by a CSS selector. Typing sends real key events, one character at a time, so input masks,
key handlers and validation run as they do for a person. Each page records its console and
network traffic, and while someone watches the panel a CDP screencast streams its frames live.
"""
from concurrent.futures import Future
from contextlib import contextmanager
import base64
import importlib.util
import json
import os
import queue
import re
import threading
from urllib.parse import urljoin, urlsplit

from .accessibility import AUDIT
from .browser_network import fetch_public, public_url
from .browser_snapshot import REF_ATTRIBUTE, SNAPSHOT
from .host_browser import PREVIEW_HOST, PublicProxy, find_browser, page_text


def available():
    return importlib.util.find_spec("playwright") is not None


def web_fetch(url):
    """Bounded public GET with redirect validation and no browser/account cookies."""
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


# Exact rendered facts for design checks: what a screenshot shows, as numbers. A "no line
# between the navbar and the page" request is verified by these borders, shadows,
# backgrounds and gaps, not by looking alone.
STYLE_PROPERTIES = ["display", "position", "top", "z-index", "width", "height", "margin-top", "margin-bottom",
                    "padding-top", "padding-bottom", "padding-left", "padding-right", "border-top", "border-bottom",
                    "border-left", "border-right", "border-radius", "box-shadow", "outline", "background-color",
                    "background-image", "backdrop-filter", "opacity", "color", "font-family", "font-size",
                    "font-weight", "line-height", "letter-spacing", "text-align", "gap", "grid-template-columns",
                    "flex-direction", "justify-content", "align-items", "overflow"]
INSPECT = """([selectors, properties]) => selectors.map(selector => {
 let nodes; try { nodes = document.querySelectorAll(selector); } catch (e) { return {selector, error: 'invalid selector'}; }
 if (!nodes.length) return {selector, found: 0};
 const e = nodes[0], r = e.getBoundingClientRect(), s = getComputedStyle(e), styles = {};
 for (const p of properties) { const v = s.getPropertyValue(p); if (v && v !== 'none' && v !== 'normal' && v !== 'auto' && v !== '0px' && v !== 'rgba(0, 0, 0, 0)' && !v.startsWith('0px none')) styles[p] = v; }
 const next = e.nextElementSibling, n = next && next.getBoundingClientRect();
 // The colour a person sees: the first non-transparent background up the tree.
 const seen = el => { for (let x = el; x; x = x.parentElement) { const c = getComputedStyle(x).backgroundColor; if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; } return 'rgb(255, 255, 255)'; };
 return {selector, found: nodes.length, tag: e.tagName.toLowerCase(),
  box: {x: Math.round(r.x), y: Math.round(r.y + scrollY), width: Math.round(r.width), height: Math.round(r.height)},
  styles, text: (e.innerText || '').trim().slice(0, 200), visible_background: seen(e),
  ...(n ? {gap_to_next_sibling: Math.round(n.top - r.bottom), next_sibling: next.tagName.toLowerCase() + (next.className && typeof next.className === 'string' ? '.' + next.className.trim().split(/\\s+/).join('.') : ''),
           next_sibling_background: seen(next), next_sibling_border_top: getComputedStyle(next).borderTop} : {})};
})"""

REF = re.compile(r"e\d{1,7}")
ROLE = re.compile(r"[a-z]{2,24}")
MODIFIERS = {"Alt", "Control", "ControlOrMeta", "Meta", "Shift"}
NAMED_KEYS = ("Enter|Tab|Backspace|Delete|Escape|ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Home|End|PageUp|PageDown|"
              "Insert|Space|F[1-9]|F1[0-2]")
KEY = re.compile(rf"(?:(?:Alt|Control|ControlOrMeta|Meta|Shift)\+){{0,3}}(?:{NAMED_KEYS}|[!-~])")
# Actions that may change a page or send data. Public pages may send form data only after one.
WRITE_ACTIONS = {"click", "fill", "type", "press", "select", "check", "uncheck", "drag", "evaluate"}
# Actions that read what the page recorded instead of looking at it again.
RECORD_ACTIONS = {"console", "network"}
LOG_LIMIT = 300
WAKE = object()


def _bounded(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


class BrowserSessions:
    def __init__(self, *, preview_port=None):
        self._preview_port = preview_port  # the app's port, or a function giving the running app's port
        self._queue = queue.Queue(maxsize=16)
        self._thread = None
        self._lock = threading.Lock()
        self._closed = False
        self._frames = threading.Condition()
        self._watchers: dict[str, int] = {}
        self._latest: dict[str, tuple[int, bytes]] = {}
        self._open: set[str] = set()
        self._playwright = self._browser = self._proxy = self._packaged = None

    # --- public API (any thread) ---------------------------------------------------------

    def execute(self, task, action, arguments, cancel=None):
        if not available():
            raise ValueError("Interactive browser support is not installed. Install Dikdik desktop support.")
        with self._lock:
            if self._closed:
                raise ValueError("Browser service is closed")
            self._start()
            future = Future()
            try:
                self._queue.put_nowait((future, task, action, arguments, cancel))
            except queue.Full:
                raise ValueError("Browser is busy; wait for the current action") from None
        # Dev servers compile a page on its first request after an edit (Vite SSR, Next),
        # which can take well over a minute on a busy machine.
        return future.result(timeout=150)

    def close(self):
        with self._lock:
            self._closed = True
            thread = self._thread
        with self._frames:
            self._frames.notify_all()
        if thread:
            self._queue.put(None, timeout=150)
            thread.join(timeout=160)
            if thread.is_alive():
                raise RuntimeError("Interactive browser did not stop")

    def discard(self, task):
        if self._thread is not None and not self._closed:
            self.execute(task, "close", {})

    def has_page(self, task):
        with self._frames:
            return task in self._open

    @contextmanager
    def watch(self, task):
        """Stream this task's page while the block runs (the Browser panel's live view)."""
        with self._frames:
            self._watchers[task] = self._watchers.get(task, 0) + 1
        self._wake()
        try:
            yield
        finally:
            with self._frames:
                self._watchers[task] -= 1
                if self._watchers[task] <= 0:
                    del self._watchers[task]
            self._wake()

    def frame(self, task, after=0, timeout=10.0):
        """The newest JPEG frame after ``after`` as ``(seq, jpeg)``, or None when none came in time."""
        with self._frames:
            self._frames.wait_for(lambda: self._closed or self._latest.get(task, (0,))[0] > after, timeout)
            latest = self._latest.get(task)
            return latest if latest and latest[0] > after else None

    @property
    def closed(self):
        return self._closed

    # --- worker thread ----------------------------------------------------------------------

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="dikdik-browser", daemon=True)
            self._thread.start()

    def _wake(self):
        with self._lock:
            if self._thread is None or self._closed:
                return
        try:
            self._queue.put_nowait(WAKE)
        except queue.Full:
            pass  # a queued job will resync the stream anyway

    def _work(self):
        pages = {}
        try:
            while True:
                casting = any(state.get("cast") for state in pages.values())
                try:
                    job = self._queue.get(timeout=0.05) if casting else self._queue.get()
                except queue.Empty:
                    self._pump(pages)  # deliver screencast frames while no action is running
                    continue
                if job is None:
                    break
                if job is not WAKE:
                    future, task, action, args, cancel = job
                    try:
                        future.set_result(self._run(pages, task, action, args or {}, cancel))
                    except Exception as exc:
                        if self._browser and not self._browser.is_connected():
                            self._browser = None
                            pages.clear()
                        future.set_result({"status": "failed", "error": str(exc)[:1500]})
                self._sync_casts(pages)
        finally:
            for state in pages.values():
                self._cast(None, state, False)
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
            if self._proxy:
                self._proxy.close()
            with self._frames:
                self._open.clear()
                self._frames.notify_all()

    def _launch(self):
        if self._playwright is None:
            import sys
            from pathlib import Path
            import playwright as playwright_package
            packaged = Path(playwright_package.__file__).parent / "driver" / "package" / ".local-browsers"
            if getattr(sys, "frozen", False):
                executable_dir = Path(sys.executable).parent
                packaged = executable_dir.parent / "Resources" / "browser" if sys.platform == "darwin" else executable_dir / "browser"
                os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(packaged))
            if packaged.is_dir():
                os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
            self._packaged = packaged
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        if self._browser is None:
            from pathlib import Path
            if self._proxy:
                self._proxy.close()
            self._proxy = PublicProxy(allow_http_writes=True, preview_port=self._preview_port)
            # A packaged Chromium is preferred. Source installations may use Edge/Chrome.
            shell_installed = any(self._packaged.glob("chromium_headless_shell-*"))
            executable = None if Path(self._playwright.chromium.executable_path).is_file() or shell_installed else find_browser()
            self._browser = self._playwright.chromium.launch(headless=True, chromium_sandbox=True,
                **({"executable_path": executable} if executable else {}),
                proxy={"server": self._proxy.address, "bypass": "<-loopback>"},
                args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                      "--proxy-bypass-list=<-loopback>"])

    def _new_state(self, pages, task, args):
        if len(pages) >= 2:
            old = pages.pop(next(iter(pages)))
            self._cast(None, old, False)
            old["context"].close()
        context = self._browser.new_context(viewport=self._viewport(args.get("viewport")),
            accept_downloads=False, service_workers="block")
        state = {"context": context, "page": context.new_page(), "writes": False, "errors": [], "http_errors": [],
                 "console": [], "requests": [], "seq": 0, "console_mark": 0, "network_mark": 0, "dialog": "dismiss",
                 "pending": {}, "cast": None}
        self._observe(state["page"], state)

        # Playwright passes (route, request) to a two-parameter handler, so the state is taken
        # from the enclosing scope: a `state=state` default would receive the request instead.
        def route(request_route):
            try:
                url = request_route.request.url
                public_url(url)
                # The project's own app may post anything (forms, server actions); public sites
                # send data only after an action that is allowed to.
                if (request_route.request.method not in {"GET", "HEAD", "OPTIONS"} and not state["writes"]
                        and urlsplit(url).hostname != PREVIEW_HOST):
                    return request_route.abort()
                request_route.continue_()
            except Exception:
                request_route.abort()
        context.route("**/*", route)
        # Public pages get no WebSockets. The project's own dev server keeps its live-reload
        # socket (tunnelled by the proxy to that server only): closing it made next dev
        # reload the page in a loop and froze the preview for minutes.
        context.route_web_socket(lambda url: urlsplit(url).hostname != PREVIEW_HOST,
                                 lambda socket: socket.close())

        # Popups stay inside the managed page, never in another desktop window.
        def popup(page, state=state):
            state["page"] = page
            self._observe(page, state)
            if len(state["context"].pages) > 4:
                state["context"].pages[0].close()
        context.on("page", popup)
        pages[task] = state
        return state

    def _observe(self, page, state):
        page.set_default_timeout(10000)

        def log(kind, entry):
            records = state[kind]
            state["seq"] += 1
            records.append({"n": state["seq"], **entry})
            if len(records) > LOG_LIMIT:
                del records[0]
            return records[-1]

        def on_error(error):
            if len(state["errors"]) < 20:
                state["errors"].append(str(error)[:500])
            log("console", {"type": "pageerror", "text": _bounded(error, 1000)})

        def on_console(message):
            try:
                where = message.location or {}
            except Exception:
                where = {}
            log("console", {"type": message.type, "text": _bounded(message.text, 1000),
                            **({"source": _bounded(f"{where.get('url', '')}:{where.get('lineNumber', '')}", 300)} if where.get("url") else {})})

        def on_request(request):
            entry = {"method": request.method, "url": _bounded(request.url, 500), "type": request.resource_type}
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                try:
                    body = request.post_data
                except Exception:
                    body = None
                if body:
                    entry["body"] = _bounded(body, 4000)
            state["pending"][request] = log("requests", entry)
            if len(state["pending"]) > LOG_LIMIT:
                state["pending"].pop(next(iter(state["pending"])))

        def on_response(response):
            entry = state["pending"].pop(response.request, None)
            if entry is not None:
                entry["status"] = response.status
            if response.status >= 400 and len(state["http_errors"]) < 20:
                state["http_errors"].append({"url": response.url[:500], "status": response.status})

        def on_failed(request):
            entry = state["pending"].pop(request, None)
            if entry is not None:
                try:
                    entry["failed"] = _bounded(request.failure, 200) or "failed"
                except Exception:
                    entry["failed"] = "failed"

        def on_dialog(dialog):
            log("console", {"type": "dialog", "text": _bounded(f"{dialog.type}: {dialog.message}", 1000),
                            "handled": state["dialog"]})
            if state["dialog"] == "accept":
                dialog.accept(state.get("dialog_text") or "") if dialog.type == "prompt" else dialog.accept()
            else:
                dialog.dismiss()

        page.on("pageerror", on_error)
        page.on("console", on_console)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_failed)
        page.on("dialog", on_dialog)

    # --- live view ------------------------------------------------------------------------

    def _sync_casts(self, pages):
        with self._frames:
            watched = set(self._watchers)
            self._open = set(pages)
            for task in list(self._latest):
                if task not in pages:
                    del self._latest[task]
        for task, state in pages.items():
            try:
                self._cast(task, state, task in watched)
            except Exception:
                state["cast"] = None  # a page that cannot stream still works without it

    def _cast(self, task, state, on):
        page, cast = state["page"], state.get("cast")
        size = page.viewport_size if not page.is_closed() else None
        if cast and (not on or cast["page"] is not page or cast["size"] != size):
            try:
                cast["cdp"].send("Page.stopScreencast")
                cast["cdp"].detach()
            except Exception:
                pass
            state["cast"] = cast = None
        if not on or cast is not None or page.is_closed() or task is None:
            return
        cdp = state["context"].new_cdp_session(page)

        def frame(params, cdp=cdp, task=task):
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass
            self._publish(task, base64.b64decode(params["data"]))
        cdp.on("Page.screencastFrame", frame)
        size = size or {"width": 1440, "height": 900}
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 72, "maxWidth": size["width"], "maxHeight": size["height"]})
        state["cast"] = {"cdp": cdp, "page": page, "size": size}

    def _pump(self, pages):
        for state in pages.values():
            if state.get("cast"):
                try:
                    state["page"].wait_for_timeout(50)  # Playwright delivers events only inside its own calls
                except Exception:
                    state["cast"] = None
                return

    def _publish(self, task, jpeg):
        with self._frames:
            seq = self._latest.get(task, (0, b""))[0] + 1
            self._latest[task] = (seq, jpeg)
            self._frames.notify_all()

    # --- actions ------------------------------------------------------------------------------

    def _run(self, pages, task, action, args, cancel):
        if action == "close":
            state = pages.pop(task, None)
            if state:
                self._cast(None, state, False)
                state["context"].close()
            return {"status": "passed"}
        if cancel is not None and cancel.is_set():
            return {"status": "cancelled", "error": "Browser action cancelled"}
        self._launch()
        if task not in pages:
            if action != "navigate":
                raise ValueError("Open a page with browse_url before interacting with it")
            self._new_state(pages, task, args)
        state = pages[task]
        page = state["page"]
        if action in WRITE_ACTIONS:
            state["writes"] = True
        if args.get("dialog") in {"accept", "dismiss"}:
            state["dialog"] = args["dialog"]
            state["dialog_text"] = self._text(args, "dialog_text", 2000) if "dialog_text" in args else ""
        extra = {}
        if action in RECORD_ACTIONS:
            return self._records(state, action, args)
        if action == "navigate":
            state["errors"].clear(); state["http_errors"].clear()
            if args.get("viewport"):
                page.set_viewport_size(self._viewport(args["viewport"]))
            self._scheme(page, state, args)
            page.goto(public_url(args.get("url")), wait_until="domcontentloaded", timeout=90000)
        elif action == "resize":
            if "width" in args or "height" in args:
                page.set_viewport_size(self._viewport(args))
            self._scheme(page, state, args)
        elif action == "click":
            target = self._target(page, args, required=False)
            button = args.get("button", "left")
            if button not in {"left", "right", "middle"}:
                raise ValueError("button is left, right or middle")
            count = 2 if args.get("double") is True else 1
            modifiers = self._modifiers(args)
            if target is not None:
                target.click(button=button, click_count=count, modifiers=modifiers or None)
            else:
                x, y = self._point(page, args)
                for key in modifiers:
                    page.keyboard.down(key)
                try:
                    if "detail" in args:  # one press of a person's multi-click (the panel): detail 2 is a double-click's second
                        detail = int(self._number(args, "detail", 1, 3))
                        page.mouse.move(x, y)
                        page.mouse.down(button=button, click_count=detail)
                        page.mouse.up(button=button, click_count=detail)
                    else:
                        page.mouse.click(x, y, button=button, click_count=count)
                finally:
                    for key in reversed(modifiers):
                        page.keyboard.up(key)
        elif action == "hover":
            target = self._target(page, args, required=False)
            if target is not None:
                target.hover()
            else:
                page.mouse.move(*self._point(page, args))
        elif action == "drag":
            source = self._target(page, args, required=False)
            destination = args.get("to")
            if source is not None and isinstance(destination, dict):
                source.drag_to(self._target(page, destination))
            else:
                x, y = self._point(page, args)
                to = destination if isinstance(destination, dict) else {}
                tx, ty = self._point(page, to)
                page.mouse.move(x, y); page.mouse.down()
                page.mouse.move(tx, ty, steps=12); page.mouse.up()
        elif action == "fill":
            self._target(page, args).fill(self._text(args, "value"))
        elif action == "type":
            text = self._text(args, "text")
            target = self._target(page, args, required=False, skip={"text"})  # "text" is what to type
            if target is not None and args.get("clear") is True:
                target.fill("")
            if args.get("insert") is True:  # a paste: one input event, as a clipboard paste sends
                if target is not None:
                    target.focus()
                page.keyboard.insert_text(text)
            elif target is not None:
                target.press_sequentially(text)  # real keydown/keypress/input/keyup per character
            else:
                page.keyboard.type(text)
            if args.get("submit") is True:
                page.keyboard.press("Enter")
        elif action == "press":
            key = self._text(args, "key", 80)
            if not KEY.fullmatch(key):
                raise ValueError("Unsupported browser key; use a key name such as Enter, Tab, Escape, ArrowDown, "
                                 "F5 or a character, optionally with Control+, Shift+, Alt+, Meta+ or ControlOrMeta+")
            target = self._target(page, args, required=False)
            (target.press if target is not None else page.keyboard.press)(key)
        elif action == "select":
            values = args.get("values")
            if values is not None:
                if not isinstance(values, list) or not 0 < len(values) <= 20 or not all(isinstance(v, str) and len(v) <= 500 for v in values):
                    raise ValueError("values lists 1-20 option values or labels")
                self._target(page, args).select_option(values)
            else:
                self._target(page, args).select_option(self._text(args, "value"))
        elif action in {"check", "uncheck"}:
            self._target(page, args).set_checked(action == "check")
        elif action == "scroll":
            target = self._target(page, args, required=False)
            if target is not None and "x" not in args and "y" not in args:
                target.scroll_into_view_if_needed()
            else:
                page.mouse.wheel(self._number(args, "x", -3000, 3000), self._number(args, "y", -3000, 3000))
        elif action == "wait_text":
            page.get_by_text(self._text(args, "text", 2000), exact=False).first.wait_for(state="visible", timeout=10000)
        elif action == "wait":
            timeout = self._number(args, "timeout", 0, 30) * 1000 if "timeout" in args else 10000
            if "text" in args:
                page.get_by_text(self._text(args, "text", 2000), exact=False).first.wait_for(state="visible", timeout=timeout)
            elif "text_gone" in args:
                page.get_by_text(self._text(args, "text_gone", 2000), exact=False).first.wait_for(state="hidden", timeout=timeout)
            elif "seconds" in args:
                page.wait_for_timeout(self._number(args, "seconds", 0, 10) * 1000)
            else:
                target = self._target(page, args)
                state_name = args.get("state", "visible")
                if state_name not in {"visible", "hidden", "attached", "detached"}:
                    raise ValueError("state is visible, hidden, attached or detached")
                target.first.wait_for(state=state_name, timeout=timeout)
        elif action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=30000)
        elif action == "forward":
            page.go_forward(wait_until="domcontentloaded", timeout=30000)
        elif action == "reload":
            page.reload(wait_until="domcontentloaded", timeout=30000)
        elif action == "inspect":
            selectors = args.get("selectors")
            if not isinstance(selectors, list) or not 1 <= len(selectors) <= 8 or not all(isinstance(s, str) and 0 < len(s) <= 300 for s in selectors):
                raise ValueError("Inspect 1-8 CSS selectors (or refs) of at most 300 characters")
            found = page.evaluate(INSPECT, [[f'[{REF_ATTRIBUTE}="{s}"]' if REF.fullmatch(s) else s for s in selectors], STYLE_PROPERTIES])
            extra["inspection"] = [{**item, "selector": asked} for item, asked in zip(found, selectors)]  # as the agent named them
        elif action == "evaluate":
            extra["result"] = self._evaluate(page, args)
        elif action not in {"screenshot", "snapshot"}:
            raise ValueError("Unsupported browser action")
        if cancel is not None and cancel.is_set():
            self._cast(None, state, False)
            state["context"].close(); pages.pop(task, None)
            return {"status": "cancelled", "error": "Browser action cancelled"}
        return self._outcome(state, action, args, extra)

    def _outcome(self, state, action, args, extra):
        observe = args.get("observe", "full")
        if observe not in {"full", "snapshot", "none"}:
            raise ValueError("observe is full, snapshot or none")
        state["page"].wait_for_timeout(120 if observe != "none" else 40)
        # An action that starts a navigation (a link, a submitted form) replaces the document
        # while it is being read; wait for the new one and read it instead.
        for attempt in range(3):
            try:
                return self._observe_page(state, action, args, extra, observe)
            except Exception as exc:
                if attempt == 2 or not any(sign in str(exc) for sign in ("Execution context was destroyed", "navigat", "Target closed")):
                    raise
                try:
                    state["page"].wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass

    def _observe_page(self, state, action, args, extra, observe):
        page = state["page"]
        url = public_url(page.url)
        png = None
        if observe != "none" and args.get("capture_screenshot") is not False or action == "screenshot":
            png = self._screenshot(page, action, args)
        outcome = {"status": "passed", "url": url, "title": page.title()[:300], "untrusted": True,
                   "interactive": not (action == "screenshot" and (args.get("selector") or args.get("ref") or args.get("region"))),
                   "png": png, **extra}
        if observe == "full":
            outcome.update({"text": page.locator("body").inner_text(timeout=5000)[:20000],
                            "elements": page.evaluate(ELEMENTS),
                            "runtime_errors": list(state["errors"]), "http_errors": list(state["http_errors"])})
        if observe != "none":
            outcome["layout"] = page.evaluate("() => ({width: innerWidth, height: innerHeight, scroll_width: document.documentElement.scrollWidth, visible_elements: document.body ? document.body.querySelectorAll('*').length : 0})")
            if state.get("scheme"):
                outcome["layout"]["color_scheme"] = state["scheme"]
        if observe == "snapshot" or action == "snapshot":
            tree = page.evaluate(SNAPSHOT, {"maxLines": int(args.get("max_lines") or 900), "maxChars": 24000 if action == "snapshot" else 12000})
            outcome.update({"snapshot": tree["snapshot"], "snapshot_truncated": tree["truncated"],
                            "scroll": tree["scroll"], **({"open_dialogs": tree["open_dialogs"]} if tree["open_dialogs"] else {})})
        if observe != "none":
            outcome.update(self._news(state))
        if args.get("audit") is True:
            # Previews only: the audit focuses controls, which ordinary browsing must not do.
            outcome["accessibility"] = page.evaluate(AUDIT)
        return outcome

    def _news(self, state):
        """Console problems and failed requests that appeared since the last report."""
        console = [e for e in state["console"] if e["n"] > state["console_mark"] and e["type"] in {"error", "warning", "pageerror", "dialog"}]
        failed = [e for e in state["requests"] if e["n"] > state["network_mark"] and (e.get("failed") or e.get("status", 0) >= 400)]
        sent = [e for e in state["requests"] if e["n"] > state["network_mark"] and e["method"] not in {"GET", "HEAD", "OPTIONS"}]
        state["console_mark"] = state["console"][-1]["n"] if state["console"] else state["console_mark"]
        state["network_mark"] = state["requests"][-1]["n"] if state["requests"] else state["network_mark"]
        strip = lambda e: {k: v for k, v in e.items() if k != "n"}
        return {**({"new_console": [strip(e) for e in console[-15:]]} if console else {}),
                **({"new_failed_requests": [strip(e) for e in failed[-15:]]} if failed else {}),
                **({"sent_requests": [strip(e) for e in sent[-10:]]} if sent else {})}

    def _records(self, state, action, args):
        limit = int(self._number(args, "limit", 1, 200)) if "limit" in args else 50
        pattern = args.get("pattern")
        if pattern is not None and (not isinstance(pattern, str) or len(pattern) > 200):
            raise ValueError("pattern is text of at most 200 characters")
        if action == "console":
            level = args.get("level", "all")
            if level not in {"all", "error", "warning", "info"}:
                raise ValueError("level is all, error, warning or info")
            kinds = {"error": {"error", "pageerror"}, "warning": {"error", "pageerror", "warning", "dialog"}}.get(level)
            entries = [e for e in state["console"] if (kinds is None or e["type"] in kinds) and (not pattern or pattern.lower() in e["text"].lower())]
        else:
            entries = [e for e in state["requests"] if (not pattern or pattern.lower() in e["url"].lower())
                       and (args.get("only_failed") is not True or e.get("failed") or e.get("status", 0) >= 400)
                       and (args.get("only_sent") is not True or e["method"] not in {"GET", "HEAD", "OPTIONS"})]
        if args.get("clear") is True:
            state["console" if action == "console" else "requests"].clear()
        return {"status": "passed", "url": public_url(state["page"].url), "untrusted": True, "total": len(entries),
                action: [{k: v for k, v in e.items() if k != "n"} for e in entries[-limit:]]}

    def _screenshot(self, page, action, args):
        if action == "screenshot" and args.get("region") is not None:
            region = args["region"]
            size = page.viewport_size
            if not isinstance(region, list) or len(region) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in region):
                raise ValueError("region is [x0, y0, x1, y1] in page pixels")
            x0, y0, x1, y1 = (max(0, min(v, limit)) for v, limit in zip(region, (size["width"], size["height"]) * 2))
            if x1 - x0 < 4 or y1 - y0 < 4:
                raise ValueError("region must be at least 4 pixels wide and tall")
            return page.screenshot(clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}, timeout=10000)
        target = self._target(page, args, required=False) if action == "screenshot" else None
        if target is not None:
            return target.screenshot(timeout=10000)
        return page.screenshot(timeout=10000, full_page=action == "screenshot" and args.get("full_page") is True)

    def _evaluate(self, page, args):
        code = self._text(args, "expression", 8000)
        target = self._target(page, args, required=False)
        is_function = bool(re.match(r"\s*(?:async\s+)?(?:function\b|\(?[\w\s,{}\[\]]*\)?\s*=>)", code))
        if target is not None:
            if not is_function:
                raise ValueError("With an element, expression is a function such as el => el.value")
            value = target.evaluate(code)
        else:
            value = page.evaluate(code)
        text = json.dumps(value, default=str)
        return json.loads(text) if len(text) <= 20000 else {"truncated": True, "json": text[:20000]}

    @staticmethod
    def _scheme(page, state, args):
        scheme = args.get("color_scheme")
        if scheme is None:
            return
        if scheme not in {"light", "dark", "no-preference"}:
            raise ValueError("color_scheme is light, dark or no-preference")
        page.emulate_media(color_scheme=scheme)
        state["scheme"] = scheme

    def _target(self, page, args, required=True, skip=()):
        """The element an action names: a snapshot ref, role and name, label, placeholder, text or CSS."""
        ref, selector = args.get("ref"), args.get("selector")
        if ref is not None:
            if not isinstance(ref, str) or not REF.fullmatch(ref):
                raise ValueError("ref is an element id from browser_snapshot, such as e12")
            locator = page.locator(f'[{REF_ATTRIBUTE}="{ref}"]')
            if locator.count() == 0:
                raise ValueError(f"Element {ref} is no longer on the page; take a new browser_snapshot")
            return locator.first
        if selector is not None:
            if not isinstance(selector, str) or not 0 < len(selector) <= 2048:
                raise ValueError("Provide a CSS selector of at most 2048 characters")
            return page.locator(selector)
        if args.get("role") is not None:
            role = args["role"]
            if not isinstance(role, str) or not ROLE.fullmatch(role):
                raise ValueError("role is an ARIA role such as button, link, textbox or checkbox")
            name = args.get("name")
            if name is not None:
                return page.get_by_role(role, name=self._text(args, "name", 300), exact=args.get("exact") is True)
            return page.get_by_role(role)
        for key, finder in (("label", page.get_by_label), ("placeholder", page.get_by_placeholder), ("text", page.get_by_text)):
            if key in args and key not in skip:
                return finder(self._text(args, key, 300), exact=args.get("exact") is True)
        if required:
            raise ValueError("Name the element: ref (from browser_snapshot), role with name, label, placeholder, text or selector")
        return None

    def _point(self, page, args):
        size = page.viewport_size
        return (self._number(args, "x", 0, size["width"] - 1), self._number(args, "y", 0, size["height"] - 1))

    @staticmethod
    def _modifiers(args):
        modifiers = args.get("modifiers") or []
        if not isinstance(modifiers, list) or not set(modifiers) <= MODIFIERS:
            raise ValueError("modifiers lists Alt, Control, ControlOrMeta, Meta or Shift")
        return list(dict.fromkeys(modifiers))

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
