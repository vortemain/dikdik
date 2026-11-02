"""HTTP load testing against the project's own server on a supervised loopback port.

The server is started from the project's command (with `{port}` replaced by a port Dikdik
reserves), requests go only to 127.0.0.1 on that port, and the server is stopped afterwards.
Nothing here can target another host. Results are wall-clock measurements on this machine
under this machine's other load: they compare changes and catch regressions; they are not a
capacity guarantee for production hardware.
"""

from __future__ import annotations

import http.client
import socket
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

from .local_exec import BackgroundShells

MAX_REQUESTS, MAX_CONCURRENCY, MAX_SECONDS = 5000, 50, 120
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return round(ordered[index], 3)


def validate(arguments: dict) -> dict:
    command, path = arguments.get("command"), arguments.get("path", "/")
    if not isinstance(command, str) or "{port}" not in command or len(command) > 4000:
        raise ValueError("Use {port} in the project's server command")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc or any(ord(c) < 32 for c in path):
        raise ValueError("path must be a path on the project's own server, such as /api/items")
    method = str(arguments.get("method", "GET")).upper()
    if method not in METHODS:
        raise ValueError("method must be one of " + ", ".join(sorted(METHODS)))
    requests, concurrency = arguments.get("requests", 200), arguments.get("concurrency", 10)
    if type(requests) is not int or not 1 <= requests <= MAX_REQUESTS:
        raise ValueError(f"requests must be 1-{MAX_REQUESTS}")
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be 1-{MAX_CONCURRENCY}")
    body = arguments.get("body", "")
    if not isinstance(body, str) or len(body) > 65536:
        raise ValueError("body must be text of at most 64 KB")
    return {"command": command, "path": path, "method": method, "requests": requests, "concurrency": concurrency,
            "body": body.encode(), "content_type": str(arguments.get("content_type", "application/json"))[:100],
            "p95_budget_ms": arguments.get("p95_budget_ms"), "max_error_rate": arguments.get("max_error_rate", 0.0)}


def generate(port: int, plan: dict, cancel: threading.Event, deadline: float) -> dict:
    """Send the planned requests at the planned concurrency; collect latencies and outcomes."""
    latencies, statuses, errors = [], {}, {}
    lock = threading.Lock()

    def one(_):
        if cancel.is_set() or time.monotonic() > deadline:
            return
        started = time.perf_counter()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                headers = {"Content-Type": plan["content_type"]} if plan["body"] else {}
                conn.request(plan["method"], plan["path"], body=plan["body"] or None, headers=headers)
                response = conn.getresponse()
                response.read()
                status = response.status
            finally:
                conn.close()
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                latencies.append(elapsed)
                statuses[status] = statuses.get(status, 0) + 1
        except (OSError, http.client.HTTPException) as exc:
            with lock:
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1

    wall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=plan["concurrency"], thread_name_prefix="dikdik-load") as pool:
        list(pool.map(one, range(plan["requests"])))
    wall = time.perf_counter() - wall
    sent = len(latencies) + sum(errors.values())
    failures = sum(errors.values()) + sum(count for status, count in statuses.items() if status >= 500)
    return {"sent": sent, "completed": len(latencies), "status_counts": {str(k): v for k, v in sorted(statuses.items())},
            "errors": errors, "error_rate": round(failures / sent, 4) if sent else 1.0,
            "throughput_rps": round(sent / wall, 1) if wall > 0 else 0.0,
            **({"p50_ms": _percentile(latencies, 0.50), "p95_ms": _percentile(latencies, 0.95), "p99_ms": _percentile(latencies, 0.99),
                "max_ms": round(max(latencies), 3), "mean_ms": round(statistics.fmean(latencies), 3)} if latencies else {}),
            "stopped_early": sent < plan["requests"]}


class ServerUnavailable(Exception):
    """The project's server did not start; ``result`` is the failed tool result to return."""

    def __init__(self, result: dict):
        super().__init__(result.get("error", "server unavailable"))
        self.result = result


def free_port() -> int:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return reserved.getsockname()[1]


@contextmanager
def serve(root, command: str, cancel: threading.Event, env: dict | None = None, *, port: int | None = None,
          ready_seconds: float = 25, shells: BackgroundShells | None = None, **placeholders):
    """Start the project's server on a reserved loopback port and stop it on exit.

    ``{port}`` and any other ``{name}`` placeholder in ``command`` are replaced; yields
    ``(port, shells, shell_id)`` so callers can check the server is still running.
    """
    port = port or free_port()
    text = command.replace("{port}", str(port))
    for name, value in placeholders.items():
        text = text.replace("{" + name + "}", str(value))
    owned = shells is None
    shells = shells or BackgroundShells()
    try:
        started = shells.start(root, text, env=env)
        ready_by = time.monotonic() + ready_seconds
        while True:
            if cancel.is_set():
                raise ServerUnavailable({"status": "cancelled", "error": "Cancelled"})
            state = shells.poll(started["id"])
            if state["status"] != "running":
                raise ServerUnavailable({"status": "failed", "error": "The server exited before becoming ready", "output": state["output"][-4000:]})
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= ready_by:
                    raise ServerUnavailable({"status": "failed", "error": f"The server did not listen on its assigned port within {ready_seconds:g} seconds",
                                             "output": shells.poll(started["id"])["output"][-4000:]})
                cancel.wait(0.1)
        yield port, shells, started["id"]
    finally:
        if owned:
            shells.close()


def run(root, arguments: dict, cancel: threading.Event, env: dict | None = None) -> dict:
    plan = validate(arguments)
    try:
        with serve(root, plan["command"], cancel, env) as (port, _, _):
            measured = generate(port, plan, cancel, time.monotonic() + MAX_SECONDS)
    except ServerUnavailable as exc:
        return exc.result
    results = []
    budget = plan["p95_budget_ms"]
    if budget is not None and "p95_ms" in measured:
        within = measured["p95_ms"] <= budget
        results.append({"name": f"load {plan['method']} {plan['path']}", "check": "latency", "status": "PASS" if within else "OVER_BUDGET",
                        "detail": f"{plan['method']} {plan['path']} p95 {measured['p95_ms']:.0f} ms {'<=' if within else '>'} {budget:.0f} ms at concurrency {plan['concurrency']}"})
    allowed = plan["max_error_rate"]
    ok = measured["error_rate"] <= allowed
    results.append({"name": f"load {plan['method']} {plan['path']}", "check": "errors", "status": "PASS" if ok else "ERRORS",
                    "detail": f"{plan['method']} {plan['path']} error rate {measured['error_rate']:.1%} (allowed {allowed:.1%}) over {measured['sent']} requests"})
    return {"status": "passed" if all(r["status"] == "PASS" for r in results) and not measured["stopped_early"] else "failed",
            "method": plan["method"], "path": plan["path"], "requests": plan["requests"], "concurrency": plan["concurrency"],
            **measured, "results": results,
            "limitations": "Loopback load from this machine against the project's own server; not a production capacity test."}
