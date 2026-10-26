# Architecture decisions

## ADR-001: One Python process, SQLite, static browser client

Accepted for the first local vertical slice. Python 3.13 is installed and no
application exists to integrate with. Alternatives: Python/Rust bindings,
subprocess protocol, separate native service; all add packaging and contracts
without a measured native-code workload. Reconsider after profiling retrieval
and indexing on representative repositories. The first prototype deferred TS;
the current original client uses strict TypeScript and Bun to build static assets
without introducing a separate server or frontend runtime dependency.

SQLite owns task metadata and ordered events. Snapshots are separate filesystem
artifacts; a DB transaction cannot undo them. One active task and an exclusive
data-directory lock prevent duplicate execution in this installation. Startup
marks abandoned queued/running tasks interrupted; it does not silently replay
commands or promise exact resume.

## ADR-002: Candidate-only changes, restricted fixed test command

Do not write into the approved source workspace. Produce a bounded text snapshot
and diff in private data storage. No “apply” endpoint exists. This is simpler and
safer than pretending filesystem writes have transactional rollback. Existing
tests cannot be edited in this milestone. No repository command is run on the
host. Docker runs a fixed Python unittest command after explicit approval with
network disabled, no capabilities, a read-only candidate mount, a non-root user,
memory/CPU/PID/output/time limits, and cleanup by container name.

Alternatives: arbitrary host shell (rejected), venv as isolation (rejected),
microVMs (stronger boundary but currently unavailable). Containers are not a
guarantee against kernel/daemon vulnerabilities or hostile local users.

## ADR-003: Explicit offline mode, bounded opt-in provider

Offline mode applies user-authored exact replacements. It must not be called
autonomous reasoning. Model mode uses one HTTPS chat-completions-compatible
request, a 128KB input context cap, 4096 requested output tokens, a 30-second
network timeout and no automatic retries or redirects. External disclosure
requires task-specific approval. Provider proposals pass the same deterministic
validation as offline edits; provider claims do not establish verification.

Streaming, additional provider capabilities, multi-agent delegation,
context routing and immediate model cancellation remain deferred. The separate
original tool-using agent mode adds bounded repair; this one-proposal mode does not.
A cancellation during the
blocking provider call takes effect after that call returns or times out.

## ADR-004: Original core, reference-only OpenHands

The user's latest IP requirement supersedes the intermediate plan to ship an
OpenHands-derived runtime. Keep the two cloned checkouts and their notices for
research/baseline testing, but import/copy neither into DIKDIK. Retain useful original
prototype components. Implement an original model/tool protocol and orchestration
loop, rather than a renamed upstream class or a wrapper around its runtime.

Root Git ignores `upstream/` and `node_modules/`; Python package discovery allows
only `dikdik` and `dikdik.*`. Wheel inspection and a fresh dependency-free install
verify that the research SDK is not a hidden runtime requirement. The frontend
package is private and `UNLICENSED`, not implicitly licensed under TypeScript's
Apache-2.0 terms. No formal clean-room, exclusivity or legal-ownership guarantee is
made. Actual distribution/copyright terms require the owner's decision.

## ADR-005: Candidate-specific completion and trustworthy failure reporting

Own agent tools operate on bounded in-memory candidate files and invoke only the
existing restricted Docker runner. Net changes and a current passing unittest
check are required for finish; edits invalidate evidence. Budgets, distinct
approval gates and repeated-failure detection constrain the original loop.

Independent review reproduced a false-positive path: production import could call
`os._exit(0)` before tests completed. Require a matching nonce-tagged completed-run
receipt and nonzero executed counts in addition to process exit. This improves
ordinary verification reliability, **not** adversarial attestation: code inside
the test interpreter can deliberately subvert that harness. Keep this limitation
explicit; do not use the receipt to market a hostile-code integrity guarantee.

Serialize cancellation acceptance and terminal state commit so an accepted
cancellation during artifact/diff finalization cannot become verified completion.
Count available provider component usage, but do not call reported usage a strict
cost guarantee when metadata is missing or dishonest.

## ADR-006: Isolated browser with a public-resource relay

The optional browser uses Playwright **1.64.1** and its matching Chromium image
only inside an operator-built `dikdik-browser:1` container. Browser rendering
requires an actual browser engine; a Python HTML renderer would not provide
equivalent screenshots. This adds no Python or frontend runtime package and
imports no code from the OpenHands reference. Playwright is Apache-2.0; Chromium
and the base image include separately licensed components (see LICENSE-STATUS).

Keep the existing no-network Docker boundary and relay public GET resources over
stdin/stdout. The standard-library relay validates every target/redirect, rejects
private DNS answers, pins the socket address, and forwards no ambient credentials.
This avoids granting the browser direct access to the host/LAN. Separate task
execution and browsing approvals are required. Restrict the first browser tool
to URL capture and bounded text/screenshot results; interactive login, arbitrary
methods and local targets require their own future policy and acceptance work.

Playwright's [Docker guidance](https://playwright.dev/docs/docker) documents its
version matching and notes the additional sandbox requirements for untrusted
sites. Our fixed Docker profile supplies the outer boundary; Chromium's own
sandbox is disabled, and live browser/container security remains a release gate.
[Request interception](https://playwright.dev/docs/network) requires service
workers to be disabled; the worker also blocks WebSockets. Direct traffic stays
blocked by Docker even if a browser API bypasses interception. No image was built
or downloaded during this increment because the Docker daemon was unavailable.
