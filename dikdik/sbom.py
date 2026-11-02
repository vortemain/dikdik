"""Software bill of materials (CycloneDX 1.5 JSON) from the project's lockfiles and manifests.

    python -m dikdik.sbom [PATH] [--output sbom.cdx.json] [--deny GPL-3.0-only,AGPL-3.0-only]

Components come from the same lockfile parser as the OSV audit (Cargo, npm, Bun, pip, Poetry/uv,
Composer), plus pyproject dependencies (and extras) pinned with `==`. Each gets a
package URL (purl). Licenses come from what is on this computer, offline: npm's package-lock,
installed Python distributions' metadata, Cargo's downloaded crate sources and `node_modules`. A
license is recorded only for the exact version found; others stay unknown rather than guessed.
`--deny` exits 1 when a component's license (or any id in its SPDX expression) is on the list.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import sys
import time
import tomllib
import uuid
from pathlib import Path
from urllib.parse import quote

from .security_scan import lockfile_packages, project_files

PURL_TYPE = {"PyPI": "pypi", "npm": "npm", "crates.io": "cargo", "Packagist": "composer"}
COPYLEFT = re.compile(r"(?i)\b(A?GPL|LGPL|MPL|EPL|CDDL|EUPL|OSL|SSPL)\b")


def purl(ecosystem: str, name: str, version: str) -> str:
    kind = PURL_TYPE[ecosystem]
    if kind == "pypi":
        name = re.sub(r"[-_.]+", "-", name).lower()
    if kind == "npm" and name.startswith("@"):
        scope, _, bare = name.partition("/")
        return f"pkg:npm/{quote(scope)}/{quote(bare)}@{quote(version)}"
    if kind == "composer" and "/" in name:
        vendor, _, bare = name.partition("/")
        return f"pkg:composer/{quote(vendor)}/{quote(bare)}@{quote(version)}"
    return f"pkg:{kind}/{quote(name)}@{quote(version)}"


def _licenses(files: dict[str, str]) -> dict[tuple[str, str], str]:
    """(name, version) -> license, where a lockfile records one."""
    found = {}
    for path, text in files.items():
        if path.endswith("package-lock.json"):
            try:
                for key, package in json.loads(text).get("packages", {}).items():
                    if key.startswith("node_modules/") and isinstance(package.get("license"), str):
                        found[(key.rsplit("node_modules/", 1)[1], package.get("version", ""))] = package["license"]
            except (ValueError, AttributeError):
                continue
    return found


CLASSIFIERS = {"MIT License": "MIT", "Apache Software License": "Apache-2.0", "BSD License": "BSD-3-Clause",
               "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
               "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0"}


def _python_license(name: str, version: str) -> str | None:
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    if dist.version != version:
        return None
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 64 and text.upper() != "UNKNOWN":
        return text
    found = [CLASSIFIERS[c.rsplit(" :: ", 1)[1]] for c in meta.get_all("Classifier") or []
             if c.startswith("License :: ") and c.rsplit(" :: ", 1)[1] in CLASSIFIERS]
    return " OR ".join(dict.fromkeys(found)) or None


def _cargo_license(name: str, version: str) -> str | None:
    registry = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo") / "registry" / "src"
    for manifest in registry.glob(f"*/{name}-{version}/Cargo.toml"):
        try:
            package = tomllib.loads(manifest.read_text(encoding="utf-8")).get("package", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(package.get("license"), str):
            return package["license"]
    return None


def _npm_license(root: Path, name: str, version: str) -> str | None:
    try:
        package = json.loads((root / "node_modules" / name / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if package.get("version") == version and isinstance(package.get("license"), str):
        return package["license"]
    return None


def installed_license(root: Path | None, package: dict) -> str | None:
    """The license of exactly this name and version, from what is installed or downloaded here
    (only when building for a project folder, so `build(files)` alone stays deterministic)."""
    if root is None:
        return None
    name, version = package["name"], package["version"]
    if package["ecosystem"] == "PyPI":
        return _python_license(name, version)
    if package["ecosystem"] == "crates.io":
        return _cargo_license(name, version)
    if package["ecosystem"] == "npm":
        return _npm_license(root, name, version)
    return None


def _project(files: dict[str, str]) -> dict:
    for name in ("pyproject.toml", "package.json", "Cargo.toml"):
        text = files.get(name)
        if not text:
            continue
        try:
            data = tomllib.loads(text) if name.endswith(".toml") else json.loads(text)
        except (ValueError, tomllib.TOMLDecodeError):
            continue
        section = data.get("project") or data.get("package") or data
        if isinstance(section, dict) and section.get("name"):
            return {"type": "application", "name": str(section["name"]), "version": str(section.get("version", "")),
                    **({"licenses": [{"license": {"id": section["license"]}}]} if isinstance(section.get("license"), str) else {})}
    return {"type": "application", "name": "project", "version": ""}


def _pyproject_pins(files: dict[str, str]) -> list[dict]:
    text = files.get("pyproject.toml")
    if not text:
        return []
    try:
        project = tomllib.loads(text).get("project", {})
    except tomllib.TOMLDecodeError:
        return []
    declared = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        declared += extra
    pins = []
    for requirement in declared:
        match = re.match(r"\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9_.+!-]+)\s*(?:;.*)?$", requirement)
        if match:
            pins.append({"ecosystem": "PyPI", "name": match.group(1), "version": match.group(2), "file": "pyproject.toml"})
    return pins


def build(files: dict[str, str], timestamp: str | None = None, root: Path | None = None) -> dict:
    packages = lockfile_packages(files) + _pyproject_pins(files)  # duplicates collapse on purl below
    licenses = _licenses(files)
    components, seen = [], set()
    for package in sorted(packages, key=lambda p: (p["ecosystem"], p["name"].lower(), p["version"])):
        if package["ecosystem"] not in PURL_TYPE:
            continue
        ref = purl(package["ecosystem"], package["name"], package["version"])
        if ref in seen:
            continue
        seen.add(ref)
        license_id = licenses.get((package["name"], package["version"])) or installed_license(root, package)
        components.append({"type": "library", "bom-ref": ref, "name": package["name"], "version": package["version"], "purl": ref,
                           **({"licenses": [{"license": {"id": license_id}}]} if license_id else {}),
                           "properties": [{"name": "dikdik:source", "value": package["file"]}]})
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "tools": {"components": [{"type": "application", "name": "dikdik-sbom"}]},
                         "component": _project(files)},
            "components": components}


def license_report(document: dict, deny: list[str]) -> dict:
    known = [(c["name"], c["licenses"][0]["license"]["id"]) for c in document["components"] if c.get("licenses")]
    denied = sorted({(n, lic) for n, lic in known if set(re.findall(r"[A-Za-z0-9.+-]+", lic)) & set(deny)})
    return {"components": len(document["components"]), "with_license": len(known),
            "unknown_license": len(document["components"]) - len(known),
            "copyleft": sorted({f"{n} ({lic})" for n, lic in known if COPYLEFT.search(lic)}),
            "denied": [f"{n} ({lic})" for n, lic in denied]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CycloneDX SBOM from lockfiles")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--output", help="write the SBOM here instead of standard output")
    parser.add_argument("--deny", default="", help="comma-separated SPDX licence ids that fail the run")
    args = parser.parse_args(argv)
    document = build(project_files(Path(args.path)), root=Path(args.path))
    text = json.dumps(document, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    report = license_report(document, [d.strip() for d in args.deny.split(",") if d.strip()])
    print(json.dumps(report), file=sys.stderr)
    return 1 if report["denied"] else 0


if __name__ == "__main__":
    sys.exit(main())
