# Dikdik project status

Updated: 2026-10-27. **Original DIKDIK core; OpenHands is a separate research reference.**
This supersedes the intermediate fork plan. No upstream source is imported into
the product; no formal clean-room or exclusive-rights guarantee is asserted.

## Current milestone

**M6: OpenHands-parity build phases.** Dikdik is now a conversational coding
agent: follow-ups and queued messages; a sandboxed shell and Python/Bun/npm/Cargo
test runners; a durable read-only Terminal and task-scoped Bash API; an approved
Docker browser tool with screenshots in chat and a Browser tab; safe apply
with conflict detection; Files and Git; model profiles and
encrypted keys driving every call; skills, MCP servers (HTTP and stdio) and agent
profiles; streamed replies over server-sent events; automations with stored
approvals; a Python client/CLI; the Rust indexer loaded by default; and run
control (pause/exact resume, per-action confirmation, hooks, subagents,
attachments, slash commands, side questions, condensing, judged goals, forks).
The parity audit (docs/PARITY.md) moved from 89 to 260 fully matched OpenHands sub-features
of 1,749. Live model-provider and live-Docker runs are still not recorded.

Test status (2026-10-27): Python **286 passed, 2 live-runtime tests skipped**; Bun
**73 passed**; Rust **15 passed**; strict TypeScript check and build passed.

## Completed

- Original Python agent/provider protocol, validated in-memory tools, bounded
  model/tool/context/repair work, cancellation and repeated-failure detection.
- Retained own authenticated service, SQLite events, locks/recovery, text inventory,
  separate candidate snapshots and real diffs; source is never overwritten.
- Every edit invalidates tests. Test runner requires exit zero plus matching
  completion receipt and nonzero executed counts; receipt is not adversarial proof.
- Own TypeScript UI built with Bun, strict type checking, functional agent-mode
  approvals and evidence rendering; no frontend runtime dependencies.
- Added a read-only Terminal whose task events append live and rebuild after
  reload, plus durable task-scoped execute/start/stop/search/get/clear Bash routes.
- Added an optional Docker browser worker with separate approval, public-resource
  relay, durable scoped screenshots, runtime discovery and a persisted disable
  switch. Successful container capture is still unverified.
- Added separate read-only planning conversations, private revisioned PLAN.md,
  explicit review/identity-checked Build, `/plan`/`/code` handoff and durable task
  checklists. Plan text and reported completion cannot grant permissions or tests.
- Added private home/conversation composer drafts, revision conflicts with explicit
  resolution, failed-send retention and clearing only after successful submission.
  Empty and whitespace-only text disables Send.
- Added an invisible composer resize grip with pointer capture, keyboard controls,
  reset on clear/navigation and height limits that reserve space for controls.
- Added run control: Pause and exact Resume from a saved transcript, stuck
  detection, a never/risky/always confirmation policy with Allow/Reject cards,
  `.dikdik/hooks.json` block rules, read-only subagents, attachments, the slash
  menu, `/btw` side questions, manual/automatic condensing, a context meter,
  judged goal loops, fork/branch and Copy. Checked in real Chrome with a local
  model stand-in; not run against a live provider or Docker.
- Independent review findings fixed: early-exit verification bypass, finalization
  cancellation race, component-token undercount; reviewer reran regressions.
- Built wheel offline, inspected its 22 entries (no upstream/Python runtime deps),
  installed in a fresh venv and started the app from the installed package.
- Established the verified baseline and per-feature local commits on `main`.
  Reference checkouts and dependencies are ignored and excluded from packaging;
  no push has been performed.

## Test status

- Python: **286 passed, 2 live-runtime tests skipped** (288 total).
- Bun: **73 passed / 378 assertions**; strict TypeScript check and browser build passed.
- Rust: **15 passed**.
- Deterministic syntax-only evaluation: **4/4**; not model task-success evidence.
- Real HTTP tests cover agent submission without user-authored edits using explicit
  scripted-model/instrumented-verifier contracts, not live sandbox/model execution.
- Fresh-installed browser app shows agent mode, forces Docker, hides edit JSON,
  and prevents submission without both approvals. Offline workflow browser check
  uses real backend events and diff. See `docs/VERIFICATION.md`.
- Real Chrome/Playwright verification exercised Terminal SSE updates, durable
  reload history, light/dark themes and 390px/desktop layouts without overflow.
  Docker was unavailable, so the displayed command truthfully ended `blocked`.
- Real Chrome checked Browser empty/blocked states, screenshot chat/panel rendering
  and reload, Settings disable/enable, and all four desktop/phone light/dark layouts.
  Screenshot success used a labeled stand-in; the actual Docker attempt was blocked.
- Real Chrome checked planning and implementation through a local streamed-model
  HTTP stand-in with a checked fixture key, real SQLite/SSE, separate conversation
  IDs, reload, bare/payload slash commands and focused Ctrl+Enter. The syntax-only
  candidate left source unchanged. Planner and Task List each passed desktop/phone
  light/dark checks with no page errors, console errors or horizontal page overflow.
- Real Chrome checked Home/conversation drafts, rejected sends, immediate navigation,
  reload, explicit two-tab conflicts and acknowledged offline submission against
  real HTTP/SQLite. Desktop/phone light/dark screenshots showed the composer without
  overflow; no page errors or unexpected console errors. No model or Docker used.
- Reference-only results: Canvas **74 passed / 1 skipped** after serial thread-pool
  retry; one SDK Stop-hook regression passed. Neither count belongs to DIKDIK.

## Blockers / limits

- Docker Linux daemon unavailable; no real container boundary/acceptance pass.
- No real provider credentials used or charged model task executed; live behavior,
  usage and repair success are unverified.
- No semantic index, arbitrary project runners, automatic apply, advanced delegation or
  public/hostile-repository release assurance.
- Owner must select copyright/license terms; private frontend is `UNLICENSED`.
  Third-party tools/reference code keep their own licenses. No publication performed.

## Performance and decisions

Historical 500-file/495600-byte inventory baseline: first 13102.409ms; repeated
median 137.301ms (20 scans); single-change full refresh 152.152ms. Not a controlled
cold-cache or comparative benchmark. Full rescan remains; no Rust speedup claimed.
Use original Python orchestration, Bun-built TS assets and SQLite without a new
proxy/service. Add native indexing only against explicit performance/correctness
contracts and measured benefit. See `docs/DECISIONS.md` and `SECURITY.md`.

## Next highest-value action

Continue Phase A with the full slash-command composer and attachments while preserving the same
task-scoped, local-first security boundary. Separately, enable a reviewed Linux
Docker runtime and explicitly configure/approve a tool-capable model to record
the still-missing live execution evidence.
