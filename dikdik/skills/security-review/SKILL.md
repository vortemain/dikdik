---
name: security-review
description: Secure coding while writing code, scripts and configs, and security checks before finishing. OWASP-style; review part adapted from affaan-m/ECC.
category: methodology
version: "1.1"
license: MIT
triggers: "input parsing, auth, login, password, secret, token, credential, api, endpoint, server, database, sql, query, upload, file paths, shell commands, subprocess, script, network, http, dependencies, config"
---

# Secure coding and security review

Write it secure the first time; then check the specific risks the change
touches before declaring done. Dikdik scans what you write: credentials are
refused, and critical or high findings come back to you to fix. Fix them; do
not silence the scanner.

## While writing

- **Validate input at the boundary.** Check type, length, range and format with
  an allowlist before use. Reject; do not try to repair hostile input.
- **Parameterized queries only.** Pass values as parameters (`?`, `%s`, named
  binds) or use the ORM's query builder. Never build SQL with f-strings, `%`,
  `+` or `.format`. Identifiers (table, column) come from a fixed allowlist.
- **Safe subprocess use.** Pass an argument list (`subprocess.run([...],
  timeout=...)`, `execFile`, `spawn`); no `shell=True`, `os.system` or string
  commands with user data. Never `eval`/`exec` untrusted text; parse it.
- **Secrets from the environment.** Read keys from environment variables or a
  secret store (`os.environ["API_KEY"]`), never literals in code, configs,
  docs or scripts. Example files use obvious placeholders. Never log secrets
  or personal data.
- **Least privilege.** Files `0o600`/`0o644`, directories and scripts `0o755`;
  never `777`/`666`. Containers and services run as a non-root user. Grant the
  narrowest token scope, database role and CI `permissions:` that work.
- **TLS stays on.** Never `verify=False`, `rejectUnauthorized: false`, `curl
  -k` or `CERT_NONE`; point at the right CA bundle instead.
- **Downloads are verified.** Do not pipe `curl | sh`; download, check a
  checksum or signature, then run.
- **Dependencies are pinned.** Name exact versions (lockfile, `==`, a commit
  SHA for CI actions), add only what is necessary, and prefer the standard
  library for a few lines of work.
- **Safe defaults.** New flags default to the most restrictive value, errors
  fail closed, and output to HTML is escaped (`textContent`, template escaping).

## Checks before finishing

**Input parsing**
- User-controlled input is validated before use, not after.
- Numbers are bounded; strings have a length limit (unbounded input is a DoS vector).
- Paths are confined to the project (no `..` traversal, no absolute escape).

**Authentication and authorization**
- A check that grants access verifies identity *and* permission, not one.
- Passwords are hashed with a slow, salted KDF (bcrypt, scrypt, argon2, PBKDF2).
- Sessions and tokens expire; comparisons of secrets are constant-time.

**File and shell**
- Writing to a path resolves it and confirms it stays inside the workspace.
- Deleting uses a specific path, not a glob over a broad area.

**Network**
- Outbound requests go to a fixed host, not a user-supplied URL, unless the
  request is explicitly about fetching a user URL. Requests have timeouts.
- Redirects are bounded; an open redirect through your endpoint is a finding.

## How to apply it

Apply what matches the change: a Markdown doc does not need the shell checks;
a CLI argument parser does. State what you checked and what you found, not a
generic "security reviewed."

## When a check fails

Fix it before finishing, or state the residual risk clearly and let the user
decide. Do not silently ship a known issue, and report any finding you could
not resolve in the final answer.
