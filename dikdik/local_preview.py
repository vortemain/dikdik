"""Supervised project preview on one ephemeral loopback port, with real browser evidence."""
import socket
import time
from urllib.parse import urlsplit

from .host_browser import PREVIEW_HOST
from .interactive_browser import BrowserSessions, available
from .local_exec import BackgroundShells


def preview(root, command, cancel, *, path="/", actions=None, expect_text="", save_screenshot=None, env=None):
    if not isinstance(command, str) or "{port}" not in command or len(command) > 4000:
        raise ValueError("Use {port} in the project's server command, for example npm run dev -- --host 127.0.0.1 --port {port}")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path) or urlsplit(path).netloc:
        raise ValueError("Preview path must stay on the project's preview origin")
    if not isinstance(expect_text, str) or len(expect_text) > 2000:
        raise ValueError("Expected text must contain at most 2000 characters")
    actions = [] if actions is None else actions
    if not isinstance(actions, list) or len(actions) > 12:
        raise ValueError("Provide at most twelve browser actions")
    for step in actions:
        if (not isinstance(step, dict) or set(step) - {"action", "selector", "value", "key"}
                or step.get("action") not in {"click", "fill", "select", "press"}
                or any(not isinstance(value, str) or len(value) > 2048 for value in step.values())):
            raise ValueError("Use bounded click, fill, select or press actions")
    if not available():
        return {"status": "blocked", "error": "Interactive Chromium support is unavailable in this installation"}
    # Choose an unused port ourselves; ordinary browsing never gains localhost access.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    shells, sessions = BackgroundShells(), BrowserSessions(preview_port=port)
    views, issues = [], []
    started = None
    try:
        started = shells.start(root, command.replace("{port}", str(port)), env=env)
        deadline = time.monotonic() + 25
        while True:
            if cancel.is_set():
                return {"status": "cancelled", "error": "Preview cancelled"}
            state = shells.poll(started["id"])
            if state["status"] != "running":
                return {"status": "failed", "error": "The preview server exited before becoming ready", "output": state["output"][-6000:]}
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    return {"status": "failed", "error": "Preview server did not listen on its assigned port within 25 seconds", "output": state["output"][-6000:]}
                cancel.wait(0.1)

        def capture(action, arguments, label):
            result = sessions.execute("preview", action, arguments, cancel)
            png = result.pop("png", None)
            if result.get("status") != "passed":
                raise ValueError(result.get("error", "Browser action failed"))
            if png and save_screenshot:
                result["screenshot"] = save_screenshot({**result, "label": label}, png)
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
            return result

        url = f"http://{PREVIEW_HOST}{path}"
        current = capture("navigate", {"url": url, "viewport": {"width": 1440, "height": 900}}, "desktop")
        for index, step in enumerate(actions):
            current = sessions.execute("preview", step["action"], {**{key: value for key, value in step.items() if key != "action"}, "capture_screenshot": False}, cancel)
            if current.get("status") != "passed":
                raise ValueError(current.get("error", "Browser interaction failed"))
        if expect_text:
            assertion = sessions.execute("preview", "wait_text", {"text": expect_text, "capture_screenshot": False}, cancel)
            if assertion.get("status") != "passed":
                issues.append("Expected interaction result was not visible: " + expect_text)
        if actions:
            current = capture("screenshot", {}, "interaction result")
        if expect_text and expect_text not in current.get("text", ""):
            issues.append("Expected interaction result was not visible: " + expect_text)
        capture("resize", {"width": 390, "height": 844}, "mobile")
        compact_views = [{**view, "text": view.get("text", "")[:2000], "elements": view.get("elements", [])[:20]} for view in views]
        return {"status": "failed" if issues else "passed", "views": compact_views, "issues": list(dict.fromkeys(issues)),
                "server_port": port,
                "interactions_checked": len(actions), "expected_text_checked": bool(expect_text),
                "note": "Measured browser rendering and supplied interactions only. Screenshots are retained; aesthetic quality and unexercised features are not automatically proven.",
                "untrusted": True}
    except Exception as exc:
        return {"status": "cancelled" if cancel.is_set() else "failed", "error": str(exc)[:1500], "views": views}
    finally:
        try:
            sessions.close()
        finally:
            shells.close()
