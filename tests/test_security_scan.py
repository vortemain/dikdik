"""The deterministic security scanner finds what it claims to, and only that."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dikdik import security_scan as scan

# Assembled at runtime so this file never contains a literal credential itself.
AWS = "AKIA" + "Q3EGJ7RZ5XN2LW4P"
GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def rules(report):
    return sorted(f["rule"] for f in report["findings"])


class SecretTests(unittest.TestCase):
    def test_known_credentials_are_found_and_redacted(self):
        report = scan.scan_files({"config.py": f'KEY = "{AWS}"\nTOKEN = "{GITHUB}"\n'})
        self.assertEqual(rules(report), ["SEC001", "SEC002"])
        for finding in report["findings"]:
            self.assertEqual(finding["severity"], "critical")
            self.assertNotIn(AWS, json.dumps(finding))
            self.assertNotIn(GITHUB, json.dumps(finding))
        self.assertEqual(report["findings"][0]["line"], 1)

    def test_private_key_header(self):
        report = scan.scan_files({"id_rsa": "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc\n"})
        self.assertEqual(rules(report), ["SEC003"])

    def test_generic_assignment_needs_entropy_and_skips_placeholders(self):
        strong = "Zq8#" + "vL2!pW9xR4tY"
        report = scan.scan_files({"a.py": f'password = "{strong}"\npassword = "changeme-please"\napi_key = "aaaaaaaaaaaaaaaa"\n'})
        self.assertEqual([(f["rule"], f["line"]) for f in report["findings"]], [("SEC010", 1)])

    def test_suppression_is_counted_not_hidden(self):
        report = scan.scan_files({"a.py": f'KEY = "{AWS}"  # dikdik-scan: ignore[SEC001] rotated test fixture\n'})
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["suppressed"], 1)
        other_rule = scan.scan_files({"a.py": f'KEY = "{AWS}"  # dikdik-scan: ignore[PY001]\n'})
        self.assertEqual(rules(other_rule), ["SEC001"])  # a suppression names the rule it silences


class CodeTests(unittest.TestCase):
    def test_python_dangerous_calls(self):
        source = "\n".join([
            "import subprocess, pickle, yaml, requests, os, tempfile, hashlib",
            "subprocess.run(cmd, shell=True)",               # PY001 high
            "subprocess.run('ls', shell=True)",              # PY001 medium
            "eval(user)",                                    # PY002
            "eval('1 + 1')",                                 # literal: fine
            "pickle.loads(blob)",                            # PY003
            "yaml.load(text)",                               # PY004
            "yaml.load(text, Loader=yaml.SafeLoader)",       # safe: fine
            "requests.get(url, verify=False)",               # PY005
            "tempfile.mktemp()",                             # PY006
            "os.system(command)",                            # PY007
            "db.execute(f'SELECT * FROM t WHERE id={x}')",   # PY008
            "db.execute('SELECT * FROM t WHERE id=?', (x,))",  # parameterized: fine
            "hashlib.md5(data)",                             # PY009
            "hashlib.md5(data, usedforsecurity=False)",      # declared non-security: fine
            "app.run(debug=True)",                           # PY010
        ])
        found = [(f["rule"], f["line"], f["severity"]) for f in scan.scan_files({"m.py": source})["findings"] if f["category"] == "security"]
        self.assertCountEqual(found, [
            ("PY001", 2, "high"), ("PY001", 3, "medium"), ("PY002", 4, "high"), ("PY003", 6, "medium"),
            ("PY004", 7, "high"), ("PY005", 9, "high"), ("PY006", 10, "low"), ("PY007", 11, "high"),
            ("PY008", 12, "high"), ("PY009", 14, "low"), ("PY010", 16, "medium")])

    def test_sql_is_safe_only_through_the_allowlisting_helpers(self):
        source = "\n".join([
            "def f(db, key, fields, where):",
            "    db.execute(f'UPDATE t SET {sql.assign([key], ALLOWED)} WHERE id=?', (1,))",      # sanctioned
            "    db.execute('SELECT * FROM t WHERE ' + sql.clauses(where, OK) + ' LIMIT ?', (1,))",  # sanctioned
            "    db.execute(f'UPDATE t SET {key}=? WHERE id=?', (1,))",                             # line 4: raw
            "    db.execute('SELECT * FROM t WHERE ' + ' AND '.join(where), ())",                   # line 5: raw
            "    query = 'SELECT * FROM t'",
            "    query += ' WHERE ' + ' AND '.join(where)",
            "    db.execute(query, ())",                                                            # line 8: built raw
            "    safe = 'SELECT * FROM t'",
            "    safe += ' WHERE ' + sql.clause(where, OK)",
            "    db.execute(safe, ())",                                                             # sanctioned
        ])
        found = sorted(f["line"] for f in scan.scan_files({"m.py": source})["findings"] if f["rule"] == "PY008")
        self.assertEqual(found, [4, 5, 8])

    def test_sql_helpers_reject_anything_off_the_allowlist(self):
        from dikdik import sql
        self.assertEqual(sql.assign(["name", "url"], {"name", "url"}), "name=?, url=?")
        self.assertEqual(sql.marks(3), "?,?,?")
        for bad in (lambda: sql.ident("name; DROP TABLE t", {"name; DROP TABLE t"}), lambda: sql.ident("id", {"name"}),
                    lambda: sql.assign([], {"name"}), lambda: sql.marks(0), lambda: sql.clause("1=1 OR 1", {"1=1"}),
                    lambda: sql.clauses(["a=?"], {"a=?"}, joiner="; ")):
            with self.assertRaises(ValueError):
                bad()

    def test_unparseable_python_is_not_a_crash(self):
        self.assertEqual(scan.scan_files({"broken.py": "def (:\n"})["findings"], [])

    def test_javascript_sinks(self):
        source = "el.innerHTML = user;\nel.innerHTML = '<b>fixed</b>';\n// eval(x) in a comment\nconst f = eval(code);\nexec(`rm ${path}`);\n"
        found = [(f["rule"], f["line"]) for f in scan.scan_files({"a.ts": source})["findings"]]
        self.assertCountEqual(found, [("JS002", 1), ("JS001", 4), ("JS003", 5)])

    def test_minified_bundles_are_skipped_and_reported(self):
        report = scan.scan_files({"app.js": "eval(x);" * 200})
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["skipped"][0]["reason"], "generated or minified code")


class QualityRuleTests(unittest.TestCase):
    def found(self, source, path="app/service.py"):
        return sorted((f["rule"], f["line"], f["category"]) for f in scan.scan_files({path: source})["findings"])

    def test_reliability_i18n_and_privacy_rules(self):
        source = "\n".join([
            "import requests, subprocess, datetime, logging",
            "requests.get(url)",                                   # 2 REL001
            "requests.get(url, timeout=10)",                       # fine
            "subprocess.run(['git', 'status'])",                   # 4 REL003
            "subprocess.run(['git', 'status'], timeout=30)",       # fine
            "stamp = datetime.datetime.utcnow()",                  # 6 I18N001
            "zone = datetime.datetime.now().astimezone().tzinfo",  # aware: fine
            "data = open('notes.txt').read()",                     # 8 I18N002
            "blob = open('image.png', 'rb').read()",               # binary: fine
            "total = float(order.amount)",                         # 10 I18N003
            "logging.info('login for %s with %s', user, password)",  # 11 PRIV001
            "logger.warning(f'sent to {email}')",                  # 12 PRIV002
            "try:\n    risky()\nexcept Exception:\n    pass",      # 15 REL002
            "try:\n    risky()\nexcept Exception:  # optional cache\n    pass",  # explained: fine
        ])
        self.assertEqual(self.found(source), [
            ("I18N001", 6, "i18n"), ("I18N002", 8, "i18n"), ("I18N003", 10, "i18n"), ("PRIV001", 11, "privacy"),
            ("PRIV002", 12, "privacy"), ("REL001", 2, "reliability"), ("REL002", 15, "reliability"), ("REL003", 4, "reliability")])

    def test_javascript_quality_rules(self):
        source = "fetch(url);\nfetch(url, { signal: AbortSignal.timeout(5000) });\nconst p = parseFloat(form.price);\nconsole.log('token', token);\n"
        self.assertEqual(self.found(source, "web/cart.ts"), [("I18N102", 3, "i18n"), ("PRIV101", 4, "privacy"), ("REL101", 1, "reliability")])

    def test_quality_rules_skip_tests_but_security_rules_do_not(self):
        source = f"import requests\nrequests.get(url)\nKEY = '{AWS}'\n"
        self.assertEqual([r for r, _, _ in self.found(source, "tests/test_api.py")], ["SEC001"])
        self.assertEqual(sorted(r for r, _, _ in self.found(source, "app/api.py")), ["REL001", "SEC001"])


class InfrastructureRuleTests(unittest.TestCase):
    def found(self, path, text):
        return sorted((f["rule"], f["line"], f["category"]) for f in scan.scan_files({path: text})["findings"])

    def test_dockerfile(self):
        bad = "\n".join(["FROM node AS build", "RUN curl -fsSL https://get.example.sh \\", "    | bash",
                         "ENV API_KEY=abc123 MODE=prod", "ARG GITHUB_TOKEN", "ADD https://example.com/x.tgz /x",
                         "FROM build", "FROM python:latest"])
        self.assertEqual(self.found("Dockerfile", bad), [
            ("INF001", 1, "infrastructure"), ("INF001", 8, "infrastructure"), ("INF002", 8, "infrastructure"),
            ("INF003", 4, "security"), ("INF004", 2, "security"), ("INF005", 6, "infrastructure")])
        good = "FROM python:3.13-slim AS base\nFROM base\nENV TOKEN_FILE=$SECRETS/token\nUSER 10001\n"
        self.assertEqual(self.found("docker/app.dockerfile", good), [])
        self.assertEqual(self.found("Dockerfile", "FROM ghcr.io/org/img@sha256:" + "a" * 64 + "\nUSER app\n"), [])

    def test_workflow(self):
        bad = "\n".join(["on: pull_request_target", "jobs:", "  build:", "    permissions: write-all", "    steps:",
                         "      - uses: actions/checkout@v4", "        with:", "          ref: ${{ github.event.pull_request.head.sha }}",
                         "      - uses: ./local-action", "      - uses: org/tool@" + "b" * 40])
        self.assertEqual(self.found(".github/workflows/ci.yml", bad), [
            ("INF006", 6, "infrastructure"), ("INF007", 4, "security"), ("INF008", 8, "security"), ("INF010", 1, "infrastructure")])
        good = "on: push\npermissions:\n  contents: read\njobs: {}\n"
        self.assertEqual(self.found(".github/workflows/ok.yaml", good), [])

    def test_compose(self):
        bad = "services:\n  app:\n    privileged: true\n    network_mode: host\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n" \
              "    environment:\n      DB_PASSWORD: hunter22\n      API_TOKEN: ${API_TOKEN}\n"
        self.assertEqual([r for r, _, _ in self.found("docker-compose.yml", bad)], ["INF003", "INF009", "INF009", "INF009"])

    def test_infrastructure_findings_reach_the_quality_gate(self):
        from dikdik import engineering
        report = engineering.quality({"Dockerfile": "FROM node\nUSER app\n"}, {})
        self.assertEqual([f["rule"] for f in report["findings"]], ["INF001"])


class DependencyTests(unittest.TestCase):
    LOCKS = {
        "Cargo.lock": '[[package]]\nname = "pyo3"\nversion = "0.23.5"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
                      '[[package]]\nname = "local"\nversion = "0.1.0"\n',
        "web/package-lock.json": json.dumps({"packages": {"": {}, "node_modules/lodash": {"version": "4.17.20"}}}),
        "bun.lock": '{"lockfileVersion": 1, "packages": {"typescript": ["typescript@6.0.3", "", {}, "sha"],},}',
        "requirements.txt": "requests==2.19.0\nflask>=2\n",
    }

    def test_lockfiles_are_parsed(self):
        found = {(p["ecosystem"], p["name"], p["version"]) for p in scan.lockfile_packages(self.LOCKS)}
        self.assertEqual(found, {("crates.io", "pyo3", "0.23.5"), ("npm", "lodash", "4.17.20"),
                                 ("npm", "typescript", "6.0.3"), ("PyPI", "requests", "2.19.0")})

    def test_osv_results_become_findings_with_conservative_severity(self):
        calls = []
        def http(method, url, body=None):
            calls.append((method, url))
            if url.endswith("/querybatch"):
                return {"results": [{"vulns": [{"id": "GHSA-1"}]} if q["package"]["name"] == "pyo3" else {}
                                    for q in body["queries"]]}
            return {"id": "GHSA-1", "summary": "Out-of-bounds read", "database_specific": {"severity": "MODERATE"}} \
                if url.endswith("GHSA-1") else {}
        report = scan.scan_dependencies(self.LOCKS, http)
        self.assertEqual(report["packages"], 4)
        self.assertEqual([(f["rule"], f["severity"], f["evidence"]) for f in report["findings"]],
                         [("DEP001", "medium", "crates.io:pyo3@0.23.5")])
        self.assertEqual(scan._advisory_severity({}), "high")  # unknown is not assumed harmless
        self.assertEqual(scan._advisory_severity({"database_specific": {"informational": "unmaintained"}}), "low")


class ProjectTests(unittest.TestCase):
    def test_cli_exit_code_follows_the_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "settings.py").write_text(f'KEY = "{AWS}"\n', encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text("eval(a)\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            report = scan.scan(root)
            self.assertEqual(rules(report), ["SEC001"])  # ignored folders are not scanned
            self.assertEqual(report["summary"]["critical"], 1)
            self.assertEqual(scan.main([str(root)]), 1)
            (root / "settings.py").write_text("import os\nKEY = os.environ['KEY']\n", encoding="utf-8")
            self.assertEqual(scan.main([str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
