"""Validate portable release archives and write SHA256SUMS.txt; standard library only."""

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def safe_names(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError(f"Unsafe archive member: {name}")
        if any(part in {".git", ".dikdik", "node_modules", "upstream", "__pycache__"} for part in path.parts):
            raise ValueError(f"Private/build material in archive: {name}")
        if path.name.startswith(".env") or path.suffix.lower() in {".pyc", ".pyd", ".so", ".dll", ".dylib"}:
            raise ValueError(f"Nonportable/private archive member: {name}")


def check_release(directory, tag=None):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag must match pyproject.toml version")
    module = (ROOT / "dikdik/__init__.py").read_text(encoding="utf-8")
    if not re.search(r'__version__ = "' + re.escape(version) + r'"', module):
        raise ValueError("Package version differs from project metadata")
    crate = tomllib.loads((ROOT / "crates/dikdik-index/Cargo.toml").read_text(encoding="utf-8"))
    if crate["package"]["version"] != version or crate["package"]["license"] != "MIT":
        raise ValueError("Rust crate version/license differs from release metadata")
    if project["license"] != "MIT" or json.loads((ROOT / "package.json").read_text())["license"] != "MIT":
        raise ValueError("Project manifests must declare MIT")

    wheel = directory / f"dikdik-{version}-py3-none-any.whl"
    source = directory / f"dikdik-{version}.tar.gz"
    if set(directory.glob("*.whl")) != {wheel} or set(directory.glob("*.tar.gz")) != {source}:
        raise ValueError("Expected exactly one portable wheel and one source distribution")
    required = {p.relative_to(ROOT).as_posix() for p in (ROOT / "dikdik").rglob("*.py") if "__pycache__" not in p.parts}
    for folder in ("web", "skills", "browser_runtime"):
        required.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "dikdik" / folder).rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel members")
        safe_names(names)
        missing = required - set(names)
        if missing:
            raise ValueError(f"Wheel is missing runtime files: {sorted(missing)}")
        prefix = f"dikdik-{version}.dist-info/"
        metadata = BytesParser().parsebytes(archive.read(prefix + "METADATA"))
        if (metadata["Name"] != "dikdik" or metadata["Version"] != version
                or metadata["Requires-Python"] != project["requires-python"]
                or metadata["License-Expression"] != "MIT" or metadata.get_all("Requires-Dist")):
            raise ValueError("Invalid package identity/license or unexpected runtime dependencies")
        if archive.read(prefix + "licenses/LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("Wheel root license differs from repository license")
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        safe_names(member.name for member in members)
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Source distribution must not contain links or special files")
        prefix = f"dikdik-{version}/"
        names = {member.name for member in members}
        expected = required | {"LICENSE", "LICENSE-STATUS.md", "README.md", "pyproject.toml", "Cargo.toml",
                               "Cargo.lock", "crates/dikdik-index/Cargo.toml", "scripts/build_native.py",
                               "scripts/check_release.py", "scripts/requirements-release.txt"}
        if not {prefix + name for name in expected}.issubset(names):
            raise ValueError("Source distribution is missing runtime, license or build sources")
        with archive.extractfile(prefix + "LICENSE") as stream:
            if stream.read() != (ROOT / "LICENSE").read_bytes():
                raise ValueError("Source license differs from repository license")
    checksums = []
    for path in (wheel, source):
        with path.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (directory / "SHA256SUMS.txt").write_text("".join(checksums), encoding="utf-8", newline="\n")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag")
    args = parser.parse_args()
    version = check_release(args.directory.resolve(), args.tag)
    print(f"Validated Dikdik {version}: portable wheel, source, MIT, bundled notices and SHA-256 checksums.")


if __name__ == "__main__":
    main()
