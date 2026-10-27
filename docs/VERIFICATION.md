# Verification record — updated 2026-10-27

## Phase A — Composer resize (latest)

- Full checks: **269 Python passes / 2 explicit live-runtime skips** (271 total),
  **62 Bun tests / 338 assertions**, **15 Rust tests**, strict TypeScript check
  and build, deterministic syntax-only evaluation **4/4**.
- Real Chrome pointer checks grew an empty field by 200 px, reduced it by 150 px,
  exercised keyboard Up/Home, automatic multiline growth, clear-to-shrink,
  retained manual size while typing and reset after reload. Shrinking to a
  390×500 viewport kept the composer below navigation and within its height bound.
- Four inspected screenshots cover **1440×900** and **390×844**, light and dark.
  The grip has no child elements, background or pointer-focus outline, uses the
  resize cursor and fits without page overflow. Keyboard focus remains visible.
  Page and console errors were empty. No task, model call, Docker run or stand-in
  was needed; text persistence still used the real authenticated draft API.
- Early screenshots exposed pointer focus leaving a line on the grip; pointer
  dragging now blurs it. The final run starts with an empty scratch draft and
  waits for theme transitions to settle before capture.

## Phase A — Composer drafts

- Full checks: **269 Python passes / 2 explicit live-runtime skips** (271 total),
  **60 Bun tests / 328 assertions**, **15 Rust tests**, strict TypeScript check
  and build, deterministic syntax-only evaluation **4/4**.
- Real Chrome against the actual HTTP server and SQLite checked Home reload,
  separate conversation drafts, immediate navigation flush, reload, suggestion
  persistence, whitespace/empty Send gating, Shift+Enter, Enter submission and
  acknowledged clearing. Missing approval preserved the rejected message. The
  two real offline syntax-only candidates left workspace source unchanged;
  no model, Docker or stand-in was involved in this feature's browser flow.
- Two tabs exercised 409 conflicts without overwriting the saved text, the remote
  preview, explicit Keep my draft and Use saved draft, and reload after resolution.
  Draft text did not appear in task events. Four inspected screenshots cover
  **1440×900** and **390×844**, light and dark, without horizontal page overflow.
  Page errors and unexpected console errors were empty; two deliberate conflict
  fetches produced the expected 409 console entries.
- Backend tests cover authentication, bounded input, missing task scope, real
  concurrent SQLite writes, revisioned clears and deletion cleanup. Frontend
  tests cover delayed navigation, concurrent clear, send acknowledgment matching,
  failed saves/retry, two initial-read flushes sharing one save and explicit
  conflict choices. No existing test was removed.
- Early QA runs used an incorrect startup selector or left the Changes drawer
  over the phone composer. The final capture closes that drawer and waits for
  theme transitions before taking the actual composer screenshots.

## Phase A — Planning and task list

- Final full checks: **263 Python passes / 2 explicit live-runtime skips** (265
  total), **54 Bun tests / 302 assertions**, **15 Rust tests**, strict TypeScript
  check and build, deterministic syntax-only evaluation **4/4**.
- Real Chrome exercised a separate planner and code conversation through the real
  HTTP/SSE/provider adapter and a local HTTP model stand-in that checked its fixture
  API key and emitted streamed tool calls. It wrote private PLAN.md, rebuilt plan
  and checklist state after reload, switched with bare and payload `/plan`/`/code`,
  and handed a reviewed revision to the code agent with focused Ctrl+Enter.
- The code turn produced the actual addition-repair candidate with syntax-only
  verification. Source files remained byte-identical; `tests_verified` stayed false.
  No live provider, paid model or Docker execution was used.
- Planner and Task List each passed **1440×900** and **390×844**, in light and dark:
  eight inspected screenshots, no page/console errors and no horizontal page
  overflow. Raw HTML and a malformed Markdown URL remained inert text. Safe links
  rendered with the external-opener policy.
- Python contracts cover read-only tools, disclosure/execution rejection, scoped
  artifacts, cancellation, identity/revision replay, duplicate Build, reported
  progress validation, restart durability and deletion cleanup. The existing
  event-order and active-task deletion assertions were preserved. Frontend
  regression coverage includes a stale completed poll arriving after a new turn.
- Early QA attempts encountered the old-completion-label race and a 30-second
  fixture timeout. The final run used new message echoes/current-turn completion
  and bounded 120-second state waits, then passed against the final backend.

## Phase A — Browser tool and panel

- Full checks: **254 Python passes / 2 explicit live-runtime skips** (256 total),
  **51 Bun tests / 288 assertions**, **15 Rust tests**, strict TypeScript check,
  frontend build, worker syntax check and deterministic syntax-only evaluation
  **4/4**. The runtime skips cover the live Docker security and browser tests.
- Implemented separate execution/browsing approval, Docker worker protocol,
  public-address/DNS/redirect policy, credential-free public GET relay, scoped
  PNG storage/retention, cancellation, restart cleanup and profile/Settings gates.
- Real Chrome/Playwright at **1440×900** and **390×844**, in light and dark, checked
  the actual UI/API: empty URL/page state, disabled external link, consent denial,
  real blocked-Docker response, chat and panel screenshot rendering, history after
  reload, and Settings disable/enable. All four layouts had no horizontal page
  overflow; page errors and console errors were empty. All screenshots were inspected.
- Successful screenshot rendering used an explicitly labeled synthetic capture
  stand-in. The actual Docker attempt returned `blocked`; no successful Chromium
  container capture, public-network capture or live container-isolation pass is
  claimed. No provider key or paid model was used.
- Python tests use real SQLite/loopback HTTP plus mocked Docker/protocol/DNS
  boundaries. The relay's transport fixture is a real loopback HTTP server behind
  an explicit socket-routing stand-in; it is not a public Internet security test.
- The live browser test is separately gated by `DIKDIK_RUN_BROWSER=1` and requests
  the public example page only when explicitly enabled. Setup is in `BROWSER.md`.

## Phase A — Terminal and Bash API

- Full regression checks: **234 Python tests passed, 1 live-Docker test skipped**
  (235 total); **48 Bun tests / 277 assertions passed**; **15 Rust tests passed**;
  strict TypeScript checking and the browser build passed; deterministic
  syntax-only evaluation remained **4/4**.
- Python contract/HTTP tests cover per-task approval, validation, authentication,
  synchronous and asynchronous commands, ordered 1 MiB chunks, 2 MiB output cap,
  paging, stop/idempotence, scope isolation, clear, persistence across service
  restart, and agent-command mirroring. Docker behavior is mocked in those cases.
- Lifecycle/security cases also bound pending commands to 16, refuse task deletion
  while its command is active, and atomically remove terminal history with the task.
- Real Chrome/Playwright against the local server exercised authentication, the
  actual Bash HTTP API, server-sent Terminal events, history after reload, and
  the exact read-only empty/waiting/blocked states. Desktop and 390px phone
  layouts passed in light and dark themes with no horizontal page overflow,
  page error or console error. These are viewport checks, not physical devices.
- The Docker Desktop Linux engine was unavailable. The real browser command
  therefore ended `blocked`, as designed. No successful live command, timeout,
  stop signal, filesystem boundary or container isolation claim is made.
- Full-suite verification exposed a follow-up dispatch race: a client could read
  the previous terminal result before the worker published `running`. Dispatch
  now commits the new running state before returning; the client regression passes.

## UI visual redesign (latest)

- Original light-neutral design: native Segoe UI Variable / platform sans-serif
  typography, violet accent, compact sidebar, readable messages, inset composer,
  and scoped monospace for code only. No new dependencies, remote fonts or assets.
- `bun run typecheck:web`, `bun run test:web`, `bun run build:web` passed:
  **39 tests / 220 assertions**, generated browser bundle 62.90 KB.
- Real browser on the existing temporary QA service at port 8791: authentication,
  populated sidebar, empty-state layout and Settings navigation checked. Browser
  same-origin srcdoc layout fixtures at **390×844** and **1440×900** authenticated
  successfully, with no horizontal page overflow; welcome title remained above
  the composer. These are responsive viewport fixtures, not physical-device tests.
- Small-height desktop gets a compact welcome so the heading is not clipped.
  `[hidden]` explicitly overrides layout display rules. Success notifications
  dismiss after 4.5 seconds; errors remain visible outside the connection screen.
- Terminal message presentation now passes persisted result evidence to
  `resultStatus`; a completed task with evidence is no longer mislabeled as
  stopped without a result. Sidebar values use safe DOM text nodes.
- No comparative usability study or live model/container acceptance claimed.

## Baseline

The starting workspace had zero files and was not a Git repository. No existing
application, tests, upstream revision, benchmark or distribution license existed.
Docker client 29.9.1 was installed; its Linux daemon was unavailable. Python 3.13.6
is the development runtime on Windows 11 (10.0.26300).

## Executed checks

| Check | Outcome |
|---|---|
| `python -m unittest discover -s tests -v` | 86 tests: 85 passed, 1 live-Docker test explicitly skipped; latest green run 16.995s |
| `bun run typecheck:web`, `bun run test:web`, `bun run build:web` | Passed strict TypeScript check; 8 tests / 42 assertions; browser bundle built |
| `node --check dikdik/web/app.js` | Passed |
| Scoped static parsing and package metadata checks | 21 original Python files under `dikdik`/`scripts`/`tests`, 3 JSON reports, private `UNLICENSED` package and explicit Python package-discovery boundary validated |
| `python scripts/evaluate.py --output docs/evaluation-results.json` | 4/4 deterministic outcome checks passed, syntax-only; no autonomous-model success claim |
| Browser → authenticated project → explicit edit → syntax check → review | Real candidate diff, unchanged source, completed workflow explicitly NOT TEST-VERIFIED |
| Browser → explicitly approved Docker check after fix | Real daemon connection error; task correctly blocked, candidate diff retained, `unittest: blocked`, no test-success claim |
| Browser reconnect after service restart | Persisted history restored; new token required; no console errors or horizontal overflow at 800×526 viewport |
| Independent review follow-up (three focused regression tests) | 3 passed in 0.158s; both credential/BOM findings independently confirmed fixed |
| Original-agent independent review and follow-up | 58 focused tests plus 2 additional review checks passed; early-exit/cancellation/token findings fixed; no live sandbox/model claim |
| Offline wheel build, inspection and fresh-venv install | Passed; 22 entries, no upstream packages or runtime Python requirements; installed module loaded from clean site-packages |
| Browser from fresh installed wheel | Authenticated project/history, real offline candidate diff, agent-mode Docker lock and required approvals; no external model request performed |
| `docker version --format '{{json .}}'` | Failed: Docker Desktop Linux engine pipe unavailable |

The deterministic cases check an exact bugfix candidate, denied test weakening,
detected syntax regression and denied workspace escape. They inspect resulting
files and unchanged source, not plausible-looking model text. Raw case results
are in `evaluation-results.json`. They do not run the fixture's tests on the host.

Unit/contract tests mock optional provider/Docker behavior; HTTP and workflow
integration tests use real local sockets, SQLite and filesystem operations.
Live Docker tests are separately gated, not silently replaced by mocks.

Agent orchestration tests use scripted models and instrumented verifiers. Their
`tests_verified` assertions validate a trusted-verifier contract, not real Docker
acceptance or model task-solving. The actual fixed supervisor/test program was
also run against controlled QA fixtures in subprocesses (not arbitrary project
execution): a normal test emits counts, while `os._exit(0)` during discovery emits
no receipt and is rejected. This does not establish live container isolation.

Reference-only baseline results are separate: OpenHands Canvas's initial fork-pool
test attempt had a worker-startup error and timed out. Serial `--maxWorkers=1
--pool=threads` retry passed **74 tests, 1 skipped**. One reference SDK Stop-hook
integration regression passed. Both checkouts remain clean; no reference source
or dependency has been introduced into DIKDIK's runtime/package. These are not a
fair autonomous-capability comparison or proof of superiority.

## Defects discovered during verification

1. Output-limit flag could be set after a fast process exited; passing exit status
   then won incorrectly. Fixed by checking the final drained-output flag.
2. Terminal task state and its event were separate commits. A real test observed
   completion without its completion evidence. Fixed with an atomic transaction
   and consistent read snapshot, not by weakening the assertion.
3. Unified diffs needed explicit no-final-newline markers. Added regression coverage.
4. Docker daemon errors on this Windows CLI can return exit **1**, not just 125.
   A real browser check caught the overly narrow infrastructure classifier. A
   marker emitted by the fixed container supervisor now distinguishes startup
   failure from test-process failure. Retested against the actual unavailable daemon.
5. Independent review reproduced a malformed credential leaking in a header
   validation exception. Reject malformed credentials before request construction;
   replace transport validation errors with fixed safe text. A real Engine/store
   regression confirms the dummy key is absent from both results and events.
6. Independent review found that valid UTF-8-BOM Python files failed string-based
   syntax parsing. Parse preserved source bytes; regression verifies BOM and CRLF
   round-trip exactly. The reviewer independently reran all three credential/BOM
   regressions successfully after these changes.
7. Independent original-agent review found startup+exit-zero could be misclassified
   as tests passed after production import called `os._exit(0)`. Now require a
   matching nonce-tagged completion receipt and discovered==ran>0 with all failure/
   error/skip/expected-failure/unexpected-success counters zero. Receipt forgery or
   interpreter monkeypatching by malicious tests remains outside this assurance.
8. Cancellation accepted during final diff generation could be ignored. Terminal
   commit and cancellation acceptance now share a lock; regression ends cancelled
   with test verification false, never completed after accepted cancellation.
9. Provider component-token usage was ignored without `total_tokens`. Now count
   the greater of total and prompt+completion, while explicitly disclosing that
   absent/dishonest usage prevents an exact token/cost guarantee.
10. A completed MCP OAuth browser callback could be replayed and still render a
    success page because state lookup included completed jobs. State lookup now
    accepts only active authorization jobs; the replay is 400 and the job remains
    completed.

## MCP completeness verification

- A local fake OAuth-protected MCP server covers protected-resource and authorization
  metadata discovery, dynamic client registration, authorization-code exchange with
  S256 PKCE, exact loopback callback validation, denial, timeout, replay, sealed token
  storage, refresh writeback and an authenticated tool call. It uses no internet.
- The focused MCP/plugin suite passes **29 tests**. The full Python suite passes
  **346 tests** with 2 live Docker/browser tests skipped by their explicit gates.
  The web typecheck passes and the web suite passes **100 tests**, including
  environment-mapping and search contracts.
- A real browser against the local server opened Settings → MCP servers, rendered the
  searchable 12-entry library, installed Linear as a disabled OAuth server, showed
  its sign-in-needed card, reopened a prefilled editor, switched it to local-command
  fields, and showed the library entry as Installed after refresh. No provider sign-in
  or credential was used.

## Automation suite verification

- Focused Python coverage passes **24 tests** for the original scheduler plus cron
  parsing, IANA time zones, local Git polling (one dispatch per new commit), richer
  metadata, versioned import/export, disabled imports, invalid/valid drafts, test
  dispatch, templates, enforced run time limits, scoped run details and routes.
- TypeScript type-checking and the complete web suite pass (**100 tests**); the
  production browser bundle builds successfully.
- The full Python suite passes **356 tests** with 2 live Docker/browser tests skipped
  by their explicit gates. The first managed-sandbox attempt denied loopback sockets;
  the required normal-temp unrestricted rerun completed cleanly.
- A real browser against disposable local data opened Settings → Automations, showed
  the empty dashboard and three template cards, applied the Weekday review template,
  exposed its cron/time-zone fields, created it after explicit per-run disclosure
  approval, updated the overview to `1 total · 1 enabled`, and verified the client-side
  no-match search state. No model request or scheduled run was made.

## Automation Git sync verification

- `tests/test_automation_git.py` passes 4 tests: preview counts exactly the
  deterministic export (one `automation.json` per automation plus
  `manifest.json`), the committed files contain no secret text, deleting an
  automation previews and commits the removal of its slug folder, sync refuses a
  repository outside the workspace or a sync directory holding foreign files,
  and a numeric `approve_push` (`{"approve_push": 1}`) is refused while the
  first unapproved sync leaves the bare remote untouched and a later explicit
  `approve_push: true` pushes once.
- Routes were exercised through `build_routes`: unconfigured status, save,
  pending preview and a hook-free commit all answer 200 with the documented
  shapes; commit author is a fixed `Dikdik Automations` identity and hooks are
  disabled by `core.hooksPath` plus `--no-verify`.
- A failing push after a successful commit persists the recorded manifest and
  revision, so a retry answers "already matched" and only the approved push is
  attempted again.
- Strict TypeScript check, the production browser bundle (`bun run build:web`)
  and the web suite (100 tests, 474 assertions) pass.
- A real Chrome check against disposable local data: the panel opened in
  Settings → Automations, refused a save whose branch is not checked out with
  the server's plain message, saved, previewed `2 pending · 2 changed ·
  0 deleted`, synced and then read `Everything is in sync.` with the push tick
  left unticked (and it stayed unticked afterwards); the scratch workspace then
  contained one `Sync Dikdik automations` commit authored by
  `Dikdik Automations <dikdik@local.invalid>`, and a reset working tree resynced
  to identical content without a second commit ("already matched Git"). The
  390 px dark layout shows no horizontal overflow and no page or console errors.
- Not checked against a hosted remote with stored credentials; pushes to a bare
  local remote were verified by the test suite only.

## Runtime variants verification

- `tests/test_runtime_variants.py` passes 7 tests against real subprocesses:
  `--version`/`-v` print `Dikdik <version>` and exit 0; `--info` prints runtime
  JSON (version, Python, platform, Docker presence, Rust-indexer presence);
  `--version --info`, `--version --port 1` and `--info -v` each exit nonzero
  with a one-line standalone warning; a missing `--workspace`, a workspace path
  that does not exist, a port already listening, and a data directory locked by
  another Dikdik service all stop with one plain line and no traceback.
- The port check uses a connection probe before startup because Windows
  `SO_REUSEADDR` otherwise lets a second server bind the same port.

## Packaging evidence

Built with local setuptools/wheel in the reference tooling venv, **without network
or dependencies**, using `pip wheel . --no-deps --no-build-isolation --no-index`.
Explicit package discovery includes only `dikdik`/`dikdik.*`. Inspected all 22 ZIP
entries and metadata: no `upstream`, `openhands` or `node_modules` packages and no
`Requires-Dist`. Wheel SHA256:
`8c3bb70ba73af05676e67824eabb36ca8a91ab490ae834a99ab725a9bbe867ea`.
Installed it with `--no-index --no-deps` into a new `venv --without-pip`; verified
the import path is that environment's site-packages, ran CLI help, then started
the installed app and exercised it in the browser. This is not a license grant,
public release or exhaustive cross-platform installation test.

## First measured inventory baseline

`python scripts/benchmark.py --samples 20 --output docs/benchmark-results.json`

- Intel64 Family 6 Model 142 Stepping 12; 8 logical CPUs; Python 3.13.6;
  Windows 11 build 26300. Full platform string is retained in JSON.
- Generated 500 Python text files across 20 directories, 495600 bytes total,
  plus one deliberately excluded hidden file.
- Measured inventory + SHA256 metadata with `perf_counter` in a single process.
- First scan: **13102.409ms**; median of 19 repeated scans: **137.301ms**.
- One-file change followed by a **full rescan**: **152.152ms**.
- All raw samples retained. No OS cache flush, no real cold-cache claim,
  no tail percentile at this sample count, no native/incremental optimization,
  and no comparison against another platform. Tests/evaluation were launched
  concurrently with this initial measurement; this is not a quiet-host experiment.
- First-scan discrepancy warrants profiling before choosing a language rewrite.
  No root cause or Rust speedup is inferred from these measurements.

## Not executed / not established

- Live container boundary tests, actual example unittest pass, timeout/cancellation
  under a real daemon, image provenance/digest and vulnerability review.
- Live model/provider request, autonomous feature completion or repair success.
- Comprehensive Python lint/type check, accessibility audit, multi-user deployment, independent penetration
  testing, or upstream/platform comparative benchmarks.
- CPU/RSS/token cost/end-to-end agent tail-latency measurements.

Reproduce the gated checks using `README.md`. This is a local developer-preview
milestone, not production-release evidence.
