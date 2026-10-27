"""Canvas extension apps: installable app pages shown in a sandboxed frame.

An extension app is a directory with an `app.json` manifest (name, version,
description, entrypoint). Its entrypoint is one self-contained HTML page (inline
style, script and data: images) served at /api/apps/<name>/bundle once the user
enables it. The response's CSP `sandbox` gives the page an opaque origin, so its
script can never reach Dikdik's cookie, API or the network. Nothing runs on the host.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

MANIFEST_NAME = "app.json"
MAX_MANIFEST = 65536
MAX_BUNDLE = 10_000_000  # 10 MB


class ExtensionAppStore:
    def __init__(self, data: Path):
        self.data = data
        self.installed_dir = data / "apps-installed"
        self.installed_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, name: str) -> Path:
        return self.installed_dir / name / MANIFEST_NAME

    def _validate_name(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("App name must be a non-empty string")
        if len(name) > 64 or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("App name must contain 1-64 alphanumeric, dash or underscore characters")
        return name.strip()

    def list(self) -> list[dict]:
        apps = []
        for child in sorted(self.installed_dir.iterdir(), key=lambda p: p.name):
            manifest = child / MANIFEST_NAME
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    apps.append({"name": child.name, "version": data.get("version", ""),
                                 "description": data.get("description", ""),
                                 "entrypoint": data.get("entrypoint", "index.html"),
                                 "enabled": data.get("enabled", False),
                                 "source": data.get("source", "local")})
                except (ValueError, OSError):
                    pass
        return apps

    def get(self, name: str) -> dict:
        name = self._validate_name(name)
        manifest = self._manifest_path(name)
        if not manifest.is_file():
            raise KeyError(name)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return {"name": name, "version": data.get("version", ""),
                 "description": data.get("description", ""),
                 "entrypoint": data.get("entrypoint", "index.html"),
                 "enabled": data.get("enabled", False),
                 "source": data.get("source", "local")}

    def install(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Install body must be a JSON object")
        name = self._validate_name(body.get("name", ""))
        source = body.get("source", "")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty path or URL")
        ref = body.get("ref")
        path = body.get("path")
        # Check for duplicates
        if self._manifest_path(name).is_file():
            if not body.get("force", False):
                raise ValueError(f"App {name} is already installed; use force to replace")
        app_dir = self.installed_dir / name
        if source.startswith("/") or (len(source) > 1 and source[1] == ":"):
            # Local path
            src = Path(source).resolve()
            if not src.is_dir():
                raise ValueError(f"Source directory does not exist: {source}")
            manifest = src / MANIFEST_NAME
            if not manifest.is_file():
                raise ValueError(f"No {MANIFEST_NAME} in source directory")
            data = json.loads(manifest.read_text(encoding="utf-8")[:MAX_MANIFEST])
            # Copy the app directory
            if app_dir.exists():
                shutil.rmtree(app_dir)
            shutil.copytree(src, app_dir, symlinks=True)  # a link stays a link; bundle() refuses one that leaves the app
        else:
            raise ValueError("Only local directory sources are supported in this build")
        # Update the manifest with install metadata
        manifest_path = app_dir / MANIFEST_NAME
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data.get("entrypoint", "index.html")
        if not isinstance(entry, str) or not entry.endswith((".html", ".htm")) or ".." in Path(entry).parts or Path(entry).is_absolute():
            shutil.rmtree(app_dir)
            raise ValueError("entrypoint must be a relative .html file inside the app")
        data["enabled"] = False
        data["source"] = "local"
        if ref:
            data["resolved_ref"] = ref
        if path:
            data["repo_path"] = path
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return self.get(name)

    def update(self, name: str, body: dict) -> dict:
        name = self._validate_name(name)
        manifest = self._manifest_path(name)
        if not manifest.is_file():
            raise KeyError(name)
        if not isinstance(body, dict) or set(body) - {"enabled"}:
            raise ValueError("Only enabled can be changed")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if "enabled" in body:
            data["enabled"] = bool(body["enabled"])
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return self.get(name)

    def delete(self, name: str) -> None:
        name = self._validate_name(name)
        app_dir = self.installed_dir / name
        if not app_dir.is_dir():
            raise KeyError(name)
        shutil.rmtree(app_dir)

    def bundle(self, name: str, relative_path: str) -> tuple[bytes, str]:
        """Serve a static file from the app's bundle directory."""
        name = self._validate_name(name)
        app_dir = self.installed_dir / name
        if not app_dir.is_dir():
            raise KeyError(name)
        # Resolve the relative path safely
        target = (app_dir / relative_path).resolve()
        if app_dir.resolve() not in target.parents and target != app_dir.resolve():
            raise ValueError("Path escapes the app bundle directory")
        if not target.is_file():
            raise KeyError(relative_path)
        raw = target.read_bytes()
        if len(raw) > MAX_BUNDLE:
            raise ValueError("Bundle file exceeds the 10 MB limit")
        ext = target.suffix.lower()
        mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".json": "application/json",
                ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
                ".woff2": "font/woff2"}.get(ext, "application/octet-stream")
        return raw, mime