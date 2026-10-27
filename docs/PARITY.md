# OpenHands parity ledger — original DIKDIK implementation

## Target and evidence rules

The user explicitly selected **Original DIKDIK core**. OpenHands is a functional
reference, not a code dependency, renamed fork, or hidden backend. Keep its source
and licenses separate. No formal clean-room/exclusive-rights assurance is made.

Frozen reference: Canvas **1.33.0**, `ec0f70fe31b637e699ab3713ba477abe3c3d6b6f`;
SDK checkout **1.58.2**, `ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50`. Canvas itself
pins server/client 1.58.1; their independently cloned main revisions have not been
established as a compatible deployment pair. See `UPSTREAM.md`.

The read-only reference inventory lists **28 feature families / 799 sub-features**.
That is an inventory, **not** 799 verified passes or DIKDIK acceptance tests.
The ledger below covers every family at the capability-group level; granular
sub-feature acceptance cases still need to be authored as each group is implemented.
No aggregate parity percentage or superiority score is justified.

Status: **partial** = some behavior exists, with explicit gaps; **missing** = not
implemented; **external** = requires an actual external service/account as well
as DIKDIK integration. **Contract-tested** is not live model/sandbox verification.

## Sub-feature audit (2026-10-27)

Every sub-feature in both reference maps was graded against the code: the
OpenHands app map (799 sub-features) and the agent-server/SDK map
(`upstream/software-agent-sdk/.agents/skills/verify-agent-server/references/feature-map`,
950 sub-features). The full list is in [`parity-audit.csv`](parity-audit.csv).

| Status | First audit | After the first build phases | After the parity fix | Run control | Interface | Models | Phases D–F | Current |
|---|---|---|---|---|---|---|---|---|
| Has (equivalent behaviour) | 89 | 199 | 230 | 260 | 283 | 325 | 425 | 425 |
| Partly (note says what is missing) | 91 | 154 | 165 | 180 | 204 | 212 | 261 | 279 |
| Missing | 1,491 | 1,318 | 1,273 | 1,228 | 1,181 | 1,131 | 978 | 965 |
| Needs an outside service (Cloud, DigitalOcean, ChatGPT login, third-party agent CLIs) | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 56 |
| Not applicable by design (telemetry, LAN binding, revealing secrets) | 17 | 17 | 20 | 20 | 20 | 20 | 24 | 24 |
| **Total** | **1,749** | **1,749** | **1,749** | **1,749** | **1,749** | **1,749** | **1,749** | **1,749** |

Correction (2026-10-27): earlier versions of this table counted 1,801 rows. 51 of
them were usage notes from the reference maps' "Gotchas" sections that the first
extraction mistook for features, and `F06.critic-result` was listed twice because
the reference names it twice. Those rows are removed and every column above is
recounted from the same filter; no grade changed.

Built since the first audit: conversations with follow-ups, queueing, rename/pin/
archive/delete/export and task links; real agent events in the chat and plain-text
answers; a Docker shell tool and Python/Bun/npm/Cargo test runners; syntax-only
agent mode; safe apply, Files and Git; model profiles and keys actually used;
skills in the prompt; MCP over HTTP and stdio; agent profiles; streamed replies over
server-sent events; automations with daily schedules, run now, edit and stored
approvals; a Python client and CLI; the Rust indexer loaded by default.
The first Phase A increment adds a durable read-only Terminal, live agent command
events and authenticated task-scoped Bash execution/history controls. Host/global
shell execution is deliberately excluded; the Docker path is contract-tested but
not live-verified in this environment.
The second increment adds an optional Docker browser worker, separate browsing
consent, a public GET relay, durable screenshots in chat and the Browser tab,
runtime discovery and a persisted disable switch. Real Chrome verified the UI
with a labeled screenshot stand-in; the real Docker attempt was blocked. The
screenshot/launch parity rows remain partial until real runtime evidence exists.
The third increment adds a separate read-only planner conversation, private
revisioned PLAN.md artifacts, review/identity-checked Build, bare and payload
`/plan`/`/code`, Ctrl/Cmd+Enter, safe plan previews and durable reported checklists.
The full HTTP/SSE/provider-adapter flow was checked in real Chrome using a local
streamed model stand-in; Build stayed syntax-only. Workspace `.agents_tmp/PLAN.md`
import remains partial.
The fourth increment adds private Home/conversation drafts with revision conflict
choices, failed-send retention, acknowledgment-based clearing and empty Send
gating. Real Chrome checked persistence and conflicts against real HTTP/SQLite;
offline syntax-only submissions needed no model or Docker stand-in.
The composer also has the missing invisible resize grip, with keyboard support,
viewport bounds and reset-on-clear behavior checked in real Chrome.
Run control adds immediate Stop for an active sandbox tool with a resumable error
result (contract-tested without live Docker and checked in a real browser at desktop
and phone widths against the local model stand-in), Pause and exact Resume (saved transcript, fresh budget), stuck
detection, per-action confirmation (never / risky only / every action) with
Allow/Reject cards, project block hooks, a read-only explorer subagent plus
project/personal subagents, attachments (+, paste, drop; images reach the model),
the slash menu (`/plan /code /goal /btw /model /condense /fork` and skills), side
questions, manual and automatic condensing, a context meter, judged goal loops,
fork and Branch from here, Copy on replies and a per-tab model choice. Real Chrome
checked every one of these against real HTTP/SQLite and a local model stand-in;
no live provider or Docker run was made.
The interface phase adds a Ctrl+K command menu, a Get started card and a
missing-model banner that blocks Send, folders, tags, sorting, an age filter and
archive clean-up in the sidebar, grouped steps, a Thinking row for provider
reasoning, Markdown tables/alerts/quotes, workspace paths that open in Files,
Copy on user messages, a file tree with tabs and Markdown preview, a resizable
panel, `#settings/<page>` links, Git identity and a finish chime, a Skills/hooks/
tools dialog, a conversation .zip download and a confirming Stop. Stuck detection
now follows OpenHands (four identical actions in a row, or an A/B pair three
times). Checked in real Chrome against a local model stand-in.
The models phase adds provider connections (one sealed key shared by several
profiles; rotate, rename, delete guarded while profiles use them), a full profile
editor (rename, duplicate, catalog suggestions, key-help links, temperature/top P/
token limits, an explicit Test button that sends one tiny request), rule-based
model routers with a per-turn "Routed to" record, a model picker on the composer
pill, API keys with undo of the last change, rename that updates every reference,
and opt-in sandbox variables passed to Docker by name only and redacted from
output, plus per-agent-profile critic review, project memory and secret scoping.
Secret counters now never repeat across deletes or undone rotations. Checked in
real Chrome against a local OpenAI-compatible stand-in; no live provider or
Docker run was made.
The automations phase adds stdlib cron schedules with IANA time zones, local
Git-commit polling, persisted drafts with validation and test dispatch, and
versioned import/export that imports disabled. Git sync extends it: Dikdik
manages a dedicated directory inside a Git repository the user selects from the
workspace, exports every automation deterministically (sorted keys, slug
folders, a manifest) with secrets never present by construction, previews the
pending changed/deleted files before writing, commits with Git hooks disabled,
and pushes only after a separate per-sync approval tick that is never stored.
Sync failure never leaves a half-managed directory: saving refuses directories
that exist with foreign files. Contract-tested; not yet screenshot-checked in a
real browser.

Gaps the first audit found in features that looked present, all fixed: unused
model profiles and keys, unused skills, the unloaded Rust indexer, automations
granting consent on their own, real agent tool events missing from the chat,
and automation runs never listing (`task_id`/`id` mismatch).

Since Phases D–F (2026-10-27): ACP agents run in a throwaway copy and return a
diff to apply; extension apps open in a sandboxed frame; backend checks really
contact the server. These 18 rows moved to Partly (5 of them from "needs an
outside service": the ACP rows are now built, though no real agent CLI has run).

Largest remaining gaps: live browser execution, VS Code serving, a sidebar rail
for apps and in-tab backend switching, ChatGPT sign-in, and any recorded run
against a live model provider, real ACP agent or Docker engine.

## Capability groups and acceptance gates

| Ref | Original DIKDIK standing / next acceptance gate | Priority |
|---|---|---|
| F01 First-run access | Partial: local bearer-token connection. Missing provider onboarding. Fresh profile must configure a provider safely or explain why work is blocked. | P0 |
| F02 Shell/navigation | Partial: responsive single-workspace UI. Missing project/sidebar/command navigation. Keyboard-only task switching, small-screen navigation and meaningful errors must work. | P1 |
| F03 Starting work | Partial: approved startup workspace and objective submission. Missing in-product workspace selection and richer launch choices. Open an approved project and start an actual objective without manual edits. | P0 |
| F04 History/organization | Partial: durable task list. Missing folders/tags/rename/pin/archive. Persist all CRUD/default transitions without losing the selected work item. | P1 |
| F05 Conversation/composer | Partial: objective-driven follow-ups, Enter-to-send, private scoped drafts, explicit tab conflicts, separate planning and /plan /code commands. Slash menu, attachments, side questions, judged goals, context meter, pause/resume and per-tab model choice are in. The pill model picker and the Usage tab are in too. Missing dictation. | P0/P1 |
| F06 Agent lifecycle | Partial: bounded tools/repair, cancellation and persisted events. Exact resume, stuck detection and per-action confirmation are in; live model/container validation absent. Failing test → focused repair → current pass, with no post-cancellation completion. | P0 |
| F07 Task detail/actions | Partial: actual events, checks, counters and diff. Missing export/download, conversation menus and broader controls. Export reproducible evidence, not model claims. | P1 |
| F08 Files/Git/review | Partial: new candidate files/tests and per-file added/modified diff review implemented in this increment. Missing full file viewer, Git history/branch/status integration and deletion. Review nested/empty/Unicode additions and edits without source writes. | P0 |
| F09 Settings navigation | Missing: no inert settings screens presented. Add screens only with working backend/configuration behavior and useful validation. | P0 |
| F10 Model profiles | Missing named profile CRUD/defaults; one compatible provider configured by environment. Persist profiles, validate capabilities and handle default deletion deterministically. | P0 |
| F11 Provider connections | Partial server-side credential input; missing connection/rotation UI. Key setup/rotation must never return secrets to the frontend or logs. | P0 |
| F12 Model routing | Missing. Demonstrate equivalent-or-better correctness with measured cost/latency benefit before enabling automatic routing. | P2 |
| F13 Agent profiles | Missing configurable profile CRUD/selection/scopes. Keep tool and secret permissions effective across create/resume/delegation paths. | P1 |
| F14 Secret management | Missing vault/scoped-secret CRUD; environment-only provider credential. Use reviewed secure storage and prove redaction/least privilege across persistence and tools. | P0 |
| F15 Context/verification policy | Partial context/call/token/repair limits, disclosure/execution approvals and candidate test gate. Missing compaction, memory/settings and broader policy UI. Required checks cannot be replaced by a model's verdict. | P0/P1 |
| F16 App preferences | Missing configurable themes/languages/voice/identity/preferences. Implement actual persisted settings; test contrast, localization and keyboard access. | P2 |
| F17 MCP | Built-in searchable catalog, custom add/edit/delete, option/argument install flows, sealed per-server key mappings, test/tool-call UI, enablement, and OAuth authorization-code + PKCE with a loopback callback and token refresh. Command catalog installs require explicit approval and a successful probe before saving. Still missing reference-specific Customize routes/section filter, provider-native Git tabs and per-agent server selection. | P1 |
| F18 Skills | Missing catalogs and personal/project skill enablement. Treat skill text as untrusted context, never permission overrides. | P1 |
| F19 Plugins | Partial: install from a folder, Git or a marketplace; a trust review before first enable; enable/disable, update, uninstall, files and launch parameters. Plugins add skills, block rules, subagents and switched-off MCP servers only; nothing in them runs on the host. Missing deep links and a /launch route. | P2 |
| F20 Extension apps | Missing app/page embedding and lifecycle. Isolate untrusted content and scope bridge permissions; no arbitrary same-origin script privileges. | P2 |
| F21 Automation dashboard | Partial: the settings dashboard now has durable overview counts, search, status filters, sorting, run/edit/toggle/delete and JSON import/export. Missing reference routes, grid/list persistence, pinning, pagination and cancellation. | P2 |
| F22 Automation creation | Partial: three working built-in templates, custom create/edit and disabled-on-import setup. Missing the reference template catalog/integration queues and agent-assisted setup. | P2 |
| F23 Automation runs/detail | Partial: interval/daily/cron/local-Git triggers, run-now, history and scoped task events/results. Existing durable intents retain idempotent catch-up and three-failure disable. Missing run cancellation and reference activity visualizations. | P2 |
| F24 Automation Git sync | Missing. Require explicit network/publication/credential approvals and reproducible encrypted configuration behavior. | P2 |
| F25 Backends/Cloud/sharing | Local-only; multi-backend/sharing missing. Cloud/vendor accounts are external, not bundled OSS features. Prove backend isolation and explicit disclosure consent. | P1/P2/external |
| F26 Runtime/distribution | Partial: source startup and dependency-free wheel; `--version`/`--info`, standalone-flag conflicts, clean missing-workspace and port-in-use exits without tracebacks. Docker engine not live-verified; desktop, library build, launcher variants and remote runtime variants missing. | P0/P2 |
| F27 Workspace tools | Partial list/read/search/create/edit, fixed multi-stack runners, Terminal/Bash API, Docker browser, separate read-only planning/review/Build and a reported task checklist. Workspace plan import and detailed usage remain missing. Live Docker/browser execution remains an acceptance gate. | P0/P1 |
| F28 Automation setup/drafts | Partial: schedule/event forms, repositories, time limit, persisted drafts and actual draft test dispatch. Invalid drafts are accepted by the API, but the compact settings UI currently saves only locally valid drafts and has no resume browser. | P2 |

Reference sources: `upstream/OpenHands/src/routes.ts`,
`src/constants/settings-nav.tsx`, and the read-only `.agents/skills/verify-openhands/
references/feature-map/README.md:229–262` plus its family files. Reference Files is
a read-only viewer; its Terminal mirrors agent commands with stdin disabled.
Do not inflate the reference into an interactive editor/terminal it does not provide.
Do not reproduce its known stale-file/diff errors as requirements.

Cloud/Enterprise/DigitalOcean, external MCP servers, third-party agents and
Slack/GitHub/Linear/Notion integrations require real services and scoped approval.
They remain explicit gaps; cloning their frontend does not provision them.

## Delivery sequence

0. **M4 (this increment):** provider profiles CRUD, encrypted-at-rest secret
   refs (plaintext never returned), skill catalog with untrusted-data markers,
   in-process automation scheduler with bounded retries and restart catch-up,
   and the first justified Rust component: an incremental repository indexer
   (`crates/dikdik-index`) benchmarked against the recorded scan baseline.
   Contract: `docs/M4_CONTRACT.md`. Live-model/Docker acceptance (item 2)
   remains pending and is never relabeled as verified.

1. **M3 (this increment):** safe nested candidate-file/test creation, original-test
   protection, complete Git-applicable diff/index, functional per-file review.
2. **P0 engineering acceptance:** reviewed live Docker runtime + approved provider;
   real failing-test repair and multi-file feature creation with independent checks.
3. **P0 foundations:** project-specific approved runners, real provider/profile/
   secret setup, persistent conversational continuation/checkpoints and Git access.
4. **Repository intelligence:** original incremental indexing and justified Rust
   boundary, measured retrieval quality and latency against the recorded baseline.
5. **Advanced parity:** planning/delegation/context, MCP/skills, integrations and
   deployment variants in bounded independently verified increments.

## “Better UI” must be earned

Current improvements are concrete DIKDIK behaviors—not a claim to beat Canvas:
per-file selection, explicit added/modified status, retained focus across unchanged
polls, preserved combined-diff fallback, and separate execution/test evidence.

Acceptance targets: at 390px and desktop widths, no horizontal page overflow;
all files/checks reachable; keyboard selection and visible focus; no status conveyed
by color alone; selected-file validity after task/change-set changes; no fake
progress or inert controls. Measure time/errors to find a failing check, inspect
a changed file and identify verification status on equivalent fixtures before a
comparative usability claim. No such comparative user study has been performed.
