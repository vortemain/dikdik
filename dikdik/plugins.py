"""Plugins: one folder that bundles skills, block hooks, subagents and MCP server definitions.

A plugin is a folder with `plugin.json` (or `.claude-plugin/plugin.json`):

    {"name": "review-kit", "version": "1.0.0", "description": "...",
     "skills": "skills", "agents": "agents", "hooks": {"block": [...]},
     "mcp_servers": [{"name": "issues", "transport": "http", "url": "http://127.0.0.1:9000/mcp"}],
     "prompt": "Review {{ branch }} carefully", "parameters": [{"name": "branch", "type": "text"}]}

Plugins come from `<data>/plugins/` (installed like skills) and
`<workspace>/.dikdik/plugins/` (shipped with the project). Neither does anything
until the user reviews and trusts it: only trusted, enabled plugins contribute
skills, hooks and subagents, and their MCP servers are added switched off so each
one is enabled by the user. Nothing in a plugin is run when it is installed, and
a plugin can never change approvals, models or limits.
"""

from __future__ import annotations  # PluginStore.list would otherwise shadow list in its annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .skills_market import RECORD, _inside, fetch, parse_source, remove_tree, InstallConflict
from .hooks import document_rules

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_FILES, MAX_BYTES = 200, 2 * 1024 * 1024
TEXT_LIMIT = 64 * 1024
PARAMETER_TYPES = {"text", "number", "boolean"}


def manifest_path(folder: Path) -> Path | None:
    for candidate in (folder / "plugin.json", folder / ".claude-plugin" / "plugin.json"):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def read_manifest(folder: Path, fallback_name: str | None = None) -> dict:
    """The validated manifest of a plugin folder; raises ValueError naming the problem."""
    path = manifest_path(folder)
    if path is None:
        if fallback_name is None:
            raise ValueError("That folder has no plugin.json")
        document = {"name": fallback_name}
    else:
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig")[:65536])
        except (OSError, ValueError):
            raise ValueError("plugin.json is not valid JSON") from None
        if not isinstance(document, dict):
            raise ValueError("plugin.json must be an object")
    name = str(document.get("name") or fallback_name or "").strip().lower()
    if not NAME.fullmatch(name) or len(name) > 64:
        raise ValueError("A plugin name is lowercase letters and digits joined by single dashes")
    servers = document.get("mcp_servers") or []
    if not isinstance(servers, list) or len(servers) > 10 or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in servers):
        raise ValueError("mcp_servers must be a list of at most 10 server definitions")
    parameters = document.get("parameters") or []
    if not isinstance(parameters, list) or len(parameters) > 20 or not all(
            isinstance(p, dict) and isinstance(p.get("name"), str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", p["name"])
            and p.get("type", "text") in PARAMETER_TYPES for p in parameters):
        raise ValueError("parameters must be a list of {name, type: text | number | boolean}")
    hooks = document.get("hooks", {})
    document_rules(hooks)
    return {"name": name, "version": str(document.get("version") or "")[:40], "description": str(document.get("description") or "")[:500],
            "skills": _dir(document.get("skills"), "skills"), "agents": _dir(document.get("agents"), "agents"),
            "hooks": hooks.get("block", []), "lifecycle_hooks": hooks.get("hooks", []), "mcp_servers": servers, "parameters": parameters,
            "prompt": str(document.get("prompt") or "")[:4000]}


def _dir(value, default: str) -> str:
    value = value if isinstance(value, str) and value.strip() else default
    if value.startswith(("/", "\\")) or Path(value).is_absolute() or ":" in value or ".." in Path(value).parts:
        raise ValueError("Plugin folders must stay inside the plugin")
    return value


def contents(folder: Path, manifest: dict) -> dict:
    """What a plugin would add, for the trust review: skills, subagents, hook rules, MCP servers."""
    skills_dir, agents_dir = folder / manifest["skills"], folder / manifest["agents"]
    skills = sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
    skills += sorted(p.stem for p in skills_dir.glob("*.md")) if skills_dir.is_dir() else []
    agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    servers = [{"name": s.get("name"), "transport": s.get("transport", "http"), "target": s.get("url") or s.get("command") or ""} for s in manifest["mcp_servers"]]
    hooks = [{"tool": h.get("tool", "*"), "pattern": h.get("pattern", ""), "message": h.get("message", "")} for h in manifest["hooks"] if isinstance(h, dict)]
    hooks += manifest.get("lifecycle_hooks", [])
    return {"skills": skills, "agents": agents, "mcp_servers": servers, "hooks": hooks}


def fill_prompt(template: str, parameters: list[dict], values: dict) -> str:
    """The plugin's starting message with launch parameters filled in (numbers default to 0, booleans to false)."""
    text = template
    for parameter in parameters:
        value = values.get(parameter["name"]) if isinstance(values, dict) else None
        kind = parameter.get("type", "text")
        if kind == "number":
            value = value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
        elif kind == "boolean":
            value = "yes" if value is True else "no"
        else:
            value = str(value or "")[:500]
        text = re.sub(r"\{\{\s*" + re.escape(parameter["name"]) + r"\s*\}\}", str(value), text)
    return text


class PluginStore:
    def __init__(self, data: Path, root: Path, store, mcp=None):
        self.dir = Path(data) / "plugins"
        self.project_dir = Path(root) / ".dikdik" / "plugins"
        self.cache = Path(data) / "marketplace-cache"
        self.store, self.mcp = store, mcp
        self.dir.mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    # --- state: installed plugins keep it in .installed.json; project plugins in SQLite -------------
    def _project_state(self, name: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (f"plugin:{name}",)).fetchone()
        return json.loads(row[0]) if row else {"enabled": False, "trusted": False}

    def _save_project_state(self, name: str, state: dict) -> None:
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"plugin:{name}", json.dumps(state)))

    def _record(self, name: str) -> dict:
        try:
            return json.loads((self.dir / name / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"source": "local", "enabled": False, "trusted": False}

    def _folders(self):
        """(scope, name, folder) for every plugin; an installed plugin wins over a project one of the same name."""
        seen = set()
        for scope, base in (("installed", self.dir), ("project", self.project_dir)):
            boundary = self.dir.parent.resolve() if scope == "installed" else self.project_dir.parent.parent.resolve()
            if not base.is_dir() or base.is_symlink() or boundary not in base.resolve().parents:
                continue
            for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
                if folder.is_symlink() or base.resolve() not in folder.resolve().parents:
                    continue
                if folder.name in seen or not NAME.fullmatch(folder.name) or manifest_path(folder) is None:
                    continue
                seen.add(folder.name)
                yield scope, folder.name, folder

    def _describe(self, scope: str, name: str, folder: Path) -> dict:
        try:
            manifest = read_manifest(folder)
        except ValueError as exc:
            return {"name": name, "scope": scope, "error": str(exc), "enabled": False, "trusted": False}
        state = self._record(name) if scope == "installed" else self._project_state(name)
        return {"name": name, "scope": scope, "version": manifest["version"], "description": manifest["description"],
                "enabled": bool(state.get("enabled")), "trusted": bool(state.get("trusted")),
                "source": state.get("source", "project" if scope == "project" else "local"), "resolved_ref": state.get("resolved_ref", ""),
                "parameters": manifest["parameters"], "prompt": manifest["prompt"], "adds": contents(folder, manifest)}

    def list(self) -> list[dict]:
        return [self._describe(scope, name, folder) for scope, name, folder in self._folders()]

    def get(self, name: str) -> dict:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("A plugin name is lowercase letters and digits joined by single dashes")
        for scope, found, folder in self._folders():
            if found == name:
                return {**self._describe(scope, name, folder), "files": self._files(folder)}
        raise KeyError(name)

    def _folder(self, name: str) -> tuple[str, Path]:
        self.get(name)
        for scope, found, folder in self._folders():
            if found == name:
                return scope, folder
        raise KeyError(name)

    @staticmethod
    def _files(folder: Path) -> list[str]:
        return [p.relative_to(folder).as_posix() for p in sorted(folder.rglob("*"))
                if p.is_file() and not p.is_symlink() and p.name != RECORD and not any(part.startswith(".") and part != ".claude-plugin" for part in p.relative_to(folder).parts)][:MAX_FILES]

    def read_file(self, name: str, path: str) -> dict:
        _, folder = self._folder(name)
        if path not in self._files(folder):
            raise KeyError(path)
        raw = (folder / path).read_bytes()[:TEXT_LIMIT]
        if b"\x00" in raw:
            return {"path": path, "text": "", "binary": True}
        return {"path": path, "text": raw.decode("utf-8", "replace"), "binary": False}

    # --- install, refresh, uninstall ---------------------------------------------------------
    def install(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        url, network = parse_source(body.get("source"))
        if network and body.get("approve_network") is not True:
            raise ValueError("Installing from the internet needs your approval for this request")
        with tempfile.TemporaryDirectory(dir=self.dir.parent) as temp:
            work = Path(temp)
            resolved = None
            if url.startswith(("https://", "file://")):
                resolved = fetch(url, body.get("ref"), work / "src")
                base = work / "src"
                fallback = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1]).lower()
            else:
                base = Path(url).expanduser()
                if not base.is_absolute() or not base.is_dir():
                    raise ValueError("Give the full path of a folder on this machine")
                fallback = base.name.lower()
            folder = _inside(base, body.get("repo_path"))
            if body.get("repo_path"):
                fallback = Path(body["repo_path"]).name.lower()
            manifest = read_manifest(folder, re.sub(r"[^a-z0-9]+", "-", fallback).strip("-") or None)
            name = manifest["name"]
            target = self.dir / name
            previous = self._record(name) if target.exists() else None
            if previous is not None and body.get("force") is not True:
                raise InstallConflict(f"“{name}” is already installed; update it or reinstall with force")
            staged = work / "plugin"
            self._copy(folder, staged)
            if manifest_path(staged) is None:  # a plain folder of skills/agents: name it so it can be found again
                (staged / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
            record = {"name": name, "source": body["source"].strip(), "repo_path": body.get("repo_path") or "", "requested_ref": body.get("ref") or "",
                      "resolved_ref": resolved or "", "installed_at": time.time(),
                      # A reinstall keeps the user's choices; trust is re-asked only if the plugin's contents changed.
                      "enabled": bool(previous and previous.get("enabled")), "trusted": bool(previous and previous.get("trusted")),
                      "mcp_ids": (previous or {}).get("mcp_ids", [])}
            if previous and contents(staged, read_manifest(staged, name)) != previous.get("adds"):
                record["enabled"] = record["trusted"] = False
            record["adds"] = contents(staged, read_manifest(staged, name))
            (staged / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
            remove_tree(target)
            shutil.move(str(staged), str(target))
        return {"name": name, "version": manifest["version"], "resolved_ref": record["resolved_ref"], "enabled": record["enabled"]}

    @staticmethod
    def _copy(folder: Path, destination: Path) -> None:
        files, total = [], 0
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if path.is_symlink() or any(part.startswith(".") and part != ".claude-plugin" for part in relative.parts):
                continue
            if path.is_file():
                total += path.stat().st_size
                files.append(path)
                if len(files) > MAX_FILES or total > MAX_BYTES:
                    raise ValueError(f"A plugin can have at most {MAX_FILES} files and {MAX_BYTES // 1024 // 1024} MB")
        destination.mkdir(parents=True)
        for path in files:
            target = destination / path.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def refresh(self, name: str, approve_network: bool) -> dict:
        scope, _ = self._folder(name)
        if scope != "installed":
            raise ValueError("Project plugins update with the project itself")
        record = self._record(name)
        if record.get("source", "local") == "local":
            raise ValueError("This plugin was copied in by hand; there is no source to update from")
        # An update follows the source's current version, dropping a pinned ref.
        return self.install({"source": record["source"], "repo_path": record.get("repo_path") or None, "force": True, "approve_network": approve_network})

    def uninstall(self, name: str) -> dict:
        scope, folder = self._folder(name)
        if scope != "installed":
            raise ValueError("Project plugins are part of the project; disable it instead")
        self._remove_servers(self._record(name).get("mcp_ids", []))
        remove_tree(folder)
        return {"deleted": name}

    # --- trust and enable ----------------------------------------------------------------
    def set_enabled(self, name: str, body) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise ValueError("enabled must be true or false")
        scope, folder = self._folder(name)
        state = self._record(name) if scope == "installed" else self._project_state(name)
        if body["enabled"] and not state.get("trusted"):
            if body.get("trust") is not True:
                raise ValueError("Review what this plugin adds and confirm you trust it before enabling it")
            state["trusted"] = True
            state["mcp_ids"] = self._add_servers(name, read_manifest(folder)["mcp_servers"])
        state["enabled"] = body["enabled"]
        if scope == "installed":
            (folder / RECORD).write_text(json.dumps(state, indent=2), encoding="utf-8")
        else:
            self._save_project_state(name, state)
        return {"name": name, "enabled": state["enabled"], "trusted": state["trusted"]}

    def _add_servers(self, plugin: str, servers: list[dict]) -> list[str]:
        """MCP servers from a trusted plugin are added switched off; the user enables each one."""
        if self.mcp is None:
            return []
        added, existing = [], {s["name"] for s in self.mcp.list()}
        for server in servers:
            name = f"{plugin}-{server['name']}"[:32]  # MCP server names are at most 32 characters
            if name in existing:
                continue
            body = {k: server[k] for k in ("transport", "url", "command", "env_var", "secret_ref") if server.get(k)}
            try:
                added.append(self.mcp.create({"name": name, **body})["id"])
            except (ValueError, KeyError):
                continue  # an unusable definition is skipped; the review listed it
        return added

    def _remove_servers(self, ids: list[str]) -> None:
        for server_id in ids or []:
            try:
                self.mcp.delete(server_id)
            except (KeyError, ValueError, AttributeError):
                pass

    # --- what enabled plugins contribute ----------------------------------------------------
    def active(self) -> list[tuple[str, Path, dict]]:
        out = []
        for scope, name, folder in self._folders():
            state = self._record(name) if scope == "installed" else self._project_state(name)
            if state.get("enabled") and state.get("trusted"):
                try:
                    out.append((name, folder, read_manifest(folder)))
                except ValueError:
                    continue
        return out

    def skill_dirs(self) -> list[tuple[str, Path]]:
        return [(name, folder / manifest["skills"]) for name, folder, manifest in self.active()
                if (folder / manifest["skills"]).is_dir() and folder.resolve() in (folder / manifest["skills"]).resolve().parents]

    def agent_dirs(self) -> list[tuple[str, Path]]:
        return [(f"plugin {name}", folder / manifest["agents"]) for name, folder, manifest in self.active()
                if (folder / manifest["agents"]).is_dir() and folder.resolve() in (folder / manifest["agents"]).resolve().parents]

    def hook_documents(self) -> list[tuple[str, dict]]:
        # Do not silently drop an enabled plugin whose policy became malformed.
        # Preserve a failure document so the central engine closes its gates.
        documents = []
        for scope, name, folder in self._folders():
            state = self._record(name) if scope == "installed" else self._project_state(name)
            if not (state.get("enabled") and state.get("trusted")):
                continue
            try:
                manifest = read_manifest(folder)
                documents.append((name, {"block": manifest["hooks"], "hooks": manifest["lifecycle_hooks"]}))
            except (OSError, ValueError, TypeError):
                documents.append((name, {"invalid": True}))
        return documents

    # --- catalog from the shared marketplaces --------------------------------------------------------
    def catalog(self, marketplaces: list[dict]) -> list[dict]:
        installed = {name for _, name, _ in self._folders()}
        entries = []
        for market in marketplaces:
            base = self.cache / market["id"]
            if not base.is_dir():
                continue
            for manifest_file in sorted(list(base.glob("plugins/*/plugin.json")) + list(base.glob("plugins/*/.claude-plugin/plugin.json")))[:200]:
                folder = manifest_file.parent if manifest_file.parent.name != ".claude-plugin" else manifest_file.parent.parent
                try:
                    manifest = read_manifest(folder)
                except ValueError:
                    continue
                entries.append({"marketplace": market["id"], "marketplace_name": market["name"], "name": manifest["name"],
                                "description": manifest["description"], "version": manifest["version"],
                                "repo_path": folder.relative_to(base).as_posix(), "installed": manifest["name"] in installed})
        return entries

    def install_from_catalog(self, market: str, repo_path: str) -> dict:
        base = self.cache / market
        if not base.is_dir():
            raise KeyError(market)
        return self.install({"source": str(_inside(base, repo_path))})
