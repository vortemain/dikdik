# Verification record — 2026-10-26

## Phase A — Terminal and Bash API (latest)

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
