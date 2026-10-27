"""Python client and command line for a running Dikdik server.

    from dikdik.client import DikdikClient
    dikdik = DikdikClient(data="~/.dikdik")          # or token=..., or DIKDIK_TOKEN
    task = dikdik.submit("Fix the failing test", share_code=True)
    for kind, event in dikdik.events(task):
        ...
    dikdik.apply(task)

    python -m dikdik.client ask "Fix the failing test" --share-code
    python -m dikdik.client tasks | show <id> | apply <id> --yes

Sharing code with the model provider and running tests in Docker are explicit
arguments, exactly like the approvals in the app. Standard library only.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

DEFAULT_URL = "http://127.0.0.1:8787"
TERMINAL = {"completed", "failed", "blocked", "cancelled", "interrupted"}


class DikdikError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class DikdikClient:
    def __init__(self, url: str = DEFAULT_URL, token: str | None = None, data: str | os.PathLike | None = None):
        self.url = url.rstrip("/")
        if token is None and data is not None:
            token = (Path(data).expanduser() / "web.token").read_text(encoding="utf-8").strip()
        self.token = token or os.environ.get("DIKDIK_TOKEN", "")
        if not self.token:
            raise DikdikError(401, "No token: pass token=, data= (the server's --data folder), or set DIKDIK_TOKEN")

    # --- transport -----------------------------------------------------------
    def _open(self, method: str, path: str, body=None, timeout: float = 30):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("error", exc.reason)
            except ValueError:
                detail = exc.reason
            raise DikdikError(exc.code, str(detail)) from None
        except urllib.error.URLError:
            raise DikdikError(0, f"Dikdik is not running at {self.url}") from None

    def _call(self, method: str, path: str, body=None):
        with self._open(method, path, body) as response:
            return json.loads(response.read() or b"null")

    # --- tasks ----------------------------------------------------------------
    @staticmethod
    def _request(objective, mode, verification, share_code, run_tests, edits, agent_profile):
        request = {"objective": objective, "mode": mode, "verification": "docker" if run_tests else verification,
                   "approve_external": bool(share_code), "approve_execution": bool(run_tests)}
        if edits is not None:
            request["edits"] = edits
        if agent_profile:
            request["agent_profile"] = agent_profile
        return request

    def submit(self, objective: str, *, mode: str = "agent", verification: str = "syntax", share_code: bool = False,
               run_tests: bool = False, edits: list | None = None, agent_profile: str | None = None) -> str:
        return self._call("POST", "/api/tasks", self._request(objective, mode, verification, share_code, run_tests, edits, agent_profile))["id"]

    def send(self, task: str, text: str, *, mode: str = "agent", verification: str = "syntax", share_code: bool = False,
             run_tests: bool = False, edits: list | None = None, agent_profile: str | None = None) -> bool:
        """A follow-up in the same conversation. True when it was queued behind a running turn."""
        body = self._request(text, mode, verification, share_code, run_tests, edits, agent_profile)
        return self._call("POST", f"/api/tasks/{quote(task)}/messages", body)["queued"]

    def task(self, task: str) -> dict:
        return self._call("GET", f"/api/tasks/{quote(task)}")

    def tasks(self) -> list[dict]:
        return self._call("GET", "/api/tasks")

    def cancel(self, task: str) -> bool:
        return self._call("POST", f"/api/tasks/{quote(task)}/cancel", {})["requested"]

    def pause(self, task: str) -> bool:
        return self._call("POST", f"/api/tasks/{quote(task)}/pause", {})["requested"]

    def resume(self, task: str, body: dict | None = None) -> dict:
        return self._call("POST", f"/api/tasks/{quote(task)}/resume", body or {})

    def ask(self, task: str, question: str) -> dict:
        """A side question: the agent answers without changing files."""
        return self._call("POST", f"/api/tasks/{quote(task)}/ask", {"question": question})

    def fork(self, task: str, *, title: str | None = None, tags: list[str] | None = None) -> str:
        body = {k: v for k, v in {"title": title, "tags": tags}.items() if v is not None}
        return self._call("POST", f"/api/tasks/{quote(task)}/fork", body)["id"]

    def condense(self, task: str, share_code: bool = False) -> dict:
        return self._call("POST", f"/api/tasks/{quote(task)}/condense", {"approve_external": bool(share_code)})

    def settings(self) -> dict:
        return self._call("GET", "/api/settings/app")

    def update_settings(self, body: dict) -> dict:
        return self._call("PATCH", "/api/settings/app", body)

    def agent_info(self) -> dict:
        return self._call("GET", "/api/agent-info")

    def rename(self, task: str, title: str) -> None:
        self._call("PATCH", f"/api/tasks/{quote(task)}", {"title": title})

    def delete(self, task: str) -> None:
        self._call("DELETE", f"/api/tasks/{quote(task)}")

    def apply(self, task: str, files: list[str] | None = None) -> dict:
        return self._call("POST", f"/api/tasks/{quote(task)}/apply", {"confirm": True, **({"files": files} if files else {})})

    def events(self, task: str, after: int = 0):
        """Yield ("task", event), ("delta", {"text"}) and finally ("end", {"state"}) as they happen."""
        with self._open("GET", f"/api/tasks/{quote(task)}/stream?after={after}", timeout=60) as response:
            kind, data = None, []
            for raw in response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: "):
                    data.append(line[6:])
                elif not line and kind:
                    payload = json.loads("\n".join(data))
                    yield kind, payload
                    if kind == "end":
                        return
                    kind, data = None, []

    def wait(self, task: str, timeout: float = 900) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.task(task)
            if data["state"] in TERMINAL:
                return data
            time.sleep(0.5)
        raise TimeoutError(f"Task {task} did not finish in {timeout} seconds")

    # --- workspace --------------------------------------------------------------
    def project(self) -> dict:
        return self._call("GET", "/api/project")

    def model(self) -> dict:
        return self._call("GET", "/api/model")

    def git(self) -> dict:
        return self._call("GET", "/api/git")

    def commit(self, message: str, files: list[str]) -> dict:
        return self._call("POST", "/api/git/commit", {"message": message, "files": files})


# --- command line --------------------------------------------------------------
def _print_event(kind: str, data: dict, out) -> None:
    if kind == "delta":
        return  # the saved message follows; keep terminal output clean
    if kind == "end":
        print(f"\n[{data['state']}]", file=out)
        return
    event, payload = data["kind"], data["data"]
    if event == "agent_message":
        print(f"\nDikdik: {payload.get('content', '')}", file=out)
    elif event == "agent_tool" and payload.get("phase") == "start":
        args = payload.get("arguments") or {}
        detail = args.get("path") or args.get("command") or args.get("query") or ""
        print(f"  · {payload.get('name')} {detail}".rstrip(), file=out)
    elif event == "agent_tool" and payload.get("phase") == "end" and payload.get("failed"):
        print(f"    ✗ {json.dumps(payload.get('observation'))[:200]}", file=out)
    elif event in {"syntax", "agent_check"}:
        print(f"  · check {payload.get('kind')}: {payload.get('status')}", file=out)
    elif event == "applied":
        print(f"  · applied {', '.join(payload.get('files', []))}", file=out)
    elif event in TERMINAL:
        if payload.get("error"):
            print(f"  error: {payload['error']}", file=out)
        if payload.get("changed_files"):
            print(f"  changed: {', '.join(payload['changed_files'])}", file=out)


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="python -m dikdik.client", description="Talk to a running Dikdik server.")
    parser.add_argument("--url", default=os.environ.get("DIKDIK_URL", DEFAULT_URL))
    parser.add_argument("--data", default=os.environ.get("DIKDIK_DATA"), help="The server's --data folder (reads its sign-in token)")
    parser.add_argument("--token", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Start a task (or reply to one with --task) and follow it")
    ask.add_argument("objective")
    ask.add_argument("--task", help="Reply in this existing task instead of starting a new one")
    ask.add_argument("--mode", choices=["agent", "model"], default="agent")
    ask.add_argument("--agent-profile")
    ask.add_argument("--share-code", action="store_true", help="Approve sending code and the objective to your model provider")
    ask.add_argument("--run-tests", action="store_true", help="Approve running the tests in the Docker sandbox")
    ask.add_argument("--no-follow", action="store_true")
    commands.add_parser("tasks", help="List recent tasks")
    show = commands.add_parser("show", help="Print a task's conversation and diff")
    show.add_argument("task")
    apply = commands.add_parser("apply", help="Write a task's changes into the workspace")
    apply.add_argument("task")
    apply.add_argument("--yes", action="store_true", help="Confirm writing the files")
    args = parser.parse_args(argv)
    try:
        client = DikdikClient(args.url, args.token, args.data)
        if args.command == "ask":
            options = {"mode": args.mode, "share_code": args.share_code, "run_tests": args.run_tests, "agent_profile": args.agent_profile}
            if args.task:
                client.send(args.task, args.objective, **options)
                task = args.task
            else:
                task = client.submit(args.objective, **options)
            print(f"task {task}", file=out)
            if not args.no_follow:
                last = max((e["seq"] for e in client.task(task)["events"]), default=0) if args.task else 0
                for kind, data in client.events(task, after=last):
                    _print_event(kind, data, out)
        elif args.command == "tasks":
            for task in client.tasks():
                print(f"{task['id'][:8]}  {task['state']:<11} {task.get('title') or task.get('objective', '')}", file=out)
        elif args.command == "show":
            task = client.task(args.task)
            for event in task["events"]:
                _print_event("task", event, out)
            if task.get("result") and task["result"].get("diff"):
                print("\n" + task["result"]["diff"], file=out)
        elif args.command == "apply":
            if not args.yes:
                print("Add --yes to write this task's changes into your workspace.", file=out)
                return 2
            done = client.apply(args.task)
            print(f"applied: {', '.join(done['applied']) or 'nothing (already applied)'}", file=out)
    except DikdikError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
