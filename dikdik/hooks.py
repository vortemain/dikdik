"""Policy-preserving lifecycle hooks. Plugin hooks are validated data, never code.

Only the trusted host may register Python callbacks. Every consumer must still
enforce its normal permissions after an ALLOW; a BLOCK is sticky for a dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, TypedDict
import uuid


class HookEvent(StrEnum):
    TASK_STARTED = "task.started"
    CONTEXT_BUILDING = "context.building"
    TOOL_BEFORE = "tool.before"
    TOOL_AFTER = "tool.after"
    TOOL_FAILED = "tool.failed"
    TASK_COMPLETED = "task.completed"


class Decision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


class HookAuditError(RuntimeError):
    """The guard could not durably record its decision."""


class TaskStartedPayload(TypedDict):
    objective: str
    mode: str


class ContextBuildingPayload(TaskStartedPayload):
    pass


class ToolBeforePayload(TypedDict):
    tool: str
    call_id: str
    arguments: dict


class ToolAfterPayload(ToolBeforePayload):
    result: dict


class ToolFailedPayload(ToolBeforePayload):
    result: dict


class TaskCompletedPayload(TypedDict):
    state: str
    error: str | None


EventPayload = TaskStartedPayload | ContextBuildingPayload | ToolBeforePayload | ToolAfterPayload | ToolFailedPayload | TaskCompletedPayload


@dataclass(frozen=True)
class HookInput:
    event: HookEvent
    task_id: str
    run_id: str
    dispatch_id: str
    payload: EventPayload


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class HookResult:
    decision: Decision = Decision.ALLOW
    reason: str = ""
    data: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, value) -> HookResult:
        if isinstance(value, cls):
            value = {"decision": value.decision, "reason": value.reason, "data": value.data}
        if not isinstance(value, dict) or set(value) - {"decision", "reason", "data"}:
            raise ValueError("Hook output must contain only decision, reason and data")
        decision = Decision(value.get("decision"))  # an explicit decision is required
        reason, data = value.get("reason", ""), value.get("data", {})
        if not isinstance(reason, str) or len(reason) > 2000 or not isinstance(data, dict):
            raise ValueError("Invalid hook output")
        if decision == Decision.BLOCK and not reason.strip():
            raise ValueError("A block requires a reason")
        encoded = _json(data)
        if len(encoded.encode()) > 16384:
            raise ValueError("Hook output exceeds 16 KiB")
        return cls(decision, reason, json.loads(encoded))


@dataclass(frozen=True)
class HookReport:
    decision: Decision
    reasons: tuple[str, ...]
    dispatch_id: str
    executions: tuple[dict, ...]

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW


@dataclass(frozen=True)
class _Hook:
    id: str
    event: HookEvent
    priority: int
    timeout: float
    source: str
    callback: Callable[[HookInput], HookResult | dict] | None = None
    rule_json: str | None = None


# Regexes supplied by projects/plugins run in a killable interpreter. A Python
# worker thread cannot reliably time out a regex that holds the GIL.
_REGEX_PROGRAM = (
    "import json,re,sys; p,t=json.load(sys.stdin); "
    "print(json.dumps(bool(re.search(p,t))))"
)
_CALLBACK_SLOTS = threading.BoundedSemaphore(8)


def _match(rule: dict, payload: dict, timeout: float) -> bool:
    if rule.get("tool", "*") not in ("*", payload.get("tool")):
        return False
    # Preserve the historical JSON spacing used by legacy block regexes.
    text = json.dumps(payload.get("arguments", payload), sort_keys=True, allow_nan=False)
    if "contains" in rule and rule["contains"] not in text:
        return False
    if rule.get("pattern"):
        result = subprocess.run(
            [sys.executable, "-I", "-c", _REGEX_PROGRAM],
            input=json.dumps([rule["pattern"], text]), text=True,
            capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0 or result.stdout.strip() not in ("true", "false"):
            raise ValueError("Hook pattern evaluation failed")
        return result.stdout.strip() == "true"
    return True


def _host_call(callback, context, timeout):
    # This API is exclusively for trusted application code. Timed-out threads
    # cannot be killed; cap outstanding callbacks and never accept late output.
    if not _CALLBACK_SLOTS.acquire(blocking=False):
        raise RuntimeError("Hook callback capacity exhausted")
    done = threading.Event()
    answer = []

    def work():
        try:
            answer.append((True, callback(context)))
        except BaseException:
            answer.append((False, None))
        finally:
            _CALLBACK_SLOTS.release()
            done.set()

    try:
        threading.Thread(target=work, name="dikdik-host-hook", daemon=True).start()
    except BaseException:
        _CALLBACK_SLOTS.release()
        raise
    if not done.wait(timeout):
        raise TimeoutError("Hook timed out")
    if not answer[0][0]:
        raise RuntimeError("Hook callback failed")
    return answer[0][1]


class HookEngine:
    """A registry snapshot per run; ascending priority, then stable registration order.

    All failures close the gate. Post events cannot undo an executed action; their
    block stops further agent progress. Results are annotations, never patches to
    arguments, tools, permissions, context or verification evidence.
    """

    def __init__(self, trace: Callable[[str, dict], None] | None = None):
        self._hooks: list[_Hook] = []
        self._lock = threading.RLock()
        self._trace = trace
        self.run_id = uuid.uuid4().hex

    def fork(self, trace: Callable[[str, dict], None] | None = None) -> HookEngine:
        engine = HookEngine(trace)
        with self._lock:
            engine._hooks = list(self._hooks)
        return engine

    def _add(self, hook: _Hook) -> None:
        if not isinstance(hook.id, str) or not 1 <= len(hook.id) <= 200:
            raise ValueError("Hook id must contain 1–200 characters")
        if type(hook.priority) is not int or not -10000 <= hook.priority <= 10000:
            raise ValueError("Hook priority must be an integer between -10000 and 10000")
        if (type(hook.timeout) not in (int, float) or not math.isfinite(hook.timeout)
                or not 0 < hook.timeout <= 30):
            raise ValueError("Hook timeout must be greater than 0 and at most 30 seconds")
        with self._lock:
            if len(self._hooks) >= 256:
                raise ValueError("At most 256 hooks may be registered")
            if any(h.id == hook.id for h in self._hooks):
                raise ValueError("Duplicate hook id")
            self._hooks.append(hook)

    def register_host(self, hook_id: str, event: HookEvent | str, callback: Callable,
                      *, priority: int = 0, timeout: float = 5) -> None:
        if not callable(callback):
            raise ValueError("A host hook needs a callable")
        self._add(_Hook(hook_id, HookEvent(event), priority, timeout, "host", callback))

    def register_rule(self, source: str, rule: dict) -> None:
        """Register inert project/plugin data; no import, command or executable field."""
        rule = validate_rule(rule)
        if not isinstance(source, str) or not source or len(source) > 100:
            raise ValueError("Invalid hook source")
        self._add(_Hook(f"{source}:{rule['id']}", HookEvent(rule["event"]),
                        rule.get("priority", 0), rule.get("timeout_seconds", 5),
                        source, rule_json=_json(rule)))

    def describe(self) -> list[dict]:
        with self._lock:
            return [{"id": h.id, "event": h.event.value, "priority": h.priority,
                     "timeout_seconds": h.timeout, "source": h.source}
                    | ({"tool": json.loads(h.rule_json).get("match", {}).get("tool", "*"),
                        "pattern": json.loads(h.rule_json).get("match", {}).get("pattern", ""),
                        "message": json.loads(h.rule_json).get("reason", ""),
                        "decision": json.loads(h.rule_json)["decision"]} if h.rule_json else {})
                    for h in sorted(self._hooks, key=lambda h: h.priority)]

    def dispatch(self, event: HookEvent | str, task_id: str, payload: EventPayload) -> HookReport:
        event = HookEvent(event)
        if not isinstance(task_id, str) or not task_id or not isinstance(payload, dict):
            raise ValueError("Hooks require a task id and an object payload")
        canonical = _json(payload)
        if len(canonical.encode()) > 256 * 1024:
            raise ValueError("Hook input exceeds 256 KiB")
        if event in (HookEvent.TOOL_BEFORE, HookEvent.TOOL_AFTER, HookEvent.TOOL_FAILED):
            if not isinstance(payload.get("tool"), str) or not isinstance(payload.get("arguments"), dict):
                raise ValueError("Tool events require tool and arguments")
            if not isinstance(payload.get("call_id"), str):
                raise ValueError("Tool events require call_id")
            if event != HookEvent.TOOL_BEFORE and not isinstance(payload.get("result"), dict):
                raise ValueError("Tool outcome events require an object result")
        elif event in (HookEvent.TASK_STARTED, HookEvent.CONTEXT_BUILDING):
            if not isinstance(payload.get("objective"), str) or not isinstance(payload.get("mode"), str):
                raise ValueError("Task/context events require objective and mode")
        elif not isinstance(payload.get("state"), str) or not (payload.get("error") is None or isinstance(payload["error"], str)):
            raise ValueError("Completion events require state and an optional error string")
        dispatch_id = uuid.uuid4().hex
        with self._lock:
            hooks = sorted((h for h in self._hooks if h.event == event), key=lambda h: h.priority)
        executions, reasons = [], []
        common = {"event": event.value, "task_id": task_id, "run_id": self.run_id,
                  "dispatch_id": dispatch_id, "call_id": payload.get("call_id"), "tool": payload.get("tool"),
                  "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        for sequence, hook in enumerate(hooks):
            started = time.monotonic()
            status, matched = "completed", None
            try:
                if hook.callback:
                    matched = True
                    context = HookInput(event, task_id, self.run_id, dispatch_id, json.loads(canonical))
                    output = _host_call(hook.callback, context, hook.timeout)
                else:
                    rule = json.loads(hook.rule_json)
                    matched = _match(rule.get("match", {}), json.loads(canonical), hook.timeout)
                    output = {"decision": rule["decision"] if matched else "allow",
                              "reason": rule.get("reason", "") if matched else "",
                              "data": rule.get("data", {}) if matched else {}}
                # Copy/validate the result before accepting it, including host results.
                result = HookResult.parse(output)
            except (TimeoutError, subprocess.TimeoutExpired):
                status = "timed_out"
                result = HookResult(Decision.BLOCK, f"Hook {hook.id} timed out")
            except Exception:
                status = "error"
                result = HookResult(Decision.BLOCK, f"Hook {hook.id} failed or returned invalid output")
            elapsed = round((time.monotonic() - started) * 1000, 3)
            if result.decision == Decision.BLOCK:
                reasons.append(result.reason)
            record = {**common, "hook_id": hook.id, "source": hook.source, "sequence": sequence,
                      "priority": hook.priority, "timeout_seconds": hook.timeout,
                      "timestamp": time.time(), "duration_ms": elapsed, "status": status,
                      "matched": matched, "decision": result.decision.value,
                      "reason": result.reason, "data": result.data}
            executions.append(record)
            self._record("hook_execution", record)
        decision = Decision.BLOCK if reasons else Decision.ALLOW
        self._record("hook_event", {**common, "decision": decision.value,
                                    "reasons": reasons, "hook_count": len(executions)})
        return HookReport(decision, tuple(reasons), dispatch_id, tuple(executions))

    def _record(self, kind: str, data: dict) -> None:
        if self._trace:
            # Persistence is part of enforcement: no silent unaudited execution.
            try:
                self._trace(kind, json.loads(_json(data)))
            except Exception:
                raise HookAuditError("Hook audit persistence failed") from None


def validate_rule(rule: dict) -> dict:
    if not isinstance(rule, dict) or set(rule) - {
        "id", "event", "priority", "timeout_seconds", "match", "decision", "reason", "data"
    }:
        raise ValueError("Unsupported hook fields; plugin hooks must be declarative")
    rule = json.loads(_json(rule))
    if not isinstance(rule.get("id"), str) or not 1 <= len(rule["id"]) <= 80:
        raise ValueError("Hook rule needs an id of 1–80 characters")
    HookEvent(rule.get("event"))
    HookResult.parse({k: rule[k] for k in ("decision", "reason", "data") if k in rule})
    match = rule.get("match", {})
    if not isinstance(match, dict) or set(match) - {"tool", "contains", "pattern"}:
        raise ValueError("Hook match accepts only tool, contains and pattern")
    if any(not isinstance(value, str) or len(value) > 2048 for value in match.values()):
        raise ValueError("Hook match values must be strings of at most 2048 characters")
    # Validate priority/timeouts using the same path as host registrations.
    probe = HookEngine()
    probe._add(_Hook(rule["id"], HookEvent(rule["event"]), rule.get("priority", 0),
                     rule.get("timeout_seconds", 5), "validation"))
    return rule


def document_rules(document: dict) -> list[dict]:
    """Convert legacy block rules without silently dropping invalid policies."""
    if not isinstance(document, dict) or set(document) - {"block", "hooks"}:
        raise ValueError("Hooks document accepts only block and hooks arrays")
    legacy, modern = document.get("block", []), document.get("hooks", [])
    if not isinstance(legacy, list) or not isinstance(modern, list) or len(legacy) + len(modern) > 100:
        raise ValueError("Hooks document must contain at most 100 rules")
    rules = []
    for index, rule in enumerate(legacy):
        if not isinstance(rule, dict) or set(rule) - {"tool", "pattern", "message"}:
            raise ValueError("Invalid legacy hook rule")
        rules.append(validate_rule({"id": f"legacy-{index}", "event": "tool.before", "decision": "block",
                                    "reason": rule.get("message") or "Blocked by a hook",
                                    "match": {"tool": rule.get("tool", "*"), "pattern": rule.get("pattern") or ""}}))
    return rules + [validate_rule(rule) for rule in modern]


def load_hook_engine(base: HookEngine, root: Path, extra=(), trace=None) -> HookEngine:
    engine = base.fork(trace)
    documents = []
    path = root / ".dikdik" / "hooks.json"
    try:
        if path.exists():
            if path.stat().st_size > 65536:
                raise ValueError("Hooks file exceeds 64 KiB")
            documents.append(("project", json.loads(path.read_text(encoding="utf-8-sig"))))
        documents.extend((f"plugin {name}", doc) for name, doc in extra)
        for source, document in documents:
            for rule in document_rules(document):
                engine.register_rule(source, rule)
    except (OSError, ValueError, TypeError):
        # Apply the configuration failure to every gate, not just tool.before.
        engine = HookEngine(trace)
        for event in HookEvent:
            engine.register_host(f"invalid-config:{event.value}", event,
                                 lambda _: HookResult(Decision.BLOCK, "Hooks configuration is invalid; fix it before continuing"),
                                 priority=-10000)
    return engine
