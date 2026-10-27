"""Deterministic, secret-free Git synchronization for automation definitions."""

import json
import os
import re
import subprocess
from pathlib import Path


NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class AutomationGitSync:
    def __init__(self, engine, automations):
        self.engine, self.store, self.automations = engine, engine.store, automations
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS automation_git_sync (
                    id INTEGER PRIMARY KEY CHECK(id=1), repository TEXT NOT NULL,
                    directory TEXT NOT NULL, branch TEXT NOT NULL, remote TEXT NOT NULL,
                    last_manifest TEXT, last_revision TEXT, updated REAL NOT NULL
                );
            """)

    def _git(self, repository: Path, *args: str, check=True):
        try:
            result = subprocess.run(["git", "-c", "core.hooksPath=NUL", *args], cwd=repository,
                                    capture_output=True, text=True, timeout=30, check=False,
                                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not run Git for automation sync") from exc
        if check and result.returncode:
            raise ValueError((result.stderr or result.stdout or "Git sync failed").strip()[:500])
        return result

    def _repository(self, value: str) -> Path:
        if not isinstance(value, str) or not value.strip() or len(value) > 260 or any(ord(c) < 32 for c in value):
            raise ValueError("Repository must be a workspace path")
        root = Path(self.engine.root).resolve()
        repository = (root / value.strip()).resolve()
        try:
            repository.relative_to(root)
        except ValueError:
            raise ValueError("Sync repository must stay inside the workspace") from None
        if not repository.is_dir():
            raise ValueError("Sync repository does not exist")
        top = self._git(repository, "rev-parse", "--show-toplevel").stdout.strip()
        if Path(top).resolve() != repository:
            raise ValueError("Choose the root of a Git repository")
        return repository

    @staticmethod
    def _directory(value) -> str:
        if not isinstance(value, str):
            raise ValueError("Directory must be a relative path")
        parts = [part for part in value.replace("\\", "/").strip("/").split("/") if part]
        if not parts or len(parts) > 5 or any(part in {".", ".."} or not SEGMENT.fullmatch(part) for part in parts):
            raise ValueError("Directory must be a safe relative path")
        return "/".join(parts)

    def get(self):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM automation_git_sync WHERE id=1").fetchone()
        if row is None:
            return 200, {"configured": False, "repository": ".", "directory": ".dikdik/automations",
                         "branch": "main", "remote": "origin", "last_revision": None}
        return 200, {"configured": True, "repository": row["repository"], "directory": row["directory"],
                     "branch": row["branch"], "remote": row["remote"], "last_revision": row["last_revision"]}

    def save(self, body, now):
        if not isinstance(body, dict) or set(body) - {"repository", "directory", "branch", "remote"}:
            raise ValueError("Git sync accepts repository, directory, branch and remote")
        repository = self._repository(body.get("repository", "."))
        root = Path(self.engine.root).resolve()
        saved_repository = "." if repository == root else repository.relative_to(root).as_posix()
        directory = self._directory(body.get("directory", ".dikdik/automations"))
        branch, remote = body.get("branch", "main"), body.get("remote", "origin")
        if not isinstance(branch, str) or not NAME.fullmatch(branch) or branch.startswith("-"):
            raise ValueError("Branch must be a safe Git branch name")
        if not isinstance(remote, str) or not NAME.fullmatch(remote) or remote.startswith("-"):
            raise ValueError("Remote must be a safe Git remote name")
        current = self._git(repository, "branch", "--show-current").stdout.strip()
        if not current or current != branch:
            raise ValueError(f"Check out the {branch} branch before configuring sync")
        with self.store.connect() as db:
            db.execute("INSERT INTO automation_git_sync(id,repository,directory,branch,remote,updated) VALUES(1,?,?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET repository=excluded.repository,directory=excluded.directory,branch=excluded.branch,remote=excluded.remote,updated=excluded.updated",
                       (saved_repository, directory, branch, remote, now))
        return 200, self.get()[1]

    @staticmethod
    def _slug(name: str, identity: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "automation"
        return f"{slug}-{identity[:8]}"

    def _desired(self) -> tuple[dict[str, bytes], list[str]]:
        files, names = {}, []
        rows = self.automations.list_automations()[1]
        for row in sorted(rows, key=lambda item: item["id"]):
            folder = self._slug(row["name"], row["id"])
            document = self.automations.export(row["id"])[1]
            relative = f"{folder}/automation.json"
            files[relative] = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
            names.append(relative)
        manifest = {"version": 1, "kind": "dikdik-automations", "files": names}
        files["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        return files, names

    def _configured(self):
        status, config = self.get()
        if not config["configured"]:
            raise ValueError("Configure Automation Git sync first")
        return config, self._repository(config["repository"])

    def status(self):
        config, repository = self._configured()
        base = repository / config["directory"]
        desired, _ = self._desired()
        if base.exists() and not (base / "manifest.json").is_file() and any(base.iterdir()):
            raise ValueError("Sync directory is not empty and is not managed by Dikdik")
        with self.store.connect() as db:
            row = db.execute("SELECT last_manifest,last_revision FROM automation_git_sync WHERE id=1").fetchone()
        previous = json.loads(row["last_manifest"]) if row and row["last_manifest"] else []
        changed = []
        for relative, content in desired.items():
            path = base / relative
            if not path.is_file() or path.read_bytes() != content:
                changed.append(relative)
        deleted = [relative for relative in previous if relative not in desired and (base / relative).exists()]
        return 200, {**config, "pending": len(changed) + len(deleted), "changed": changed, "deleted": deleted,
                     "last_revision": row["last_revision"] if row else None}

    def sync(self, body, now):
        if not isinstance(body, dict) or set(body) - {"approve_push"} or not isinstance(body.get("approve_push", False), bool):
            raise ValueError("Sync accepts approve_push as a boolean")
        config, repository = self._configured()
        before = self.status()[1]
        base = repository / config["directory"]
        desired, names = self._desired()
        with self.store.connect() as db:
            row = db.execute("SELECT last_manifest FROM automation_git_sync WHERE id=1").fetchone()
        previous = json.loads(row["last_manifest"]) if row and row["last_manifest"] else []
        base.mkdir(parents=True, exist_ok=True)
        for leftover in base.glob("*.tmp"):
            if leftover.is_file():
                leftover.unlink()  # a crashed write left a partial export behind
        for relative in previous:
            if relative not in desired:
                path = (base / relative).resolve()
                try:
                    path.relative_to(base.resolve())
                except ValueError:
                    continue
                if path.is_file():
                    path.unlink()
                parent = path.parent
                if parent != base and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        for relative, content in desired.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        directory = config["directory"]
        self._git(repository, "add", "--", directory)
        staged = self._git(repository, "diff", "--cached", "--quiet", "--", directory, check=False).returncode != 0
        committed = False
        revision = self._git(repository, "rev-parse", "HEAD").stdout.strip()
        managed = [*names, "manifest.json"]
        with self.store.connect() as db:
            db.execute("UPDATE automation_git_sync SET last_manifest=?,last_revision=?,updated=? WHERE id=1",
                       (json.dumps(managed), revision, now))
        if staged:
            env = {**os.environ, "GIT_AUTHOR_NAME": "Dikdik Automations", "GIT_AUTHOR_EMAIL": "dikdik@local.invalid",
                   "GIT_COMMITTER_NAME": "Dikdik Automations", "GIT_COMMITTER_EMAIL": "dikdik@local.invalid", "GIT_TERMINAL_PROMPT": "0"}
            result = subprocess.run(["git", "-c", "core.hooksPath=NUL", "commit", "--no-verify", "-m", "Sync Dikdik automations", "--", directory],
                                    cwd=repository, capture_output=True, text=True, timeout=30, env=env, check=False)
            if result.returncode:
                raise ValueError((result.stderr or result.stdout or "Could not commit automation sync").strip()[:500])
            committed = True
            revision = self._git(repository, "rev-parse", "HEAD").stdout.strip()
            with self.store.connect() as db:
                db.execute("UPDATE automation_git_sync SET last_revision=? WHERE id=1", (revision,))
        pushed = False
        if body.get("approve_push") is True:
            self._git(repository, "push", config["remote"], config["branch"])
            pushed = True
        return 200, {"pending_before": before["pending"], "committed": committed, "pushed": pushed, "revision": revision}
