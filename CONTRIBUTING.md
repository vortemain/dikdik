# Contributing to Dikdik

Dikdik is a local-first AI workspace: chat and research with sources, documents
and data, and an agent that builds software. It is a developer preview, so the
codebase favours small, reviewable changes, honest reporting and tests over
breadth.

This guide describes how work is contributed here. It follows the way the
maintainer has landed changes in this repository: focused branches, conventional
commits, a green check suite, and a plain description of what was actually
verified.

## Before you start

- Open an issue before large features, refactors or dependency changes so the
  approach can be agreed first.
- Keep a pull request focused on one change. Unrelated cleanups make review
  slower and are easier to land separately.
- Never commit credentials, tokens, private hostnames or personal data. Stored
  secrets belong in the app's data directory, not in the repository.
- Report security issues through the process in [SECURITY.md](SECURITY.md), not
  in a public issue.

## Set up

Requires **Python 3.13+** and **Bun 1.4.0** for frontend work.

```bash
git clone https://github.com/vortemain/dikdik.git
cd dikdik
python -m pip install -e .
bun install --frozen-lockfile
```

Optional tools (Git, GitHub CLI, Edge/Chrome, Tesseract, Rust) are listed in the
README. The compiled browser bundle `dikdik/web/app.js` is committed.

## Run the checks

Run the same checks CI runs before opening a pull request:

```bash
python -m unittest discover -s tests -v
bun run typecheck:web
bun run test:web
bun run build:web
git diff --exit-code -- dikdik/web/app.js
```

The Python suite is large and can take several minutes. Coverage has a floor of
80% configured in `pyproject.toml`; CI enforces it:

```bash
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
```

Security gates also run in CI: the Dikdik scanner, `pip-audit`, `bun audit`, a
CycloneDX SBOM and a Gitleaks history scan. Run the local ones when your change
touches dependencies or execution paths:

```bash
python -m dikdik.security_scan . --deps --fail-on high --exclude 'benchmarks/tasks/*'
bun audit
```

## Branches and commits

Work on a branch, not directly on `main`. Name the branch after the area and the
change, for example:

```text
docs/contributing-guide
fix/desktop-shutdown-race
feat/literature-search
```

Agent-assisted branches in this repository have used the
`claude/dikdik-<topic>-<suffix>` form; follow whichever form the maintainer asks
for when one is assigned.

Commit messages follow the Conventional Commits shape already used here:

```text
feat: add literature search across OpenAlex, PubMed and arXiv
fix(desktop): restore right-click menu and hide child console windows
test: coverage for analysis.py and docextract.py
docs: document the release checks
ci: pin supported Actions runtimes and release runner images
```

- Use an imperative subject and keep it under about 72 characters.
- Add a scope in parentheses when the change is limited to one area.
- Explain the reason and the user-visible effect in the body, wrapped at about
  72 columns. Note trade-offs, limits and anything not verified live.
- Keep `Co-Authored-By:` trailers when an agent or another person contributed.
- Do not squash a branch's history before review unless asked. Pull requests are
  merged with a merge commit.

## Pull requests

A pull request should state:

1. What changed and why, in plain language.
2. Which checks were run, with the actual result.
3. What was not verified, if anything. A stand-in or mocked check is never
   reported as a live one.
4. Any migration, configuration or dependency impact.

Include screenshots or a short recording for interface changes. Keep the
description accurate: this project records only validation actually performed.

## Code and content rules

These constraints are part of the product and are enforced in review and CI:

- **Local by default.** The server binds to loopback unless remote mode is
  explicitly configured with TLS. Do not weaken host, origin or session checks.
- **No telemetry, CDNs or runtime npm dependencies.** The app must start without
  Bun, npm or network access for its own assets. Frontend dependencies are
  bundled at build time with their notices.
- **No Docker.** Containers were removed by decision (see
  [docs/DECISIONS.md](docs/DECISIONS.md)); commands run locally with the user's
  toolchain.
- **Secrets stay out of events, logs and API responses.** Redaction and the
  secrets store are load-bearing.
- **Untrusted text stays data.** Repository contents, objectives, model output
  and web pages never change policy.
- **Honest verification labels.** Passing static checks are not passing tests; a
  missing tool is not a green suite.
- **Files are UTF-8 without BOM and LF line endings.** `dikdik/web/app.js` is
  built output; commit the rebuilt bundle with frontend changes.
- **Original code only.** Third-party code, fonts and assets keep their own
  licences and notices; see [LICENSE-STATUS.md](LICENSE-STATUS.md).

Architecture decisions are recorded as ADRs in
[docs/DECISIONS.md](docs/DECISIONS.md). Add one when a change picks a direction
that future contributors would otherwise question.

## Tests and documentation

- Add or update tests for behaviour changes. A bug fix should come with a test
  that fails before it.
- Keep user-facing documentation in step with the change: `README.md`, the
  relevant guide in `docs/`, and `CHANGELOG.md` for a release.
- Record measured results only. Benchmarks and verification notes must match the
  commands that produced them.

## Releases

The maintainer prepares releases following [docs/RELEASING.md](docs/RELEASING.md):
versions in `pyproject.toml`, `dikdik/__init__.py` and
`crates/dikdik-index/Cargo.toml` move together, release notes are added under
`docs/releases/`, `CHANGELOG.md` is updated, checks pass on `main`, and an
annotated tag is pushed. Contributors do not bump versions or publish releases
unless asked.

## License

By contributing, you agree that your original contribution is licensed under the
MIT License in [LICENSE](LICENSE). Bundled third-party material keeps its own
terms.
