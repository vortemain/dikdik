# Initial audit — 2026-10-26

## Observed starting state

`C:\Projects\Dikdik` contained **zero entries**. `git status --short --branch`
returned “not a git repository.” There was no branch, revision, working tree,
upstream code, application, dependency manifest, license, or baseline test suite.
Nothing could responsibly be retained, rewritten, benchmarked against upstream,
or described as an OpenHands fork.

Python 3.13.6 is available. Git, Node, Bun and the Docker CLI are installed.
`docker version --format '{{json .}}'` reported client 29.9.1 but could not
connect to the Docker Desktop Linux engine. No model credentials were requested
or used. No external service, dependency install, or container pull is required
for the development checks.

## First milestone

Deliver an original, single-process local workspace with:

1. An explicitly approved repository directory and bounded text inventory.
2. Objective submission; explicit offline edits or opt-in model proposals.
3. Protected source files: changes exist only in separate candidate snapshots.
4. Exact edit validation, real diffs, independent Python syntax parsing.
5. Explicit permission for a restricted Docker unittest runner; honest blocked
   status when the runtime is absent.
6. Durable tasks/events, cancellation, interruption reporting and a working UI.
7. Executed development tests and reproducible performance measurements.

Acceptance does **not** imply autonomous general-purpose engineering, full
test-and-repair, production security, or a release-ready platform. In particular,
offline edits are user-specified transformations, not AI reasoning. Syntax checks
are not tests. Container integration requires a working Linux Docker daemon.

## Decisions backed by current evidence

| Change | Evidence/problem | Benefit | Risk/complexity | Verification |
|---|---|---|---|---|
| Original Python standard-library service | Empty workspace; Python available | Runnable baseline without installs | HTTP server is local preview, not hardened public infrastructure | Real loopback HTTP tests |
| SQLite and one worker | Need durable results; no existing infrastructure | Ordered events and bounded concurrency | No distributed scheduling or exact resume | Recovery/concurrency tests |
| Separate candidate snapshots | No proven rollback or sandbox implementation | Never overwrites user source | Bounded text snapshot omits assets | Source-integrity and path tests |
| Docker-only repository execution | Host execution would violate the trust boundary | Explicit isolation and resource restrictions | Daemon unavailable; Docker host remains trusted | Command-contract tests now, real Docker checks when available |
| Exact replacements and one model proposal | No established provider or agent implementation | Small reviewable executable workflow | No new files or autonomous repair yet | Edit/provider contract tests |
| No Rust, vector DB, broker or separate API service | No measured bottleneck justifies these | Small deployment and audit surface | Limited repository scale | Measure inventory before optimization |

## Licensing and security constraints

There is no retained upstream code and no runtime third-party Python dependency.
No project distribution license was supplied; do not invent owner attribution
or imply redistribution rights have been granted. See `LICENSE-STATUS.md`.
Python, browsers, operating systems, optional providers, and Docker images
remain independently licensed. Image redistribution has not been approved or
audited here. See `SECURITY.md` for actual boundaries and residual risks.

The largest current bottlenecks are **capability and verification gaps**, not
proven CPU hotspots: unavailable sandbox runtime, untested live model integration,
single-proposal operation, limited text-only repository understanding, and no
automatic source application. Current measurements must not imply improvement
over a nonexistent baseline or superiority over another product.
