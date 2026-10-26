"""Single-worker lifecycle; source workspace is never modified."""

import difflib
import hashlib
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .providers import propose
from .agent import AgentBudget, run_agent
from .agent_profiles import LIMITS as PROFILE_LIMITS, AgentProfiles
from .agent_provider import CompatibleAgentModel
from .automation import AutomationScheduler
from .locking import DataLock
from .mcp import McpSession, McpStore
from .model_config import ModelResolver
from .repository import approved_root, apply_edits, can_create_file, inventory, metadata, syntax_check
from .runners import detect_runners, run_project_tests, run_shell
from .sandbox import cleanup_container
from .secrets_store import SecretsStore
from .skills import SkillIndex
from .store import Store


TERMINAL = {"completed", "failed", "blocked", "cancelled", "interrupted"}


def run_tests(snapshot, cancel, name=None, files=None):
    """Every detected suite for a candidate snapshot (one seam that tests replace)."""
    return run_project_tests(snapshot, files if files is not None else inventory(snapshot), cancel, name)


class ApplyConflict(ValueError):
    """Workspace files changed since the candidate was made; nothing was written."""

    def __init__(self, paths):
        super().__init__("These files changed in your workspace since Dikdik read them: " + ", ".join(paths))
        self.paths = paths


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def base_hashes(original, paths):
    """What each changed path looked like when the candidate was made (None: did not exist)."""
    return {path: _sha(original[path]) if path in original else None for path in paths}


def runner_label(files):
    labels = [runner["label"] for runner in detect_runners(files)]
    return " + ".join(labels) or "no test suite found"
HISTORY_LIMIT = 24 * 1024  # bytes of earlier conversation text replayed to the model


def _git_path(prefix: str, path: str) -> str:
    raw = (prefix + path).encode("utf-8")
    if all(33 <= byte < 127 and byte not in {34, 92} for byte in raw):
        return raw.decode("ascii")
    return '"' + "".join(chr(byte) if 32 <= byte < 127 and byte not in {34, 92} else f"\\{byte:03o}" for byte in raw) + '"'


def _diff_lines(text: str) -> list[str]:
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _changed_paths(original, changed):
    return sorted(path for path in set(original) | set(changed) if path not in original or path not in changed or original[path] != changed[path])


def candidate_diff(original, changed):
    chunks = []
    for path in _changed_paths(original, changed):
        before, after = _git_path("a/", path), _git_path("b/", path)
        chunks.append(f"diff --git {before} {after}\n")
        if path not in original:
            chunks.append("new file mode 100644\n")
        elif path not in changed:
            chunks.append("deleted file mode 100644\n")
        for line in difflib.unified_diff(_diff_lines(original.get(path, "")), _diff_lines(changed.get(path, "")), fromfile=before if path in original else "/dev/null", tofile=after if path in changed else "/dev/null"):
            chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(chunks)


def change_summary(original, changed, diff):
    paths = _changed_paths(original, changed)
    lines = diff.split("\n")
    starts = [index for index, line in enumerate(lines) if line.startswith("diff --git ")]
    if len(starts) != len(paths) or (paths and not diff.endswith("\n")):
        raise ValueError("Diff metadata does not match candidate files")
    if any(lines[starts[index]] != f"diff --git {_git_path('a/', path)} {_git_path('b/', path)}" for index, path in enumerate(paths)):
        raise ValueError("Diff headers do not match candidate paths")
    return [{"path": path, "status": "added" if path not in original else "deleted" if path not in changed else "modified", "start_line": starts[index], "end_line": starts[index + 1] if index + 1 < len(starts) else len(lines) - 1} for index, path in enumerate(paths)]


class Engine:
    def __init__(self, root: Path, data: Path):
        root = approved_root(str(root))
        data = data.resolve()
        if data == root or root in data.parents or data in root.parents or "," in str(data):
            raise ValueError("Choose a separate private data directory (no commas), not a workspace ancestor")
        self.root, self.data = root, data
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_lock = DataLock(data / "service.lock")
        try:
            self.store = Store(data / "state.sqlite3")
            self.store.bind_workspace(root)
            for task in self.store.stale_execution_ids():
                warning = cleanup_container("dikdik-" + task)
                self.store.event(task, "recovery_cleanup", {"warning": warning, "container": "dikdik-" + task})
            self.store.recover()
        except Exception:
            self.data_lock.close()
            raise
        # Same files as the /api/secrets routes; the default profile decides the model.
        self.models = ModelResolver(self.store, SecretsStore(data / "secrets.bin", data / "secrets.key"))
        self.skills = SkillIndex(self.store, data / "skills", root / ".dikdik" / "skills")
        self.mcp = McpStore(self.store)
        self.agents = AgentProfiles(self.store)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dikdik")
        self.lock = threading.Lock()
        self.active: dict[str, threading.Event] = {}
        self.pending: dict[str, list[dict]] = {}  # follow-ups sent while their task was running
        self.live: dict[str, str] = {}  # a reply as it streams in; never persisted
        self.automation = AutomationScheduler(self)  # M4: scheduler owned by Engine lifecycle
        self.automation.start()

    def submit(self, request):
        clean = self._validate(request)
        with self.lock:
            if self.active:
                raise ValueError("One task at a time; wait or cancel the active task")
            task = self.store.create(clean)
            self._start(task, clean, follow_up=False)
        return task

    def send(self, task, request):
        """A follow-up message: runs on the same task, continuing from its last candidate."""
        clean = self._validate(request)
        with self.lock:
            state = self.store.get(task)["state"]
            message = {"text": clean["objective"], "mode": clean["mode"], "verification": clean["verification"]}
            if task in self.active:
                self.pending.setdefault(task, []).append(clean)
                self.store.event(task, "user_message", {**message, "queued": True})
                return True
            if self.active:
                raise ValueError("Another task is running; wait for it or stop it first")
            if state not in TERMINAL:
                raise ValueError("This task is still starting; try again in a moment")
            self.store.event(task, "user_message", message)
            self._start(task, clean, follow_up=True)
        return False

    def delete(self, task):
        with self.lock:
            if task in self.active:
                raise ValueError("Stop the task before deleting it")
            self.store.delete(task)
        artifact = (self.data / "artifacts" / task).resolve()
        if artifact.parent == (self.data / "artifacts").resolve() and artifact.is_dir():
            shutil.rmtree(artifact, ignore_errors=True)

    def apply(self, task, paths=None):
        """Write the task's candidate files into the workspace, only if nobody changed them meanwhile."""
        with self.lock:
            if task in self.active:
                raise ValueError("Wait for the task to finish before applying its changes")
            result = self.store.get(task)["result"] or {}
            changed = result.get("changed_files") or []
            if not changed:
                raise ValueError("This task has no changes to apply")
            chosen = changed if paths is None else paths
            if not isinstance(chosen, list) or not chosen or not all(isinstance(p, str) and p in changed for p in chosen):
                raise ValueError("Choose files from this task's changes")
            base = result.get("base")
            if not isinstance(base, dict) or any(p not in base for p in chosen):
                raise ValueError("This task was made before safe apply existed; send a follow-up to refresh it")
            artifact, root = self.data / "artifacts" / task, self.root.resolve()
            writes, conflicts, skipped = {}, [], []
            for path in chosen:
                target, source = (root / path).resolve(), artifact / path
                if root not in target.parents or artifact.resolve() not in source.resolve().parents or not source.is_file():
                    raise ValueError(f"Cannot apply {path}")
                content = source.read_bytes()
                try:
                    current = _sha(target.read_bytes().decode("utf-8")) if target.exists() else None
                except (UnicodeDecodeError, OSError):
                    current = "unreadable"
                if current == _sha(content.decode("utf-8")):
                    skipped.append(path)  # already identical, e.g. applied before
                elif current != base[path]:
                    conflicts.append(path)
                else:
                    writes[path] = (target, content)
            if conflicts:
                raise ApplyConflict(conflicts)
            for path, (target, content) in writes.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.dikdik-apply")
                temporary.write_bytes(content)
                if target.exists():
                    shutil.copymode(target, temporary)
                os.replace(temporary, target)
            self.store.event(task, "applied", {"files": sorted(writes), "unchanged": sorted(skipped)})
        return {"applied": sorted(writes), "unchanged": sorted(skipped)}

    def file_text(self, path, task=None):
        """A workspace file, or the task's candidate version of it."""
        if not isinstance(path, str) or not path:
            raise ValueError("Choose a file")
        if task:
            result = self.store.get(task)["result"] or {}
            if path in (result.get("changed_files") or []):
                source = self.data / "artifacts" / task / path
                if (self.data / "artifacts" / task).resolve() in source.resolve().parents and source.is_file():
                    return {"path": path, "version": "candidate", "text": source.read_bytes().decode("utf-8")}
        files = inventory(self.root)
        if path not in files:
            raise KeyError(path)
        return {"path": path, "version": "workspace", "text": files[path]}

    def _live(self, task, text):
        if text:
            self.live[task] = text[-16384:]
        else:
            self.live.pop(task, None)

    def busy(self, task):
        with self.lock:
            return task in self.active or bool(self.pending.get(task))

    def _skills(self, task):
        """Enabled skills for the model, recorded so the conversation shows which were used."""
        try:
            guidance, names = self.skills.context()
        except OSError:
            return ""
        if names:
            self.store.event(task, "skills", {"names": names})
        return guidance

    def _start(self, task, clean, follow_up):
        # Caller holds self.lock.
        cancel = threading.Event()
        self.active[task] = cancel
        self.pool.submit(self._run, task, clean, cancel, follow_up)

    def _validate(self, request):
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON object")
        objective = request.get("objective")
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 4000:
            raise ValueError("Objective must contain 1–4000 characters")
        if request.get("mode") not in {"offline", "model", "agent"}:
            raise ValueError("Choose offline, model or agent mode")
        if request.get("verification") not in {"syntax", "docker"}:
            raise ValueError("Choose syntax or docker verification")
        if request["verification"] == "docker" and request.get("approve_execution") is not True:
            raise ValueError("Explicit Docker execution approval required")
        if request["mode"] in {"model", "agent"} and request.get("approve_external") is not True:
            raise ValueError("Explicit external-code disclosure approval required")
        if request["mode"] == "offline":
            edits = request.get("edits")
            if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
                raise ValueError("Offline mode requires 1–20 explicit edits")
        profile = request.get("agent_profile")
        if profile not in (None, ""):
            if not isinstance(profile, str):
                raise ValueError("Agent profile must be an id")
            try:
                self.agents.get(profile)
            except KeyError:
                raise ValueError("That agent profile no longer exists; pick another") from None
        # Persist only the public contract, never arbitrary caller fields.
        clean = {k: request[k] for k in ("objective", "mode", "verification", "edits", "approve_execution", "approve_external") if k in request}
        if profile not in (None, ""):
            clean["agent_profile"] = profile
        return clean

    def _profile(self, request):
        """The task's agent profile, or None for the built-in default (missing ones fall back too)."""
        try:
            return self.agents.get(request["agent_profile"]) if request.get("agent_profile") else None
        except KeyError:
            return None

    def _starting_files(self, task, original):
        """Fresh source plus the files the last turn changed, so follow-ups build on them."""
        # The task row's result is cleared while running; each turn's outcome lives in its terminal event.
        ends = [e["data"] for e in self.store.get(task)["events"] if e["kind"] in TERMINAL and isinstance(e["data"], dict)]
        changed = next((d["changed_files"] for d in reversed(ends) if "changed_files" in d), None)
        artifact = self.data / "artifacts" / task
        files = dict(original)
        for path in changed if isinstance(changed, list) else []:
            source = artifact / path
            if isinstance(path, str) and source.is_file() and artifact.resolve() in source.resolve().parents:
                files[path] = source.read_bytes().decode("utf-8")
        return files

    def _history(self, task):
        """Earlier user messages, agent replies and turn outcomes, newest kept within budget."""
        turns = []
        events = self.store.get(task)["events"]
        for event in events[:-1]:  # the newest user_message is this turn's objective
            kind, data = event["kind"], event["data"]
            if kind == "queued":
                turns.append({"role": "user", "content": data.get("objective", "")})
            elif kind == "user_message":
                turns.append({"role": "user", "content": data.get("text", "")})
            elif kind == "agent_message" and isinstance(data.get("content"), str):
                turns.append({"role": "assistant", "content": data["content"]})
            elif kind in TERMINAL:
                changed = ", ".join(data.get("changed_files") or []) or "none"
                note = f"[Turn ended: {kind}. Changed files: {changed}." + (f" Error: {data['error']}" if data.get("error") else "") + "]"
                turns.append({"role": "assistant", "content": note})
        kept, size = [], 0
        for turn in reversed(turns):
            size += len(turn["content"].encode())
            if size > HISTORY_LIMIT:
                break
            kept.append(turn)
        return list(reversed(kept))

    def cancel(self, task):
        with self.lock:
            if self.store.get(task)["state"] in TERMINAL:
                return False
            flag = self.active.get(task)
            if flag:
                flag.set()
                # Stop means stop: queued follow-ups are dropped too.
                dropped = len(self.pending.pop(task, []))
                self.store.event(task, "cancellation_requested", {"dropped_messages": dropped} if dropped else {})
                return True
        return False

    def _finish(self, task, state, result, cancel):
        # Cancellation acceptance and terminal commit have one serialization point.
        with self.lock:
            if cancel.is_set():
                state = "cancelled"
                if result is not None:
                    result = {**result, "tests_verified": False, "error": "Cancelled"}
            self.store.state(task, state, result)

    def _run(self, task, request, cancel, follow_up=False):
        started = time.monotonic()
        try:
            self.store.state(task, "running")
            if not follow_up:  # the pipeline is the same every turn; describe it once
                self.store.event(task, "plan", {"steps": ["Inspect bounded repository snapshot", "Validate minimal edit proposal", "Produce isolated candidate and diff", "Run selected checks", "Report evidence without changing source"]})
            original = inventory(self.root)
            self.store.event(task, "inspected", {"file_count": len(original), "manifests": metadata(original)["manifests"]})
            files = self._starting_files(task, original) if follow_up else original
            if cancel.is_set():
                self._finish(task, "cancelled", None, cancel)
                return
            if request["mode"] == "agent":
                self._agent_workflow(task, request, original, cancel, started, files, self._history(task) if follow_up else [])
                return
            profile = self._profile(request)
            guidance = self._skills(task) if profile is None or profile["use_skills"] else ""
            model_id = profile["model_profile_id"] if profile else None
            edits, provider = propose(request, files, lambda: self.models.resolve(model_id or None), guidance)
            self.store.event(task, "proposal", provider)
            changed = apply_edits(files, edits, protected_paths=set(original))
            if changed == files:
                raise ValueError("Proposal has no net changes")
            diff = candidate_diff(original, changed)
            if len(diff.encode()) > 1_000_000:
                raise ValueError("Diff exceeds 1MB")
            candidate = self.data / "artifacts" / task
            candidate.mkdir(parents=True, exist_ok=True)
            for path, text in changed.items():
                dest = candidate / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(text.encode())
            checks = [syntax_check(changed)]
            self.store.event(task, "syntax", checks[0])
            if cancel.is_set():
                self._finish(task, "cancelled", {"diff": diff, "checks": checks}, cancel)
                return
            if request["verification"] == "docker" and checks[0]["status"] != "failed":
                self.store.event(task, "execution", {"runner": "Docker", "command": runner_label(changed), "timeout_seconds": 60, "container": "dikdik-" + task})
                checks.append(run_tests(candidate, cancel, name="dikdik-" + task, files=changed))
            tested = any(c["kind"] != "python_syntax" and c["status"] == "passed" for c in checks)
            state = "cancelled" if cancel.is_set() else "blocked" if any(c["status"] in {"blocked", "inconclusive"} for c in checks) else "failed" if any(c["status"] in {"failed", "timed_out", "output_limit"} for c in checks) else "completed"
            self._finish(task, state, {"turn_changed": True, "base": base_hashes(original, _changed_paths(original, changed)), "diff": diff, "changed_files": _changed_paths(original, changed), "file_changes": change_summary(original, changed, diff), "checks": checks, "tests_verified": tested and state == "completed", "source_modified": False, "artifact": task, "seconds": round(time.monotonic() - started, 3), "limitations": "Candidate only. Syntax success does not establish objective correctness. No automatic apply or test-and-repair loop."}, cancel)
        except Exception as exc:
            # Known validation errors are safe; unexpected exceptions may embed secrets.
            error = str(exc) if isinstance(exc, ValueError) else "Internal failure; inspect local service diagnostics"
            self._finish(task, "cancelled" if cancel.is_set() else "failed", {"error": error}, cancel)
        finally:
            with self.lock:
                self.active.pop(task, None)
                queue = self.pending.get(task)
                if queue:
                    following = queue.pop(0)
                    if not queue:
                        self.pending.pop(task, None)
                    self._start(task, following, follow_up=True)

    def _agent_workflow(self, task, request, original, cancel, started, files=None, history=None):
        candidate = self.data / "artifacts" / task

        def snapshot(files):
            candidate.mkdir(parents=True, exist_ok=True)
            for path, text in files.items():
                dest = candidate / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(text.encode())

        def verify(files):
            snapshot(files)
            self.store.event(task, "execution", {"runner": "Docker", "command": runner_label(files), "timeout_seconds": 60, "container": "dikdik-" + task})
            return run_tests(candidate, cancel, name="dikdik-" + task, files=files)

        def shell(files, command, timeout, image):
            return run_shell(self.data / "work" / task, files, command, cancel, image=image, timeout=timeout, name="dikdik-" + task)

        docker = request["verification"] == "docker"

        profile = self._profile(request)
        model_id = (profile or {}).get("model_profile_id") or None
        guidance = self._skills(task) if profile is None or profile["use_skills"] else ""
        notes = [{"role": "user", "content": f"Instructions from the user's agent profile “{profile['name']}”:\n{profile['instructions']}"}] if profile and profile["instructions"] else []
        history = notes + ([{"role": "user", "content": guidance}] if guidance else []) + list(history or [])
        servers = [server for server in self.mcp.list() if server["enabled"]] if profile is None or profile["use_mcp"] else []
        session = McpSession(servers, self.models.secrets.reveal) if servers else None
        if session:
            self.store.event(task, "mcp", {"servers": [s["name"] for s in servers], "tools": len(session.tools), "errors": session.errors})
        budget = AgentBudget(**{key: profile[key] for key in PROFILE_LIMITS if profile and profile.get(key)})
        model = self.models.describe(model_id)
        self.store.event(task, "agent_started", {"implementation": "original_dikdik", "profile": profile["name"] if profile else "Default", "max_model_calls": budget.max_model_calls, "max_tool_calls": budget.max_tool_calls, "max_repair_attempts": budget.max_repair_attempts, "deadline_seconds": budget.deadline_seconds, "model": {k: v for k, v in model.items() if k != "problem"}})
        try:
            outcome = run_agent(
                original, request["objective"], CompatibleAgentModel(request["approve_external"], lambda: self.models.resolve(model_id), lambda text: self._live(task, text)),
                verify if docker else None, lambda kind, data: self.store.event(task, "agent_" + kind, data), cancel, budget,
                creation_policy=lambda path: can_create_file(self.root, path),
                start=files, history=history, shell=shell if docker and (profile is None or profile["allow_shell"]) else None,
                extra_tools=session.tools if session else None, extra_call=session.call if session else None,
            )
        finally:
            if session:
                session.close()  # stdio servers stop with the task
        snapshot(outcome.files)
        diff = candidate_diff(original, outcome.files)
        if len(diff.encode()) > 1_000_000:
            raise ValueError("Diff exceeds 1MB")
        result = {
            "turn_changed": outcome.files != (files if files is not None else original),
            "base": base_hashes(original, _changed_paths(original, outcome.files)),
            "diff": diff, "changed_files": _changed_paths(original, outcome.files),
            "file_changes": change_summary(original, outcome.files, diff),
            "checks": outcome.checks, "tests_verified": outcome.tests_verified,
            "source_modified": False, "artifact": task,
            "seconds": round(time.monotonic() - started, 3),
            "agent": {"model_calls": outcome.model_calls, "tool_calls": outcome.tool_calls,
                      "repair_attempts": outcome.repair_attempts, "reported_usage_tokens": outcome.usage_tokens},
            "limitations": ("Candidate only. Tests ran in a network-less Docker sandbox; a pass is not proof of every objective." if docker
                            else "Candidate only. Syntax checked only; no tests were run.") + " Provider usage may be unavailable; no cost claim.",
        }
        if outcome.error:
            result["error"] = outcome.error
        self._finish(task, outcome.state, result, cancel)

    def close(self):
        self.automation.close()
        with self.lock:
            for flag in self.active.values():
                flag.set()
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.data_lock.close()
