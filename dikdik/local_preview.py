"""Supervised project preview on one ephemeral loopback port, with real browser evidence."""
import hashlib
import json
import re
import socket
import time
from urllib.parse import urlsplit

from . import accessibility
from .host_browser import PREVIEW_HOST, connect_loopback
from .interactive_browser import BrowserSessions, available
from .local_exec import BackgroundShells

# Cold starts of SSR dev servers (Vite, Nitro, Next) optimise dependencies first and
# can take far longer than a static server; giving up early failed real projects.
READY_SECONDS = 90


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


def preview(root, command, cancel, *, path="/", actions=None, expect_text="", save_screenshot=None, env=None,
            focus="", inspect=None, visual=None):
    """``focus`` screenshots one element (the part being designed) at desktop and mobile width;
    ``inspect`` reports exact computed styles and boxes for up to eight selectors at both widths;
    ``visual(view, png)``, when given, compares each capture with the accepted baseline for
    that view (``path#label``; interaction results also key on the actions performed)."""
    if isinstance(command, str) and "{port}" not in command:
        # A literal port flag (serve -l 5173, --port 3000, -p 8080) becomes the assigned port.
        command = re.sub(r"(?<!\S)(--port[= ]|-p |-l |PORT=)\d{2,5}\b", lambda m: m.group(1) + "{port}", command, count=1)
    if not isinstance(command, str) or "{port}" not in command or len(command) > 4000:
        raise ValueError("Use {port} in the project's server command, for example npm run dev -- --host 127.0.0.1 --port {port}")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path) or urlsplit(path).netloc:
        raise ValueError("Preview path must stay on the project's preview origin")
    if not isinstance(expect_text, str) or len(expect_text) > 2000:
        raise ValueError("Expected text must contain at most 2000 characters")
    actions = [] if actions is None else actions
    inspect = [] if inspect is None else inspect
    if not isinstance(focus, str) or len(focus) > 300:
        raise ValueError("focus must be one CSS selector of at most 300 characters")
    if not isinstance(inspect, list) or len(inspect) > 8 or not all(isinstance(item, str) and 0 < len(item) <= 300 for item in inspect):
        raise ValueError("inspect takes at most eight CSS selectors")
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
    views, issues, audits, compared = [], [], [], []
    acted = hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()[:12]
    started = None
    try:
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
        compact_views = [{**view, "text": view.get("text", "")[:2000], "elements": view.get("elements", [])[:20]} for view in views]
        # A 500 page or a script error is explained in the dev server's own console, not the page.
        broken = any(view.get("runtime_errors") or any(e.get("status", 0) >= 500 for e in view.get("http_errors", [])) for view in views)
        server_output = shells.poll(started["id"]).get("output", "")[-4000:] if broken else ""
        return {"status": "failed" if issues else "passed", "views": compact_views, "issues": list(dict.fromkeys(issues)),
                "server_port": port, **({"server_output": server_output} if server_output else {}),
                **({"inspection": inspection, "seams": seams(inspection)} if inspection else {}),
                "interactions_checked": len(actions), "expected_text_checked": bool(expect_text),
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
        # exception; the dev server is stopped either way.
        try:
            sessions.close()
        except Exception:
            pass
        finally:
            shells.close()
