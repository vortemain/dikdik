"""The SBOM is valid CycloneDX built from real lockfile entries, with licences only where recorded."""

import importlib.metadata
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import sbom

FILES = {
    "pyproject.toml": '[project]\nname = "shop"\nversion = "1.2.0"\nlicense = "MIT"\n'
                      'dependencies = ["Flask_Login==0.6.3", "requests>=2"]\n[project.optional-dependencies]\ndev = ["pytest==8.3.0"]\n',
    "Cargo.lock": '[[package]]\nname = "serde"\nversion = "1.0.200"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n',
    "web/package-lock.json": json.dumps({"packages": {"": {}, "node_modules/@scope/ui": {"version": "2.0.0", "license": "MIT"},
                                                      "node_modules/gpl-thing": {"version": "1.0.0", "license": "GPL-3.0-only"}}}),
    "requirements.txt": "pytest==8.3.0\n",
}


class SbomTests(unittest.TestCase):
    def test_purls(self):
        self.assertEqual(sbom.purl("PyPI", "Flask_Login", "0.6.3"), "pkg:pypi/flask-login@0.6.3")
        self.assertEqual(sbom.purl("npm", "@scope/ui", "2.0.0"), "pkg:npm/%40scope/ui@2.0.0")
        self.assertEqual(sbom.purl("crates.io", "serde", "1.0.200"), "pkg:cargo/serde@1.0.200")
        self.assertEqual(sbom.purl("Packagist", "laravel/framework", "11.0.0"), "pkg:composer/laravel/framework@11.0.0")

    def test_document(self):
        document = sbom.build(FILES, timestamp="2026-11-01T00:00:00Z")
        self.assertEqual((document["bomFormat"], document["specVersion"]), ("CycloneDX", "1.5"))
        self.assertTrue(document["serialNumber"].startswith("urn:uuid:"))
        self.assertEqual(document["metadata"]["component"]["name"], "shop")
        purls = [c["purl"] for c in document["components"]]
        self.assertEqual(purls, sorted(set(purls), key=purls.index))  # no duplicates (pytest appears twice in inputs)
        self.assertEqual(set(purls), {"pkg:cargo/serde@1.0.200", "pkg:npm/%40scope/ui@2.0.0", "pkg:npm/gpl-thing@1.0.0",
                                      "pkg:pypi/flask-login@0.6.3", "pkg:pypi/pytest@8.3.0"})  # unpinned requests is not invented
        ui = next(c for c in document["components"] if c["name"] == "@scope/ui")
        self.assertEqual(ui["licenses"], [{"license": {"id": "MIT"}}])
        self.assertNotIn("licenses", next(c for c in document["components"] if c["name"] == "serde"))

    def test_licence_report_and_deny(self):
        report = sbom.license_report(sbom.build(FILES), ["GPL-3.0-only"])
        self.assertEqual((report["with_license"], report["copyleft"], report["denied"]), (2, ["gpl-thing (GPL-3.0-only)"], ["gpl-thing (GPL-3.0-only)"]))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)
            for path, text in FILES.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text, encoding="utf-8")
            out = root / "bom.json"
            self.assertEqual(sbom.main([str(root), "--output", str(out), "--deny", "GPL-3.0-only"]), 1)
            self.assertEqual(sbom.main([str(root), "--output", str(out)]), 0)
            self.assertEqual(len(json.loads(out.read_text(encoding="utf-8"))["components"]), 5)

    def test_licences_come_from_what_is_installed_for_the_exact_version(self):
        installed = importlib.metadata.distribution("coverage") if importlib.util.find_spec("coverage") else None
        with tempfile.TemporaryDirectory() as temp:
            root, cargo = Path(temp) / "project", Path(temp) / "cargo"
            crate = cargo / "registry" / "src" / "index.crates.io-x" / "serde-1.0.200"
            crate.mkdir(parents=True)
            (crate / "Cargo.toml").write_text('[package]\nname = "serde"\nlicense = "MIT OR Apache-2.0"\n', encoding="utf-8")
            module = root / "node_modules" / "@scope" / "ui"
            module.mkdir(parents=True)
            (module / "package.json").write_text('{"name": "@scope/ui", "version": "2.0.0", "license": "ISC"}', encoding="utf-8")
            with patch.dict(os.environ, {"CARGO_HOME": str(cargo)}):
                self.assertEqual(sbom.installed_license(root, {"ecosystem": "crates.io", "name": "serde", "version": "1.0.200"}), "MIT OR Apache-2.0")
                self.assertIsNone(sbom.installed_license(root, {"ecosystem": "crates.io", "name": "serde", "version": "1.0.201"}))
            self.assertEqual(sbom.installed_license(root, {"ecosystem": "npm", "name": "@scope/ui", "version": "2.0.0"}), "ISC")
            self.assertIsNone(sbom.installed_license(root, {"ecosystem": "npm", "name": "@scope/ui", "version": "3.0.0"}))
            self.assertIsNone(sbom.installed_license(None, {"ecosystem": "npm", "name": "@scope/ui", "version": "2.0.0"}))
            if installed:
                found = sbom.installed_license(root, {"ecosystem": "PyPI", "name": "coverage", "version": installed.version})
                self.assertIn("Apache", found or "")
                self.assertIsNone(sbom.installed_license(root, {"ecosystem": "PyPI", "name": "coverage", "version": "0.0.1"}))

    def test_deny_matches_any_id_in_an_spdx_expression(self):
        document = {"components": [{"name": "dual", "licenses": [{"license": {"id": "MIT OR AGPL-3.0-only"}}]},
                                   {"name": "fine", "licenses": [{"license": {"id": "Apache-2.0"}}]}]}
        self.assertEqual(sbom.license_report(document, ["AGPL-3.0-only"])["denied"], ["dual (MIT OR AGPL-3.0-only)"])


if __name__ == "__main__":
    unittest.main()
