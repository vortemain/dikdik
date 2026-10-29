# Releases

Original Dikdik source is MIT-licensed; third-party software, fonts and marks keep
their own notices. Release downloads include a portable Python wheel, a source
distribution and `SHA256SUMS.txt`. GitHub also provides archives of each Git tag.
The 0.x line is published as a developer preview, not as a production guarantee.

## Release checks

`.github/workflows/release.yml` validates pushes to main, pull requests, version
tags and manual runs. It tests Python 3.13 on Windows and Linux, runs the frontend
tests/typecheck/build, checks the committed browser bundle, builds distributions,
validates metadata/notices, rejects private or native build files, installs the
portable wheel outside the checkout and generates SHA-256 checksums.
Windows tests install the declared `tzdata` dependency for named time zones;
Linux uses its system IANA database. Project boundaries normalize Windows path
aliases before comparisons.

Actions are pinned to commit hashes. Test/package jobs have read-only repository
permissions; only the tag publication job has write access. Tags must match the
Python/package/Rust versions and have a matching release-notes file. A failed
check prevents automatic publication. No PyPI or npm upload occurs.

## Prepare the next release

1. Update `pyproject.toml`, `dikdik/__init__.py` and
   `crates/dikdik-index/Cargo.toml` together. Update Cargo.lock when required.
2. Add `docs/releases/vX.Y.Z.md` and update `CHANGELOG.md` and the README's
   installation example. Record only validation actually performed.
3. Run tests, commit and push main; wait for its checks. Create an annotated tag
   at that exact reviewed commit and push it. For example:

   ```bash
   git tag -a v0.1.1 -m "Dikdik 0.1.1 developer preview"
   git push origin v0.1.1
   ```

4. The tag workflow repeats checks and publishes the release assets and notes.
   Tags starting with `v0.` or containing a hyphen are prereleases. Published
   tags/assets must not be rewritten; fix problems in a new version.

## Build and verify locally

Use a fresh checkout at the release commit, with Python 3.13+:

```bash
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r scripts/requirements-release.txt
python -m build --no-isolation
python -m twine check --strict dist/*.whl dist/*.tar.gz
python scripts/check_release.py dist --tag v0.1.0
```

Install the wheel in a second empty virtual environment and run `dikdik --version`
from outside the source checkout. The wheel must use the Python indexer; it must
not carry a developer's platform-specific native library. For the optional Rust
accelerator, extract the source archive, run `python scripts/build_native.py` with
Rust installed, and run/install that source checkout in editable mode.

Checksums detect changed downloads; they are not signatures or proof of identity.
Use this repository's HTTPS release page as the distribution source. Production
readiness work listed in SECURITY.md remains separate from packaging checks.
