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

## Sub-feature audit (2026-10-26)

Every sub-feature in both reference maps was graded against the code: the
OpenHands app map (844 entries) and the agent-server/SDK map
(`upstream/software-agent-sdk/.agents/skills/verify-agent-server/references/feature-map`,
957 entries). The full list is in [`parity-audit.csv`](parity-audit.csv).

| Status | First audit | After the build phases |
|---|---|---|
| Has (equivalent behaviour) | 89 | 218 |
| Partly (note says what is missing) | 91 | 167 |
| Missing | 1,534 | 1,326 |
| Needs an outside service (Cloud, DigitalOcean, ChatGPT login, third-party agent CLIs) | 70 | 70 |
| Not applicable by design (telemetry, LAN binding, revealing secrets) | 17 | 20 |
| **Total** | **1,801** | **1,801** |

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

Gaps the first audit found in features that looked present, all fixed: unused
model profiles and keys, unused skills, the unloaded Rust indexer, automations
granting consent on their own, real agent tool events missing from the chat,
and automation runs never listing (`task_id`/`id` mismatch).

Largest remaining gaps: live browser execution, planning mode and task lists,
the command menu, folders and tags, per-action confirmation, model routing, plugins
and extension apps, automation Git sync and templates, WebSocket and TypeScript
clients, conversation fork/condense/goals, VS Code serving, onboarding, and any
recorded run against a live model provider or Docker engine.

## Capability groups and acceptance gates

| Ref | Original DIKDIK standing / next acceptance gate | Priority |
|---|---|---|
| F01 First-run access | Partial: local bearer-token connection. Missing provider onboarding. Fresh profile must configure a provider safely or explain why work is blocked. | P0 |
| F02 Shell/navigation | Partial: responsive single-workspace UI. Missing project/sidebar/command navigation. Keyboard-only task switching, small-screen navigation and meaningful errors must work. | P1 |
| F03 Starting work | Partial: approved startup workspace and objective submission. Missing in-product workspace selection and richer launch choices. Open an approved project and start an actual objective without manual edits. | P0 |
| F04 History/organization | Partial: durable task list. Missing folders/tags/rename/pin/archive. Persist all CRUD/default transitions without losing the selected work item. | P1 |
| F05 Conversation/composer | Partial: objective-driven conversation with message stream, composer (Enter-to-send), conversation sidebar with state badges. Missing follow-ups within a task, drafts, attachments, slash commands and planning mode. | P0/P1 |
| F06 Agent lifecycle | Partial: bounded tools/repair, cancellation and persisted events. Live model/container validation and exact resume absent. Failing test → focused repair → current pass, with no post-cancellation completion. | P0 |
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
| F17 MCP | Missing server catalog/configuration/testing/scoping. No arbitrary host launch: integration processes need explicit permissions and an actual execution boundary. | P1 |
| F18 Skills | Missing catalogs and personal/project skill enablement. Treat skill text as untrusted context, never permission overrides. | P1 |
| F19 Plugins | Missing lifecycle/deep-link/install/update support. Define extension trust and distribution/license boundaries before enabling code loading. | P2 |
| F20 Extension apps | Missing app/page embedding and lifecycle. Isolate untrusted content and scope bridge permissions; no arbitrary same-origin script privileges. | P2 |
| F21 Automation dashboard | Missing. Reference requires a separate Automation service. Original scheduling/run/list/filter/import/export operations need durable state and real execution evidence. | P2 |
| F22 Automation creation | Missing templates/custom/import setup. Configure real triggers and permissions; do not ship nonfunctional template cards. | P2 |
| F23 Automation runs/detail | Missing logs, triggers, editing/debugging. Verify idempotency, bounded retries, cancel and crash recovery. | P2 |
| F24 Automation Git sync | Missing. Require explicit network/publication/credential approvals and reproducible encrypted configuration behavior. | P2 |
| F25 Backends/Cloud/sharing | Local-only; multi-backend/sharing missing. Cloud/vendor accounts are external, not bundled OSS features. Prove backend isolation and explicit disclosure consent. | P1/P2/external |
| F26 Runtime/distribution | Partial: source startup and inspected dependency-free wheel/fresh install; Docker test runner not live-verified. Desktop, embedding and remote runtime variants missing. Verify each supported artifact and actual sandbox boundary. | P0/P2 |
| F27 Workspace tools | Partial list/read/search/create/edit, fixed multi-stack runners, durable read-only Terminal, task-scoped Bash API and Docker browser capture with a Browser tab. Missing planner/task list and detailed usage. Live Docker/browser execution remains an acceptance gate. | P0/P1 |
| F28 Automation setup/drafts | Missing schedule/event forms, draft tests and resume. Backend validation, persisted draft lifecycle and actual dispatch must precede UI completion claims. | P2 |

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
