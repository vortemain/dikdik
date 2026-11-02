"""Dynamic security checks (DAST) against the project's own server on a loopback port.

The server is started from the project's command on a port Dikdik reserves; every request goes
to 127.0.0.1 on that port and nothing else can be targeted. Same-origin links and GET forms are
crawled up to a page limit. Checks: response security headers, cookie flags, CORS with
credentials, reflected input (an inert marker, never script), SQL and stack-trace error
disclosure, open redirects, exposed repository and environment files, and directory listings.
Probes are GET requests only; forms that POST are reported but never submitted, so the scan does
not create or change the application's data. This is a bounded smoke scan, not a penetration
test: authenticated areas, stored XSS, business logic and anything outside the crawl are not
covered.
"""

from __future__ import annotations

import http.client
import re
import secrets
import threading
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from . import loadtest

MAX_PAGES, MAX_SECONDS, MAX_BODY = 60, 90, 512 * 1024
PROBE_HOST = "dikdik-probe.invalid"
REDIRECT_PARAMS = {"next", "url", "redirect", "redirect_uri", "redirect_url", "return", "returnto", "return_to", "continue", "dest", "destination", "goto"}
SENSITIVE = [("/.git/HEAD", re.compile(r"^ref: refs/"), "the Git repository metadata"),
             ("/.git/config", re.compile(r"\[core\]"), "the Git repository configuration"),
             ("/.env", re.compile(r"(?m)^[A-Z][A-Z0-9_]*=\S"), "an environment file with settings or secrets")]
LISTING = re.compile(r"<title>\s*(Index of /|Directory listing for /)", re.I)
TRACE = re.compile(r"Traceback \(most recent call last\)|at [\w$.<>]+ \([^)]*:\d+:\d+\)|Exception in thread|"
                   r"java\.lang\.\w+Exception|System\.\w+Exception|<b>Fatal error</b>|Werkzeug Debugger")
SQL_ERROR = re.compile(r"sqlite3\.OperationalError|SQLSTATE\[|syntax error at or near|You have an error in your SQL syntax|"
                       r"unrecognized token:|ORA-\d{5}|SqlException|psycopg2\.errors|near \"[^\"]*\": syntax error", re.I)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.forms, self._form = [], [], None

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v or "" for k, v in attrs}
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "form":
            self._form = {"action": attrs.get("action", ""), "method": attrs.get("method", "get").lower(), "fields": []}
            self.forms.append(self._form)
        elif tag in {"input", "textarea", "select"} and self._form is not None and attrs.get("name"):
            if attrs.get("type", "").lower() not in {"submit", "button", "image", "file", "reset"}:
                self._form["fields"].append(attrs["name"])

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def validate(arguments: dict) -> dict:
    command = arguments.get("command")
    if not isinstance(command, str) or "{port}" not in command or len(command) > 4000:
        raise ValueError("Use {port} in the project's server command")
    paths = arguments.get("paths", ["/"])
    if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
        raise ValueError("paths must list 1-20 start paths")
    for path in paths:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc or any(ord(c) < 32 for c in path):
            raise ValueError("Each path must be a path on the project's own server, such as /search?q=x")
    pages = arguments.get("max_pages", 30)
    if type(pages) is not int or not 1 <= pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be 1-{MAX_PAGES}")
    return {"command": command, "paths": paths, "max_pages": pages}


class _Client:
    def __init__(self, port: int, deadline: float, cancel: threading.Event):
        self.port, self.deadline, self.cancel, self.requests = port, deadline, cancel, 0

    def get(self, target: str, headers: dict | None = None) -> dict | None:
        if self.cancel.is_set() or time.monotonic() > self.deadline:
            return None
        self.requests += 1
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", target, headers={"Host": f"127.0.0.1:{self.port}", "User-Agent": "dikdik-dast", **(headers or {})})
            response = conn.getresponse()
            body = response.read(MAX_BODY)
            return {"status": response.status, "headers": {k.lower(): v for k, v in response.getheaders()},
                    "cookies": response.msg.get_all("Set-Cookie") or [], "body": body.decode("utf-8", "replace")}
        except (OSError, http.client.HTTPException) as exc:
            return {"status": 0, "headers": {}, "cookies": [], "body": "", "error": type(exc).__name__}
        finally:
            conn.close()


def _target(base: str, link: str, port: int) -> str | None:
    """A same-origin path+query for a link on ``base``, or None for other origins and schemes."""
    absolute = urljoin(f"http://127.0.0.1:{port}{base}", link.strip())
    parts = urlsplit(absolute)
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"} or (parts.port or 80) != port:
        return None
    return urlunsplit(("", "", parts.path or "/", parts.query, ""))


def scan(port: int, plan: dict, cancel: threading.Event) -> dict:
    client = _Client(port, time.monotonic() + MAX_SECONDS, cancel)
    findings: dict[tuple, dict] = {}

    def report(rule, severity, path, message, evidence=""):
        key = (rule, path.split("?")[0])
        findings.setdefault(key, {"rule": rule, "severity": severity, "path": path, "message": message, "evidence": evidence[:300]})

    queue, seen, pages, forms = list(plan["paths"]), set(), [], []
    while queue and len(pages) < plan["max_pages"]:
        target = queue.pop(0)
        if target in seen:
            continue
        seen.add(target)
        response = client.get(target)
        if response is None:
            break
        pages.append((target, response))
        if "text/html" in response["headers"].get("content-type", "") and 200 <= response["status"] < 300:
            parsed = _Page()
            try:
                parsed.feed(response["body"])
            except Exception:
                continue
            for link in parsed.links:
                found = _target(target, link, port)
                if found and found not in seen:
                    queue.append(found)
            for form in parsed.forms:
                forms.append((target, form))

    marker = "dkx" + secrets.token_hex(4)
    for target, response in pages:
        headers, body, status = response["headers"], response["body"], response["status"]
        html = "text/html" in headers.get("content-type", "")
        if html and 200 <= status < 300:
            csp = headers.get("content-security-policy", "")
            if not csp:
                report("DAST001", "medium", target, "HTML response without a Content-Security-Policy header")
            if "x-frame-options" not in headers and "frame-ancestors" not in csp:
                report("DAST003", "medium", target, "Page can be framed by other sites (no X-Frame-Options or CSP frame-ancestors)")
        if status and "nosniff" not in headers.get("x-content-type-options", "").lower():
            report("DAST002", "low", target, "Response without X-Content-Type-Options: nosniff")
        for cookie in response["cookies"]:
            name = cookie.split("=", 1)[0].strip()
            flags = cookie.lower()
            if "httponly" not in flags:
                report("DAST004", "medium", target, f"Cookie {name} is readable by page scripts (no HttpOnly)", cookie.split(";", 1)[0][:40] + "...")
            if "samesite" not in flags:
                report("DAST005", "low", target, f"Cookie {name} has no SameSite attribute")
        if status >= 500 or TRACE.search(body):
            match = TRACE.search(body)
            report("DAST006", "medium", target, f"Server error page{' exposes a stack trace' if match else f' (HTTP {status})'}",
                   match.group(0) if match else "")
        if LISTING.search(body):
            report("DAST009", "low", target, "Directory listing is enabled")
        server = headers.get("server", "") + " " + headers.get("x-powered-by", "")
        if re.search(r"\d+\.\d+", server):
            report("DAST011", "info", target, "Server software version is disclosed in response headers", server.strip())

    # Reflected input and injection probes on query parameters and GET forms.
    probes = []
    for target, _ in pages:
        parts = urlsplit(target)
        params = parse_qsl(parts.query, keep_blank_values=True)
        if params:
            probes.append((parts.path, params, target))
    for page, form in forms:
        action = _target(page, form["action"] or page, port)
        if action is None or not form["fields"]:
            continue
        if form["method"] != "get":
            report("DAST012", "info", page, f"{form['method'].upper()} form to {action.split('?')[0]} was not submitted (the scan never changes data)")
            continue
        probes.append((urlsplit(action).path, [(field, "x") for field in form["fields"]], page))
    tried = set()
    for path, params, origin in probes[:40]:
        names = tuple(sorted(name for name, _ in params))
        if (path, names) in tried:
            continue
        tried.add((path, names))
        for index, (name, _) in enumerate(params):
            values = list(params)
            payload = f"{marker}<i\"'>"
            values[index] = (name, payload)
            response = client.get(path + "?" + urlencode(values))
            if response is None:
                break
            if payload in response["body"] and "text/html" in response["headers"].get("content-type", ""):
                report("DAST007", "high", f"{path}?{name}=", f"Parameter {name} is reflected into HTML without escaping (reflected XSS)",
                       response["body"][max(0, response["body"].find(payload) - 60):response["body"].find(payload) + 40])
            values[index] = (name, "1'\"")
            response = client.get(path + "?" + urlencode(values))
            if response is not None and SQL_ERROR.search(response["body"]):
                report("DAST008", "high", f"{path}?{name}=", f"A quote in parameter {name} produces a database error (likely SQL injection)",
                       SQL_ERROR.search(response["body"]).group(0))
            if response is not None and (response["status"] >= 500 or TRACE.search(response["body"])):
                match = TRACE.search(response["body"]) or SQL_ERROR.search(response["body"])
                report("DAST006", "medium", f"{path}?{name}=", f"Unexpected input in {name} causes a server error (HTTP {response['status']})"
                       + (" that exposes internals" if match else ""), match.group(0) if match else "")
            if name.lower() in REDIRECT_PARAMS:
                values[index] = (name, f"https://{PROBE_HOST}/")
                response = client.get(path + "?" + urlencode(values))
                location = (response or {}).get("headers", {}).get("location", "")
                if response is not None and 300 <= response["status"] < 400 and urlsplit(urljoin("http://127.0.0.1/", location)).hostname == PROBE_HOST:
                    report("DAST010", "medium", f"{path}?{name}=", f"Parameter {name} redirects to any external site (open redirect)", location)

    for path, pattern, what in SENSITIVE:
        response = client.get(path)
        if response is not None and response["status"] == 200 and pattern.search(response["body"][:4096]):
            report("DAST013", "critical", path, f"{path} is served and exposes {what}")

    start = plan["paths"][0]
    response = client.get(start, {"Origin": f"https://{PROBE_HOST}"})
    if response is not None:
        allowed = response["headers"].get("access-control-allow-origin", "")
        credentials = response["headers"].get("access-control-allow-credentials", "").lower() == "true"
        if allowed == f"https://{PROBE_HOST}" and credentials:
            report("DAST014", "high", start, "Any site may read responses with the user's cookies (CORS reflects Origin with credentials)")
        elif allowed == f"https://{PROBE_HOST}":
            report("DAST014", "medium", start, "CORS reflects any Origin")

    ordered = sorted(findings.values(), key=lambda f: (SEVERITY_ORDER[f["severity"]], f["rule"], f["path"]))
    counts = {level: sum(1 for f in ordered if f["severity"] == level) for level in SEVERITY_ORDER}
    return {"pages": [p for p, _ in pages], "forms": len(forms), "requests": client.requests, "findings": ordered, "summary": counts,
            "stopped_early": cancel.is_set() or time.monotonic() > client.deadline}


def run(root, arguments: dict, cancel: threading.Event, env: dict | None = None) -> dict:
    plan = validate(arguments)
    try:
        with loadtest.serve(root, plan["command"], cancel, env) as (port, _, _):
            result = scan(port, plan, cancel)
    except loadtest.ServerUnavailable as exc:
        return exc.result
    blocking = result["summary"]["critical"] + result["summary"]["high"]
    return {"status": "failed" if blocking else "passed", **result,
            "limitations": "GET-only crawl of the project's own loopback server; authenticated areas, POST handlers, stored XSS "
                           "and business logic are not covered."}
