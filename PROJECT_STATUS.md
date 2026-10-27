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
approvals, cron and Git-commit triggers, drafts and templates, and a
deterministic, secret-free Git sync of definitions into a user-selected
workspace repository (previewed pending files, hook-free commits and a separate
per-sync push approval); a Python client/CLI; the Rust indexer loaded by default; and run
control (interrupt/exact resume, pause-at-boundary, per-action confirmation, hooks, subagents,
attachments, slash commands, side questions, condensing, judged goals, forks);
an organised interface (command menu, folders and tags, file tree, onboarding);
and model management (connections, routing, a model picker, sandbox secrets);
an OpenAI-compatible gateway (/v1/models, /v1/chat/completions,
/v1/responses) that runs one ordinary Dikdik agent task per request, always
keyed and with consent explicit per request.
The parity audit (docs/PARITY.md) moved from 89 to 422 fully matched OpenHands sub-features
of 1,749. Live model-provider and live-Docker runs are still not recorded.

Test status (2026-10-27): Python **359 passed, 2 live-runtime tests skipped** (361 run); Bun
**100 passed / 474 assertions**; Rust **15 passed**; strict TypeScript check and build passed.

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
- Added run control: Stop interrupts an active sandbox tool without retaining
  partial output and exact Resume passes its error result back to the model;
  Pause still waits for the current step. Also added stuck
  detection, a never/risky/always confirmation policy with Allow/Reject cards,
  `.dikdik/hooks.json` block rules, read-only subagents, attachments, the slash
  menu, `/btw` side questions, manual/automatic condensing, a context meter,
  judged goal loops, fork/branch and Copy. Checked in real Chrome with a local
  model stand-in; not run against a live provider or Docker.
- Added a Usage tab: provider-reported input, cache, output and total tokens per
  task and per turn, and cost only when a provider reports one. Fixed a race where
  a Stop arriving as a turn finished could turn a later cancel into a pause, and
  unexpected task errors are now printed to the server console as the error
  message promised.
- Added an image lightbox, Ctrl/Cmd+Enter and Shift+Ctrl/Cmd+Backspace for
  pending approvals, and Branch on individual messages (a reply is kept, your own
  message is left out so it can be rewritten).
- Closed interface gaps: workspace images in Files, Diff/Split/New views in
  Changes (Markdown rendered), pinned tabs kept across reloads, a resizable file
  tree, a collapsed sidebar rail, "+" on folders, a Running / Needs you filter, a
  status menu on the state pill, automatic conversation titles with a chosen
  model, a phone settings hub with Back, and a Git identity Save that is enabled
  only when something changed.
- Closed model gaps: classifier routers (a model of your choice labels each
  message; your table maps labels to profiles), Add models… on a connection,
  profile/connection/router limits, provider errors in plain words (rejected key,
  unknown model, rate limit, out of credit, unreachable) without echoing keys,
  and the agent's request for a secret: a card in the chat whose value is sealed,
  given to this run's sandbox commands and never sent to the model.
- Phase D, skills marketplace: install skills from a folder, a Git source
  (https://, file://, github:owner/repo) pinned to a commit, or a marketplace you
  add; refresh, uninstall, hand-copied skills listed; a Skills page with search,
  filters, cards, a detail view and Use skill. Git runs with hooks, prompts and
  other protocols off, only files are copied, and internet fetches need approval.
- Phase D, plugins: a plugin.json manifest bundling skills, block rules,
  subagents, MCP servers, a starting prompt and parameters. Install from a
  folder, Git or a marketplace's plugins/ folder; project plugins in
  .dikdik/plugins. A review screen lists everything a plugin adds; the first
  enable needs an explicit trust tick and adds its MCP servers switched off.
  Enable/disable, update, uninstall (removes its MCP servers), file viewer and
  Start conversation with parameters. Checked in a real browser against a
  local plugin; no internet source was fetched.
- Added the interface phase: Ctrl+K command menu, Get started card, a banner and
  blocked Send without a model, folders/tags/sort/age filter, grouped steps,
  Thinking rows, Markdown tables and alerts, clickable workspace paths, a file
  tree with tabs and preview, a resizable panel, settings links, Git identity,
  a finish chime, an agent-info dialog, .zip download and a confirming Stop.
- Added the models phase: provider connections, profile rename/duplicate/test and
  request settings, rule-based routers, a pill model picker, key undo/rename,
  opt-in redacted sandbox variables, and per-profile critic, memory and secret
  scope. Fixed secret counters so a keystream is never reused after a delete or
  an undone rotation.
- Independent review findings fixed: early-exit verification bypass, finalization
  cancellation race, component-token undercount; reviewer reran regressions.
- Added automations cron/Git-commit events, drafts with validation and test
  dispatch, templates and import/export; then Automation Git sync: one managed
  directory in a chosen workspace Git repository, deterministic sorted exports
  that cannot contain secrets by construction, a preview of pending
  changed/deleted files, hook-free commits with a fixed identity, distinct
  branch checkout and safe-path validation, and a push that happens only with a
  separate never-remembered approval tick. Contract-tested with real Git repos,
  including a bare remote that stays empty until an approved push.
- Built wheel offline, inspected its 22 entries (no upstream/Python runtime deps),
  installed in a fresh venv and started the app from the installed package.
- Established the verified baseline and per-feature local commits on `main`.
  Reference checkouts and dependencies are ignored and excluded from packaging;
  no push has been performed.

## Test status

- Python: **359 passed, 2 live-runtime tests skipped** (361 run).
- Bun: **100 passed / 474 assertions**; strict TypeScript check and browser build passed.
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
