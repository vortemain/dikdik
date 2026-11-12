"""Supervised project preview on one ephemeral loopback port, with real browser evidence."""
import hashlib
import json
import logging
import os
import re
import shlex
import socket
import sys
import threading
import time
import weakref
from pathlib import Path
from urllib.parse import urlsplit

from . import accessibility, logs
from .host_browser import PREVIEW_HOST, connect_loopback
from .interactive_browser import BrowserSessions, available
from .local_exec import BackgroundShells

# Cold starts of SSR dev servers (Vite, Nitro, Next) optimise dependencies first and
# can take far longer than a static server; giving up early failed real projects.
READY_SECONDS = 90
# A plain HTML/CSS/JS project needs no dev server of its own: Dikdik's Python serves it, so the
# preview neither downloads one (npx http-server) nor depends on what is on PATH.
STATIC_SERVER = ("python" if os.name == "nt" else shlex.quote(sys.executable)) + " -m http.server {port} --bind 127.0.0.1"
# "open http://...", "start", a bare URL: attempts to show the page rather than serve it.
OPENER = re.compile(r"^\s*(?:(?:open|start|xdg-open|explorer(?:\.exe)?)\b|https?://)", re.I)


def server_command(root, command):
    """The command that serves the project on {port}. A static site may omit it; a literal port
    (--port 3000, -p 8080, http.server 5173) becomes the assigned one."""
    if (command is None or (isinstance(command, str) and (not command.strip() or OPENER.match(command)))) \
            and (Path(root) / "index.html").is_file():
        return STATIC_SERVER
    if isinstance(command, str) and "{port}" not in command:
        command = re.sub(r"(?<!\S)(--port[= ]|-p |-l |PORT=)\d{2,5}\b", lambda m: m.group(1) + "{port}", command, count=1)
        command = re.sub(r"(-m\s+http\.server\s+)\d{2,5}\b", lambda m: m.group(1) + "{port}", command, count=1)
        if "{port}" not in command and re.search(r"-m\s+http\.server\s*$", command):
            command += " {port}"
        # Next.js, Vite, Astro and Nuxt all take --port; only npm needs "--" to pass it through.
        if "{port}" not in command and re.fullmatch(r"\s*npm\s+run\s+dev\s*", command):
            command = command.strip() + " -- --port {port}"
        elif "{port}" not in command and re.fullmatch(r"\s*(?:bun|pnpm|yarn)\s+(?:run\s+)?dev\s*", command):
            command = command.strip() + " --port {port}"
    return command


WARM_SECONDS = 240
# Servers that pick up file changes by themselves (dev servers, a static file server); a
# production server such as `next start` serves its last build, so it is started fresh.
RELOADING = re.compile(r"\bdev\b|\bwatch\b|vite|--reload|runserver|flask\s+run|\bserve\b|-m\s+http\.server", re.I)


_LIVE: "weakref.WeakSet[LiveServer]" = weakref.WeakSet()


def stop_all() -> None:
    """Stop every kept dev server; called before Dikdik exits without the usual shutdown (an update)."""
    for live in list(_LIVE):
        try:
            live.stop()
        except Exception:  # stopping the others matters more than one failure
            logs.log("live_preview_stop_failed", logging.WARNING, exc_info=True)


class LiveServer:
    """The project's dev server, kept running after a preview so the Browser panel shows the real
    page and the next preview reuses it instead of starting and compiling again. One per project;
    a different command replaces it, and Dikdik stops it when the project closes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._current: dict | None = None
        _LIVE.add(self)

    def reuse(self, root, command):
        """The port of a running server for the same project and command that reloads by itself."""
        with self._lock:
            current = self._current
        if not current or current["root"] != str(root) or current["command"] != command or not RELOADING.search(command):
            return None
        if current["shells"].poll(current["id"])["status"] != "running":
            self.stop()
            return None
        return current["port"]

    def adopt(self, shells, shell_id, port, command, root, path) -> None:
        with self._lock:
            previous, self._current = self._current, {"shells": shells, "id": shell_id, "port": port, "command": command,
                                                       "root": str(root), "path": path, "started": time.time()}
        if previous and previous["shells"] is not shells:
            previous["shells"].close()

    def visited(self, path) -> None:
        with self._lock:
            if self._current:
                self._current["path"] = path

    def output(self) -> str:
        with self._lock:
            current = self._current
        return current["shells"].poll(current["id"]).get("output", "")[-4000:] if current else ""

    def status(self) -> dict:
        with self._lock:
            current = self._current
        if not current:
            return {"running": False}
        running = current["shells"].poll(current["id"])["status"] == "running"
        return {"running": running, "port": current["port"], "path": current["path"], "command": current["command"],
                "started": current["started"]}

    def stop(self) -> None:
        with self._lock:
            current, self._current = self._current, None
        if current:
            current["shells"].close()


def warm_up(port, path, cancel, timeout=WARM_SECONDS):
    """Request the page once before the browser does. Dev servers (next dev, Vite, Nuxt) compile a
    route on its first request, often for longer than a navigation should wait; afterwards the
    browser gets the compiled page at once. A failure here is left for the browser to report."""
    import http.client

    done = threading.Event()

    def fetch():
        try:
            connection = connect_loopback(port, timeout)
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
            client.sock = connection
            client.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Accept": "text/html"})
            client.getresponse().read()
            client.close()
        except Exception:  # best effort: the browser's own request reports what is wrong
            logs.log("preview_warm_up_failed", logging.DEBUG, exc_info=True)
        finally:
            done.set()

    threading.Thread(target=fetch, name="dikdik-preview-warm-up", daemon=True).start()
    deadline = time.monotonic() + timeout
    while not done.wait(0.2):
        if cancel.is_set() or time.monotonic() >= deadline:
            return


def page_path(path):
    """The URL path to open. Models often pass the project folder (".", "./") or a file name."""
    if path is None or (isinstance(path, str) and path.strip() in ("", ".", "./")):
        return "/"
    if isinstance(path, str) and not path.startswith("/") and "://" not in path and "\\" not in path:
        return "/" + path.removeprefix("./")
    return path


def _visible(value):
    return bool(value) and not str(value).startswith("0px") and " none " not in f" {value} "


def seams(inspection):
    """What visibly separates each inspected element from the next one: a bottom border, a
    shadow, a gap or a change of background. Spelled out so a "make it seamless" change is
    checked against every cause, not only the first one noticed."""
    found = []
    for width, items in (inspection or {}).items():
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or "next_sibling" not in item:
                continue
            styles, causes = item.get("styles", {}), []
            if _visible(styles.get("border-bottom")):
                causes.append(f"border-bottom {styles['border-bottom']}")
            if _visible(item.get("next_sibling_border_top")):
                causes.append(f"next element border-top {item['next_sibling_border_top']}")
            if styles.get("box-shadow"):
                causes.append(f"box-shadow {styles['box-shadow']}")
            if item.get("gap_to_next_sibling"):
                causes.append(f"{item['gap_to_next_sibling']}px gap")
            if item.get("visible_background") != item.get("next_sibling_background"):
                causes.append(f"background changes {item.get('visible_background')} -> {item.get('next_sibling_background')}")
            found.append({"width": width, "between": f"{item['selector']} | {item['next_sibling']}",
                          "visible_separation": causes or "none"})
    return found


# Steps a preview can take, as a person would. "type" presses keys one by one (masks and
# validation run); "fill" sets the value at once. A step names its element by CSS selector,
# role and name, label, placeholder or visible text.
STEP_ACTIONS = {"click", "dblclick", "fill", "type", "select", "press", "check", "uncheck", "hover"}
STEP_KEYS = {"action", "selector", "role", "name", "label", "placeholder", "text", "value", "key", "expect"}


def _step_arguments(step):
    """The browser action and arguments for one preview step."""
    action = step["action"]
    args = {key: step[key] for key in ("selector", "role", "name", "label", "placeholder", "text") if key in step}
    if action == "dblclick":
        return "click", {**args, "double": True}
    if action == "type":  # a field is named by selector, role, label or placeholder; "value" is what to type
        args.pop("text", None)
        return "type", {**args, "text": step.get("value", "")}
    if action in {"fill", "select"}:
        return action, {**args, "value": step.get("value", "")}
    if action == "press":
        return "press", {**args, "key": step.get("key", "")}
    return action, args


def _describe_step(step):
    target = step.get("selector") or (f'{step["role"]} "{step.get("name", "")}"' if step.get("role") else "") \
        or step.get("label") or step.get("placeholder") or step.get("text") or step.get("key") or ""
    return f'{step["action"]} {target}'.strip()


def preview(root, command, cancel, *, path="/", actions=None, expect_text="", save_screenshot=None, env=None,
            focus="", inspect=None, visual=None, live: LiveServer | None = None, color_scheme=None):
    """``focus`` screenshots one element (the part being designed) at desktop and mobile width;
    ``inspect`` reports exact computed styles and boxes for up to eight selectors at both widths;
    ``visual(view, png)``, when given, compares each capture with the accepted baseline for
    that view (``path#label``; interaction results also key on the actions performed).
    With ``live``, a running server for the same command is reused and the server is kept
    running afterwards for the Browser panel; without it the server is stopped as before."""
    command, path = server_command(root, command), page_path(path)
    if not isinstance(command, str) or "{port}" not in command or len(command) > 4000:
        raise ValueError("command is the command that starts the project's dev server, with {port} for the port Dikdik "
                         "assigns, for example npm run dev -- --host 127.0.0.1 --port {port}. preview_app starts and "
                         "stops it; do not start a server yourself. A static site with index.html needs no command.")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path) or urlsplit(path).netloc:
        raise ValueError("path is the page's URL path on the preview server, such as / or /about")
    if not isinstance(expect_text, str) or len(expect_text) > 2000:
        raise ValueError("Expected text must contain at most 2000 characters")
    actions = [] if actions is None else actions
    inspect = [] if inspect is None else inspect
    if not isinstance(focus, str) or len(focus) > 300:
        raise ValueError("focus must be one CSS selector of at most 300 characters")
    if not isinstance(inspect, list) or len(inspect) > 8 or not all(isinstance(item, str) and 0 < len(item) <= 300 for item in inspect):
        raise ValueError("inspect takes at most eight CSS selectors")
    if not isinstance(actions, list) or len(actions) > 20:
        raise ValueError("Provide at most twenty browser actions")
    for step in actions:
        if (not isinstance(step, dict) or set(step) - STEP_KEYS
                or step.get("action") not in STEP_ACTIONS
                or any(not isinstance(value, str) or len(value) > 2048 for value in step.values())):
            raise ValueError("Use bounded " + ", ".join(sorted(STEP_ACTIONS)) + " steps")
    if color_scheme not in {None, "light", "dark"}:
        raise ValueError("color_scheme is light or dark")
    if not available():
        return {"status": "blocked", "error": "Interactive Chromium support is unavailable in this installation"}
    # Choose an unused port ourselves; ordinary browsing never gains localhost access.
    reused = live.reuse(root, command) if live is not None else None
    if reused:
        port = reused
    else:
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
    shells, sessions = (None if reused else BackgroundShells()), BrowserSessions(preview_port=port)
    views, issues, audits, compared, steps = [], [], [], [], []
    acted = hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()[:12]
    started, ready = None, bool(reused)
    try:
        if not reused:
            started = shells.start(root, command.replace("{port}", str(port)), env=env)
            deadline = time.monotonic() + READY_SECONDS
            while True:
                if cancel.is_set():
                    return {"status": "cancelled", "error": "Preview cancelled"}
                state = shells.poll(started["id"])
                if state["status"] != "running":
                    return {"status": "failed", "error": "The preview server exited before becoming ready", "output": state["output"][-6000:]}
                try:
                    with connect_loopback(port, 0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        return {"status": "failed", "error": f"Preview server did not listen on its assigned port within {READY_SECONDS} seconds", "output": state["output"][-6000:]}
                    cancel.wait(0.1)
            ready = True
        warm_up(port, path, cancel)

        def capture(action, arguments, label):
            result = sessions.execute("preview", action, {**arguments, "audit": True}, cancel)
            png = result.pop("png", None)
            if result.get("status") != "passed":
                raise ValueError(result.get("error", "Browser action failed"))
            if png and save_screenshot:
                result["screenshot"] = save_screenshot({**result, "label": label}, png)
            if png and visual is not None:
                check = visual(f"{path}#{label}" + (f"#{acted}" if label == "interaction result" else ""), png)
                if isinstance(check, dict):
                    compared.append(check)
                    result["visual"] = check.get("verdict")
            result["label"] = label
            views.append(result)
            if result.get("runtime_errors"):
                issues.append(label + ": JavaScript runtime errors")
            if result.get("http_errors"):
                issues.append(label + ": failed HTTP resources")
            layout = result.get("layout", {})
            if layout.get("scroll_width", 0) > layout.get("width", 0) + 1:
                issues.append(label + ": horizontal overflow")
            if not result.get("text", "").strip() and not result.get("elements") and not layout.get("visible_elements"):
                issues.append(label + ": no visible text or controls")
            summary = accessibility.summarize(result.get("accessibility"))
            if summary["serious"]:
                issues.append(f"{label}: accessibility failures ({', '.join(summary['failing_rules'])})")
            audits.append({"label": label, **summary,
                           "violations": (result.get("accessibility") or {}).get("violations", [])[:20]})
            return result

        url = f"http://{PREVIEW_HOST}{path}"
        current = capture("navigate", {"url": url, "viewport": {"width": 1440, "height": 900}}, "desktop")
        # Each step is checked as it happens: a step that fails, or whose expected text does not
        # appear, is reported by number with a capture of the page at that moment, and the run stops there.
        stopped = False
        for index, step in enumerate(actions, 1):
            action, arguments = _step_arguments(step)
            done = sessions.execute("preview", action, {**arguments, "observe": "none"}, cancel)
            record = {"step": index, "did": _describe_step(step), "status": done.get("status")}
            if done.get("status") == "passed" and step.get("expect"):
                seen = sessions.execute("preview", "wait", {"text": step["expect"], "timeout": 10, "observe": "none"}, cancel)
                record["expected"] = step["expect"]
                if seen.get("status") != "passed":
                    record["status"], done = "failed", {"error": f"expected text did not appear: {step['expect']}"}
            if record["status"] != "passed":
                record["error"] = str(done.get("error", "Browser interaction failed"))[:400]
                issues.append(f"step {index} ({record['did']}) failed: {record['error'].splitlines()[0][:200]}")
                steps.append(record)
                try:
                    current = capture("screenshot", {}, f"failed step {index}")
                except ValueError:
                    pass
                stopped = True
                break
            steps.append(record)
        if expect_text and not stopped:
            assertion = sessions.execute("preview", "wait_text", {"text": expect_text, "capture_screenshot": False}, cancel)
            if assertion.get("status") != "passed":
                issues.append("Expected interaction result was not visible: " + expect_text)
        if actions and not stopped:
            current = capture("screenshot", {}, "interaction result")
        if expect_text and not stopped and expect_text not in current.get("text", ""):
            issues.append("Expected interaction result was not visible: " + expect_text)
        inspection = {}

        def examine(width):
            if focus:
                try:
                    capture("screenshot", {"selector": focus}, f"{width} focus")
                except ValueError as exc:
                    issues.append(f"{width}: focus selector {focus!r} could not be captured: {str(exc)[:200]}")
            if inspect:
                found = sessions.execute("preview", "inspect", {"selectors": inspect, "capture_screenshot": False}, cancel)
                inspection[width] = found.get("inspection") if found.get("status") == "passed" else {"error": found.get("error", "inspection failed")}

        examine("desktop")
        capture("resize", {"width": 390, "height": 844}, "mobile")
        examine("mobile")
        if color_scheme == "dark":
            capture("resize", {"width": 1440, "height": 900, "color_scheme": "dark"}, "dark")
            examine("dark")
        # What the page said and sent during the run: console errors and the data forms posted.
        console = sessions.execute("preview", "console", {"level": "warning", "limit": 20}, cancel).get("console", [])
        sent = sessions.execute("preview", "network", {"only_sent": True, "limit": 10}, cancel).get("network", [])
        compact_views = [{**view, "text": view.get("text", "")[:2000], "elements": view.get("elements", [])[:20]} for view in views]
        # A 500 page or a script error is explained in the dev server's own console, not the page.
        broken = any(view.get("runtime_errors") or any(e.get("status", 0) >= 500 for e in view.get("http_errors", [])) for view in views)
        server_output = ((live.output() if reused else shells.poll(started["id"]).get("output", "")[-4000:]) if broken else "")
        return {"status": "failed" if issues else "passed", "views": compact_views, "issues": list(dict.fromkeys(issues)),
                "server_port": port, **({"server_reused": True} if reused else {}),
                **({"server_output": server_output} if server_output else {}),
                **({"inspection": inspection, "seams": seams(inspection)} if inspection else {}),
                "interactions_checked": sum(step["status"] == "passed" for step in steps),
                "expected_text_checked": bool(expect_text) and not stopped or bool(steps) and not stopped and any("expected" in step for step in steps),
                **({"steps": steps} if steps else {}),
                **({"console": console} if console else {}), **({"sent_requests": sent} if sent else {}),
                "accessibility": audits,
                **({"visual": compared} if visual is not None else {}),
                "note": "Measured browser rendering, an automated WCAG 2.2 subset and supplied interactions only. Screenshots are retained; "
                        "aesthetic quality, full accessibility and unexercised features are not automatically proven.",
                "untrusted": True}
    except Exception as exc:
        reason = str(exc) or ("The browser did not finish loading the page in time (the dev server may still be compiling)"
                              if isinstance(exc, TimeoutError) else type(exc).__name__)
        return {"status": "cancelled" if cancel.is_set() else "failed", "error": reason[:1500], "views": views}
    finally:
        # A browser that is slow to shut down must not replace the finished result with an
        # exception. A server that came up is kept for the Browser panel when asked to; any
        # other is stopped.
        try:
            sessions.close()
        except Exception:
            logs.log("preview_browser_close_failed", logging.WARNING, exc_info=True)
        finally:
            if reused:
                live.visited(path)
            elif live is not None and ready and not cancel.is_set() and shells.poll(started["id"])["status"] == "running":
                live.adopt(shells, started["id"], port, command, root, path)
            elif shells is not None:
                shells.close()
