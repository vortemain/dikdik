"""Installing skills from folders, Git sources and marketplaces the user adds.

Installed skills live in `<data>/skills-installed/<name>/` next to an
`.installed.json` record of where they came from. Nothing here runs code from a
source: Git clones with hooks, prompts and unusual protocols disabled, only
regular files are copied, and sizes are bounded. Fetching from the network
always needs `approve_network: true` on that request.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_FILES, MAX_BYTES = 50, 512 * 1024
GIT_TIMEOUT = 60
RECORD = ".installed.json"
SAFE_GIT = ["-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=always", "-c", "credential.helper=",
            "-c", "core.symlinks=false", "-c", "advice.detachedHead=false"]


class InstallConflict(ValueError):
    pass


def remove_tree(path: Path) -> None:
    """Delete a folder even when Git left read-only files in it (Windows)."""
    def retry(function, target, _error):
        os.chmod(target, 0o700)
        function(target)
    if Path(path).exists():
        shutil.rmtree(path, onexc=retry)


def front_matter(text: str) -> dict:
    """Strict `---` front matter: `key: value` lines only; anything else is malformed."""
    text = text.lstrip("﻿")  # a byte-order mark from Windows editors
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with --- front matter naming the skill")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("SKILL.md front matter is not closed with ---")
    meta = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key.strip()):
            raise ValueError("SKILL.md front matter is malformed")
        meta[key.strip()] = value.strip().strip("\"'")[:500]
    if not meta.get("name"):
        raise ValueError("SKILL.md front matter needs a name")
    return meta


def parse_source(source: str) -> tuple[str, bool]:
    """(git URL or local path, is_network). `github:owner/repo` becomes its HTTPS URL."""
    if not isinstance(source, str) or not source.strip() or len(source) > 500 or any(ord(c) < 32 for c in source):
        raise ValueError("Give a folder path, a Git URL or github:owner/repo")
    source = source.strip()
    match = re.fullmatch(r"github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", source)
    if match:
        return f"https://github.com/{match[1]}/{match[2]}.git", True
    if source.startswith("https://"):
        return source, True
    if source.startswith("file://"):
        return source, False
    if re.match(r"^[a-z][a-z0-9+.-]*://", source):
        raise ValueError("Only https://, file:// and github: sources are supported")
    return source, False


def _git(*args: str, cwd: Path | None = None) -> str:
    if shutil.which("git") is None:
        raise ValueError("Git is not installed on this machine")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}
    try:
        done = subprocess.run(["git", *SAFE_GIT, *args], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT, env=env,
                              shell=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ValueError("Fetching the source took too long") from None
    except OSError:
        raise ValueError("Git could not be started") from None
    if done.returncode:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"Could not fetch the source: {detail[-1][:200] if detail else 'git failed'}")
    return done.stdout.decode("utf-8", "replace").strip()


def fetch(url: str, ref: str | None, into: Path) -> str:
    """Clone `url` into `into` at `ref` (default branch if None); returns the resolved commit."""
    if ref is not None and (not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", ref) or ref.startswith("-")):
        raise ValueError("ref must be a branch, tag or commit")
    _git("clone", "--quiet", "--no-checkout", "--no-recurse-submodules", "--", url, str(into))
    target = ref or "HEAD"
    try:
        commit = _git("rev-parse", "--verify", "--quiet", f"{target}^{{commit}}", cwd=into)
    except ValueError:
        try:
            commit = _git("rev-parse", "--verify", "--quiet", f"origin/{target}^{{commit}}", cwd=into)
        except ValueError:
            raise ValueError(f"The source has no ref “{ref}”") from None
    _git("checkout", "--quiet", commit, cwd=into)
    remove_tree(into / ".git")  # only the files are kept; nothing from the repository's Git data is used
    return commit


def _inside(base: Path, sub: str | None) -> Path:
    if not sub:
        return base
    if not isinstance(sub, str) or sub.startswith(("/", "\\")) or ".." in Path(sub).parts or len(sub) > 300:
        raise ValueError("repo_path must be a folder inside the source")
    target = (base / sub).resolve()
    if base.resolve() != target and base.resolve() not in target.parents:
        raise ValueError("repo_path must be a folder inside the source")
    return target


def copy_skill(folder: Path, destination: Path) -> dict:
    """Copy one skill folder (regular files only, bounded); returns its front matter."""
    skill = folder / "SKILL.md"
    if not skill.is_file() or skill.is_symlink():
        raise ValueError("That folder has no SKILL.md")
    meta = front_matter(skill.read_text(encoding="utf-8", errors="replace")[:16384])
    files, total = [], 0
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts) or path.is_symlink():
            continue
        if path.is_file():
            total += path.stat().st_size
            files.append(path)
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise ValueError(f"A skill can have at most {MAX_FILES} files and {MAX_BYTES // 1024} KB")
    destination.mkdir(parents=True)
    for path in files:
        target = destination / path.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return meta


class SkillStore:
    """Installed skills plus the marketplaces (catalog sources) the user added."""

    def __init__(self, data: Path, store):
        self.root = Path(data) / "skills-installed"
        self.cache = Path(data) / "marketplace-cache"
        self.store = store
        self.root.mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS marketplaces (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, source TEXT NOT NULL, "
                       "repo_path TEXT, synced REAL, status TEXT, created REAL NOT NULL)")

    # --- installed skills ------------------------------------------------------------
    def record(self, name: str) -> dict:
        try:
            return json.loads((self.root / name / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"source": "local"}  # copied in by hand: still listed (self-heal)

    def install(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        url, network = parse_source(body.get("source"))
        if network and body.get("approve_network") is not True:
            raise ValueError("Installing from the internet needs your approval for this request")
        force = body.get("force") is True
        with tempfile.TemporaryDirectory(dir=self.root.parent) as temp:
            work = Path(temp)
            resolved = None
            if url.startswith(("https://", "file://")):
                resolved = fetch(url, body.get("ref"), work / "src")
                base = work / "src"
            else:
                base = Path(url).expanduser()
                if not base.is_absolute() or not base.is_dir():
                    raise ValueError("Give the full path of a folder on this machine")
            folder = _inside(base, body.get("repo_path"))
            staged = work / "skill"
            meta = copy_skill(folder, staged)
            name = meta["name"].strip().lower()
            if not NAME.fullmatch(name):
                raise ValueError("A skill name is 1–64 lowercase letters, digits or -")
            if body.get("name") and body["name"] != name:
                raise ValueError(f"That source holds “{name}”, not “{body['name']}”")
            target = self.root / name
            if target.exists() and not force:
                raise InstallConflict(f"“{name}” is already installed; refresh it or reinstall with force")
            record = {"name": name, "source": body["source"].strip(), "repo_path": body.get("repo_path") or "",
                      "requested_ref": body.get("ref") or "", "resolved_ref": resolved or "", "installed_at": time.time()}
            (staged / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
            remove_tree(target)
            shutil.move(str(staged), str(target))
        return {"name": name, **{k: record[k] for k in ("source", "resolved_ref")}}

    def refresh(self, name: str, approve_network: bool) -> dict:
        record = self.record(name)
        if not (self.root / name).is_dir():
            raise KeyError(name)
        if record.get("source") == "local":
            raise ValueError("This skill was copied in by hand; there is no source to refresh from")
        return self.install({"source": record["source"], "repo_path": record.get("repo_path") or None,
                             "ref": record.get("requested_ref") or None, "force": True, "approve_network": approve_network})

    def uninstall(self, name: str) -> dict:
        target = self.root / name
        if not NAME.fullmatch(name) or not target.is_dir():
            raise KeyError(name)
        record_path = target / ".installed.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
        except (OSError, ValueError):
            record = {}
        remove_tree(target)
        if record.get("source") == "bundled":  # tombstone so a removed bundled skill does not reseed
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (f"bundled_uninstalled:{name}", "1"))
        return {"deleted": name}

    # --- marketplaces ---------------------------------------------------------------------
    def marketplaces(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM marketplaces ORDER BY created")]

    def add_marketplace(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name):
            raise ValueError("Name must contain 1–64 characters on one line")
        parse_source(body.get("source"))
        repo_path = body.get("repo_path") or None
        if repo_path is not None:
            _inside(Path("."), repo_path)
        market = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40] or "marketplace"
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM marketplaces WHERE name=? OR id=?", (name.strip(), market)).fetchone():
                raise ValueError("A marketplace with that name already exists")
            db.execute("INSERT INTO marketplaces(id,name,source,repo_path,created) VALUES(?,?,?,?,?)",
                       (market, name.strip(), body["source"].strip(), repo_path, time.time()))
        return {"id": market}

    def remove_marketplace(self, market: str) -> dict:
        with self.store.connect() as db:
            if db.execute("DELETE FROM marketplaces WHERE id=?", (market,)).rowcount == 0:
                raise KeyError(market)
        remove_tree(self.cache / market)
        return {"deleted": market}

    def sync(self, market: str, approve_network: bool) -> dict:
        """Fetch a marketplace into the cache; on failure the previous copy stays for offline use."""
        row = next((m for m in self.marketplaces() if m["id"] == market), None)
        if row is None:
            raise KeyError(market)
        url, network = parse_source(row["source"])
        if network and approve_network is not True:
            raise ValueError("Syncing from the internet needs your approval for this request")
        status = "ok"
        try:
            with tempfile.TemporaryDirectory(dir=self.root.parent) as temp:
                fresh = Path(temp) / "copy"
                if url.startswith(("https://", "file://")):
                    fetch(url, None, fresh)
                else:
                    shutil.copytree(Path(url), fresh, symlinks=False, ignore=shutil.ignore_patterns(".git"))
                self.cache.mkdir(exist_ok=True)
                remove_tree(self.cache / market)
                shutil.move(str(fresh), str(self.cache / market))
        except (ValueError, OSError) as exc:
            status = f"error: {exc}"[:200]
        with self.store.connect() as db:
            db.execute("UPDATE marketplaces SET synced=?, status=? WHERE id=?", (time.time(), status, market))
        return {"id": market, "status": status}

    def catalog(self) -> list[dict]:
        """Skills found in synced marketplaces, marked installed or not."""
        installed = {p.name for p in self.root.iterdir() if p.is_dir()}
        entries = []
        for market in self.marketplaces():
            base = self.cache / market["id"]
            if not base.is_dir():
                continue
            try:
                root = _inside(base, market.get("repo_path"))
            except ValueError:
                continue
            for skill in sorted(root.glob("**/SKILL.md"))[:500]:
                if any(part.startswith(".") for part in skill.relative_to(base).parts):
                    continue
                try:
                    meta = front_matter(skill.read_text(encoding="utf-8", errors="replace")[:16384])
                except (ValueError, OSError):
                    continue
                name = meta["name"].strip().lower()
                entries.append({"marketplace": market["id"], "marketplace_name": market["name"], "name": name,
                                "description": meta.get("description", ""), "category": meta.get("category", ""),
                                "repo_path": skill.parent.relative_to(base).as_posix(), "source": market["source"],
                                "installed": name in installed})
        return entries

    def install_from_catalog(self, market: str, repo_path: str) -> dict:
        """Install a catalog entry from the synced copy (no new network fetch)."""
        row = next((m for m in self.marketplaces() if m["id"] == market), None)
        base = self.cache / market
        if row is None or not base.is_dir():
            raise KeyError(market)
        with tempfile.TemporaryDirectory(dir=self.root.parent) as temp:
            staged = Path(temp) / "skill"
            meta = copy_skill(_inside(base, repo_path), staged)
            name = meta["name"].strip().lower()
            if not NAME.fullmatch(name):
                raise ValueError("A skill name is 1–64 lowercase letters, digits or -")
            if (self.root / name).exists():
                raise InstallConflict(f"“{name}” is already installed")
            (staged / RECORD).write_text(json.dumps({"name": name, "source": row["source"], "repo_path": repo_path,
                                                     "requested_ref": "", "resolved_ref": "", "marketplace": market,
                                                     "installed_at": time.time()}, indent=2), encoding="utf-8")
            shutil.move(str(staged), str(self.root / name))
        return {"name": name}
