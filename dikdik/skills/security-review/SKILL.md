---
name: security-review
description: Security checks for changes that touch input, auth, files, or the network. Adapted from affaan-m/ECC.
category: methodology
version: "1.0"
license: MIT
triggers: "input parsing, auth, file paths, shell commands, network, dependencies"
---

# Security review

For changes that touch trust boundaries, check the specific risks before
declaring done. This is not a generic scan — it targets what the change
actually does.

## Checks by area

**Input parsing**
- User-controlled input is validated before use, not after.
- Numbers are bounded (no negative where negative is meaningless, no overflow).
- Strings have a length limit; unbounded input is a DoS vector.
- Paths are confined to the project (no `..` traversal, no absolute escape).

**Authentication and authorization**
- A check that grants access verifies identity *and* permission, not one.
- Secrets are read from the environment or a secret store, not hardcoded.
- A new permission flag defaults to the most restrictive value.

**File and shell**
- Commands are not built by string-concatenating user input; use argument lists.
- `shell=True` with user input is a command injection; flag it.
- Writing to a path resolves it and confirms it stays inside the workspace.
- Deleting uses a specific path, not a glob over a broad area.

**Network**
- Outbound requests go to a fixed host, not a user-supplied URL, unless the
  request is explicitly about fetching a user URL.
- TLS is not disabled to make a request "work."
- Redirects are bounded; an open redirect through your endpoint is a finding.

**Dependencies**
- A new dependency is named, pinned, and necessary. Do not add a dependency to
  use one function you could write in five lines.
- Check the dependency's reputation before adding it; say where you looked.

## How to apply it

Run the checks that match the change. A change to a Markdown doc does not need
the shell checks; a change to a CLI argument parser does. State what you
checked and what you found, not a generic "security reviewed."

## When a check fails

Stop and fix it before finishing, or state the residual risk clearly and let
the user decide. Do not silently ship a known issue.
