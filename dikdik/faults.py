"""Fault injection between the project's service and one of its dependencies, on loopback only.

The dependency (a stub API, a database, a second service of the project) is started on a port
Dikdik reserves; the application is started with ``{dependency_port}`` pointing at a fault proxy
in between. Each scenario switches the proxy into one failure mode, checks how the application
answers, then clears the fault and checks it recovers:

- ``latency``: every connection is delayed by ``latency_ms`` before data flows.
- ``reset``: connections are accepted and reset (TCP RST) immediately.
- ``close``: connections are accepted and closed cleanly before any data (an early EOF).
- ``blackhole``: connections are accepted and nothing is ever answered (a network partition).
- ``down``: the proxy stops accepting (connection refused, as when the dependency is restarting).

The application passes a scenario when it answers within ``max_ms``, with a status the caller
accepts for a degraded dependency (by default anything below 500, or 502/503/504), the process
is still running, and after the fault is cleared it answers successfully again within
``recovery_seconds``. Clock skew, disk faults and multi-hop topologies are not simulated.
"""

from __future__ import annotations

import http.client
import socket
import struct
import threading
import time
from urllib.parse import urlsplit

from . import loadtest

MODES = {"latency", "reset", "close", "blackhole", "down"}
MAX_SCENARIOS = 8


class FaultProxy:
    """A TCP proxy on 127.0.0.1 whose behaviour can be switched while it runs."""

    def __init__(self, upstream_port: int, port: int | None = None):
        self.upstream_port = upstream_port
        self.port = port or loadtest.free_port()
        self.mode, self.latency_ms = "none", 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._open: set[socket.socket] = set()
        self.connections = 0
        self._thread = threading.Thread(target=self._serve, name="dikdik-fault-proxy", daemon=True)

    def start(self) -> "FaultProxy":
        self._listen()
        self._thread.start()
        return self

    def _listen(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(64)
        listener.settimeout(0.2)
        with self._lock:
            self._listener = listener

    def set(self, mode: str, latency_ms: int = 0) -> None:
        if mode not in MODES | {"none"}:
            raise ValueError("Unknown fault mode")
        with self._lock:
            previous, self.mode, self.latency_ms = self.mode, mode, latency_ms
            listener = self._listener
            if mode == "down" and listener is not None:
                self._listener = None
            stale = list(self._open) if mode in {"reset", "close", "blackhole", "down"} else []
        if mode == "down" and listener is not None:
            _stop_listening(listener)
        if previous == "down" and mode != "down":
            self._listen()
        for sock in stale:  # a new fault also breaks connections the application is reusing
            self._abort(sock)

    def _abort(self, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        with self._lock:
            self._open.discard(sock)

    def _serve(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                listener = self._listener
            if listener is None:
                self._stop.wait(0.05)
                continue
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                self._stop.wait(0.05)
                continue
            with self._lock:
                mode, latency = self.mode, self.latency_ms
                self.connections += 1
            if mode == "reset":
                self._abort(client)
            elif mode == "close":
                threading.Thread(target=self._eof, args=(client,), daemon=True).start()
            elif mode == "blackhole":
                with self._lock:
                    self._open.add(client)  # held open, never answered, closed on stop
            else:
                threading.Thread(target=self._relay, args=(client, latency), daemon=True).start()

    @staticmethod
    def _eof(client: socket.socket) -> None:
        """A clean early EOF: closing with unread data would send a reset instead on some systems."""
        try:
            client.shutdown(socket.SHUT_WR)
            client.settimeout(1)
            while client.recv(65536):
                pass
        except OSError:
            pass
        finally:
            client.close()

    def _relay(self, client: socket.socket, latency_ms: int) -> None:
        if latency_ms:
            time.sleep(latency_ms / 1000)
        try:
            upstream = socket.create_connection(("127.0.0.1", self.upstream_port), timeout=10)
        except OSError:
            self._abort(client)
            return
        with self._lock:
            self._open.update((client, upstream))

        def pump(source, sink):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    sink.sendall(data)
            except OSError:
                pass
            finally:
                for sock in (source, sink):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        other = threading.Thread(target=pump, args=(upstream, client), daemon=True)
        other.start()
        pump(client, upstream)
        other.join(30)
        for sock in (client, upstream):
            try:
                sock.close()
            except OSError:
                pass
            with self._lock:
                self._open.discard(sock)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            listener, self._listener = self._listener, None
            sockets = list(self._open)
        if listener is not None:
            _stop_listening(listener)
        for sock in sockets:
            self._abort(sock)
        self._thread.join(2)


def validate(arguments: dict) -> dict:
    command, dependency = arguments.get("command"), arguments.get("dependency_command")
    if not isinstance(command, str) or "{port}" not in command or "{dependency_port}" not in command or len(command) > 4000:
        raise ValueError("command starts the application; use {port} for its port and {dependency_port} for the dependency it calls")
    if not isinstance(dependency, str) or "{port}" not in dependency or len(dependency) > 4000:
        raise ValueError("dependency_command starts the dependency; use {port} for its port")
    path = arguments.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc or any(ord(c) < 32 for c in path):
        raise ValueError("path must be a path on the application, such as /api/orders")
    scenarios = arguments.get("scenarios") or [{"fault": mode} for mode in ("latency", "reset", "blackhole", "down")]
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise ValueError(f"scenarios must list 1-{MAX_SCENARIOS} faults")
    checked = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.get("fault") not in MODES or set(scenario) - {"fault", "latency_ms"}:
            raise ValueError("Each scenario is {fault: latency|reset|close|blackhole|down, latency_ms?}")
        latency = scenario.get("latency_ms", 2000 if scenario["fault"] == "latency" else 0)
        if type(latency) is not int or not 0 <= latency <= 30000:
            raise ValueError("latency_ms must be 0-30000")
        checked.append({"fault": scenario["fault"], "latency_ms": latency})
    max_ms = arguments.get("max_ms", 5000)
    recovery = arguments.get("recovery_seconds", 10)
    if type(max_ms) is not int or not 100 <= max_ms <= 60000:
        raise ValueError("max_ms must be 100-60000")
    if type(recovery) is not int or not 1 <= recovery <= 60:
        raise ValueError("recovery_seconds must be 1-60")
    accepted = arguments.get("degraded_statuses", [])
    if not isinstance(accepted, list) or any(type(s) is not int or not 100 <= s <= 599 for s in accepted):
        raise ValueError("degraded_statuses lists HTTP status codes")
    return {"command": command, "dependency_command": dependency, "path": path, "scenarios": checked,
            "max_ms": max_ms, "recovery_seconds": recovery, "degraded_statuses": set(accepted)}


def _request(port: int, path: str, timeout: float) -> dict:
    started = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        response.read(65536)
        return {"status": response.status, "ms": round((time.perf_counter() - started) * 1000, 1)}
    except socket.timeout:
        return {"status": 0, "error": "no answer (timed out)", "ms": round((time.perf_counter() - started) * 1000, 1)}
    except (OSError, http.client.HTTPException) as exc:
        return {"status": 0, "error": type(exc).__name__, "ms": round((time.perf_counter() - started) * 1000, 1)}
    finally:
        conn.close()


def _stop_listening(listener: socket.socket) -> None:
    """Refuse new connections now. On Linux a bare close() waits for the accept loop's poll to
    return, and the kernel meanwhile completes handshakes it then resets, instead of refusing."""
    try:
        listener.shutdown(socket.SHUT_RDWR)
    except OSError:  # Windows: a listening socket is not connected; close() alone refuses there
        pass
    listener.close()


def _degraded_ok(status: int, accepted: set[int]) -> bool:
    if accepted:
        return status in accepted
    return 0 < status < 500 or status in {502, 503, 504}


def run(root, arguments: dict, cancel: threading.Event, env: dict | None = None) -> dict:
    plan = validate(arguments)
    shells = loadtest.BackgroundShells()
    proxy = None
    results, scenarios = [], []
    try:
        with loadtest.serve(root, plan["dependency_command"], cancel, env, shells=shells) as (dependency_port, _, _):
            proxy = FaultProxy(dependency_port).start()
            with loadtest.serve(root, plan["command"], cancel, env, shells=shells, dependency_port=proxy.port) as (port, _, app):
                healthy = _request(port, plan["path"], plan["max_ms"] / 1000)
                if not 200 <= healthy["status"] < 400:
                    return {"status": "failed", "error": f"The application did not answer {plan['path']} successfully with the dependency healthy",
                            "healthy": healthy, "output": shells.poll(app)["output"][-4000:]}
                for scenario in plan["scenarios"]:
                    if cancel.is_set():
                        return {"status": "cancelled", "error": "Fault test cancelled", "scenarios": scenarios}
                    proxy.set(scenario["fault"], scenario["latency_ms"])
                    during = _request(port, plan["path"], plan["max_ms"] / 1000 + 1)
                    alive = shells.poll(app)["status"] == "running"
                    proxy.set("none")
                    recovered, deadline = None, time.monotonic() + plan["recovery_seconds"]
                    while time.monotonic() < deadline and not cancel.is_set():
                        recovered = _request(port, plan["path"], 5)
                        if 200 <= recovered["status"] < 400:
                            break
                        cancel.wait(0.25)
                    answered = during["status"] > 0 and during["ms"] <= plan["max_ms"]
                    if scenario["fault"] == "latency" and scenario["latency_ms"] < plan["max_ms"]:
                        handled = answered and (200 <= during["status"] < 400 or _degraded_ok(during["status"], plan["degraded_statuses"]))
                    else:
                        handled = answered and _degraded_ok(during["status"], plan["degraded_statuses"])
                    recovered_ok = recovered is not None and 200 <= recovered["status"] < 400
                    problems = ([] if answered else [f"no answer within {plan['max_ms']} ms ({during.get('error', 'too slow')})"]) + \
                               ([] if not answered or handled else [f"answered HTTP {during['status']}, not a handled degradation"]) + \
                               ([] if alive else ["the application process exited"]) + \
                               ([] if recovered_ok else [f"did not recover within {plan['recovery_seconds']} s after the fault cleared"])
                    label = scenario["fault"] + (f" {scenario['latency_ms']} ms" if scenario["fault"] == "latency" else "")
                    scenarios.append({"fault": label, "during": during, "process_alive": alive, "recovered": recovered,
                                      "status": "PASS" if not problems else "FAIL", "problems": problems})
                    results.append({"name": f"fault {label}", "check": "resilience", "status": "PASS" if not problems else "FAIL",
                                    "detail": f"{label}: " + ("; ".join(problems) if problems else
                                              f"answered HTTP {during['status']} in {during['ms']:.0f} ms and recovered")})
                    if not alive:
                        break
    except loadtest.ServerUnavailable as exc:
        return exc.result
    finally:
        if proxy is not None:
            proxy.close()
        shells.close()
    return {"status": "passed" if all(r["status"] == "PASS" for r in results) else "failed", "path": plan["path"],
            "scenarios": scenarios, "results": results,
            "limitations": "One application and one dependency on loopback; clock skew, disk faults and multi-hop topologies are not simulated."}
