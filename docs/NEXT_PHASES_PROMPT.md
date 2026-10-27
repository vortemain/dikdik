# Brief for the coding agent: finish Dikdik's remaining features

> **Superseded (2026-10-27):** phases A–C are done. Start from
> [`CODEX_HANDOFF.md`](CODEX_HANDOFF.md), which has the current state, baseline and remaining work.

You are continuing work on **Dikdik**, an original, local-first AI coding agent
(Python backend, Bun/TypeScript web UI, Rust components). Your job is to build every
remaining feature that OpenHands offers **except OpenHands Cloud**, to the same
standard as the existing code: working end to end, tested, documented, and honest
about what was and was not verified. The owner will later build a hosted
**Dikdik Cloud**; do not build a hosted service, but keep the seams it will need
(see "Dikdik Cloud seams").

Read this whole brief before writing code. Work in the phases below, in order.

---

## 1. Where things stand

- **Repository:** `C:\Projects\Dikdik` (Windows 11, Git Bash and PowerShell).
- **Run it:** `python -m dikdik.server --workspace examples/repair --data "$HOME/.dikdik-demo"`
  (opens the browser, signs in automatically via a `#token=` link and an HttpOnly cookie).
- **Status docs:** `README.md`, `PROJECT_STATUS.md`, `SECURITY.md`, `docs/PARITY.md`,
  `docs/ARCHITECTURE.md`, `docs/BRAND.md`.
- **Source of truth for remaining work:** `docs/parity-audit.csv` — one row per
  OpenHands sub-feature (1,749 rows) with `map, family, id, status, note, desc`.
  Status: `Y` has it, `P` partly (note says what is missing), `N` missing,
  `E` needs an outside service, `X` not applicable by design.
  Totals on 2026-10-27: Y 230, P 165, N 1,273, E 61, X 20 (see `docs/PARITY.md`
  for the current table).
- **The reference (read-only):** `upstream/OpenHands` (web app) and
  `upstream/software-agent-sdk` (agent server/SDK). Each sub-feature ID is described
  in `upstream/OpenHands/.agents/skills/verify-openhands/references/feature-map/F*.md`
  and `upstream/software-agent-sdk/.agents/skills/verify-agent-server/references/feature-map/F*.md`.
  Use these to understand behaviour. **Never copy OpenHands source code into
  Dikdik**: Dikdik is an original implementation (see `LICENSE-STATUS.md`).

### What already exists (do not rebuild)

| Area | Files |
|---|---|
| HTTP server, auth, SSE stream, static files | `dikdik/server.py`, `dikdik/server_routes.py` |
| Task engine: conversations, follow-ups, queue, apply, files | `dikdik/engine.py`, `dikdik/store.py` (SQLite, in-place migrations) |
| Agent loop and tools (`list_files`, `read_file`, `search_text`, `edit_file`, `create_file`, `run_tests`, `run_command`, `finish`, MCP tools) | `dikdik/agent.py`, `dikdik/agent_provider.py` |
| Model calls, streaming, profiles, encrypted keys | `dikdik/providers.py`, `dikdik/model_config.py`, `dikdik/profiles.py`, `dikdik/secrets_store.py` |
| Docker sandbox and test runners (Python/Bun/npm/Cargo) | `dikdik/sandbox.py`, `dikdik/runners.py` |
| Git (status/history/commit, hooks off) | `dikdik/gitops.py` |
| Skills, MCP (HTTP + stdio), agent profiles, automations | `dikdik/skills.py`, `dikdik/mcp.py`, `dikdik/agent_profiles.py`, `dikdik/automation.py` |
| Python client and CLI | `dikdik/client.py` |
| Repository inventory (+ Rust indexer, auto-loaded) | `dikdik/repository.py`, `crates/dikdik-index`, `scripts/build_native.py` |
| Web UI | `dikdik/web/index.html`, `style.css`, `app.ts` (UI logic), `contracts.ts` (types, parsers, event→chat mapping, settings panels); `app.js` is the Bun build output |
| Tests | `tests/*.py` (unittest), `tests/web/*.test.ts` (bun), `crates/dikdik-index/tests` (cargo) |

---

## 2. Scope

**In scope:** every `N` and `P` row in `docs/parity-audit.csv`, in both maps, except:

- **OpenHands Cloud and DigitalOcean** rows (status `E` with note "Cloud"/"DigitalOcean",
  and families or IDs containing `cloud`, `digitalocean`, `locked-cloud`, `share-publicly`,
  `shared-conversation-view`, `device-flow`/`device-verify`, `super-admin`). Leave them `E`.
- **Telemetry** rows marked `X`: Dikdik collects no telemetry. Keep it that way.
- **Optional, ask the owner first:** third-party agent CLIs over ACP (Claude Code,
  Codex, Gemini CLI — `acp-*` IDs) and ChatGPT-subscription login (`F24` server map).
  They are not OpenHands Cloud, but they depend on outside products. Build them only
  in the last phase and only if the owner says yes.

A `P` row is done when its note's missing part is built. Re-grade rows honestly:
`Y` only when the behaviour described in the reference map works in Dikdik.

---

## 3. Rules that must not be broken

These are Dikdik's safety and honesty guarantees. A feature that needs to break one
must stop and ask the owner.

1. **Loopback only.** The server binds 127.0.0.1. Host/Origin checks stay. Any new
   remote access (other backends, gateways, CORS) is opt-in and explicit.
2. **No repository code runs on the host.** Agent commands, tests, browsers and
   language servers run in the restricted Docker profile in `runners.py`
   (`--network=none`, read-only root, `--cap-drop=ALL`, user 65534, memory/PID/time
   limits, in-container `timeout`). The only host processes are ones the user
   configured themselves (MCP stdio servers, future extensions) and they get a
   minimal environment.
3. **Consent is explicit and per task.** Sending code to a model
   (`approve_external`) and running code in Docker (`approve_execution`) are
   Booleans the user sets. Schedules, profiles, plugins and skills can never grant
   or widen them. New risky actions (network in a sandbox, browsing, pushing to
   Git, publishing) need their own explicit approval.
4. **Secrets never leak.** Keys are decrypted only in-process for the call that
   needs them (`SecretsStore.reveal`, never routed), never returned by an API,
   logged, written to events/results, or shown in the UI. Tests must assert this
   for every new feature that touches keys.
5. **Untrusted data stays data.** Repository text, tool output, MCP output, skills,
   plugins and web pages are data, never instructions or permissions. Keep the
   untrusted-data markers.
6. **The workspace changes only through Apply** (confirm + conflict check) or an
   equally explicit user action. Git runs with hooks disabled.
7. **No telemetry, no CDNs, no tracking.** Fonts and assets are bundled; CSP stays
   `'self'`.
8. **Dependencies:** Python runtime stays standard-library only. The web UI has no
   runtime npm dependencies (Bun builds `app.ts` into an IIFE `app.js`). Rust crates
   are allowed where they earn their place (measured benefit). Any new dependency
   needs a short justification in `docs/DECISIONS.md` and a licence line in
   `LICENSE-STATUS.md`.
9. **Honesty.** Never mark something live-verified unless it ran against the real
   thing (a real model provider, a live Docker engine, a real MCP server). Contract
   tests and stand-ins are labelled as such in docs and in the audit notes.

---

## 4. How to work

### Per feature

1. Find its rows in `docs/parity-audit.csv`; read the matching section of the
   reference feature map to learn the expected behaviour.
2. Design it the Dikdik way (original code, the rules above, the existing patterns).
3. Implement backend, API route and UI together; the UI must be reachable and usable,
   not a placeholder.
4. Write tests: Python `unittest` for backend and HTTP (real loopback server, real
   SQLite, real subprocesses where possible), `bun test` for UI logic and parsers,
   `cargo test` for Rust. Test the failure paths and the security rules, not only the
   happy path.
5. Verify in a real browser (see "Browser checks") at desktop width (1440×900) and
   phone width (390×844), light and dark.
6. Update the rows in `docs/parity-audit.csv` (status + a short note in plain words),
   and the user-facing docs.
7. Run the full verification (section 6). Everything must be green before moving on.

### Per phase

- Re-count the audit totals and update `docs/PARITY.md` (table + "built since" list),
  `PROJECT_STATUS.md` (milestone + test counts) and `README.md` ("What works"/"Not yet").
- Report to the owner: what was built, test counts, what was verified live versus
  only with stand-ins, and anything skipped or blocked.
- Commits: start by asking the owner whether to commit the current uncommitted work as
  a baseline. After that, commit once per feature with a clear message, and never push
  or open pull requests unless the owner asks.

### Conventions and known gotchas

- **Match the surrounding code**: small focused modules, short docstrings explaining
  *why*, user-facing text in plain words ("Apply to workspace", not "commit candidate").
- **Database changes** go through `Store` with in-place migrations
  (`PRAGMA table_info` then `ALTER TABLE … ADD COLUMN`), like `store.py`,
  `automation.py`. Never break existing data directories.
- **API routes**: task routes live in `server.py`; settings-style resources go in
  `server_routes.py` (add the prefix to `ROUTE_PREFIXES`). Return
  `(status, dict)`; `ValueError` → 400, `KeyError` → 404, conflicts → 409.
- **Event → chat**: new event kinds need a case in `eventToMessages`
  (`contracts.ts`) and, for compact rows, `stepLabel` in `app.ts`.
- **After editing `app.ts`/`contracts.ts`** run `bun run typecheck:web` and
  `bun run build:web`; `app.js` is the served file.
- **The DOM test double** in `tests/web/review-polling.test.ts` imports `app.ts`
  for real. If the app calls a new endpoint at sign-in, add it to the mock's known
  URLs; if it uses a new DOM API at load time, extend the double.
- **Module-level `let` used at load time** must be declared above its first use
  (an earlier bug was a temporal-dead-zone crash from `syncMode()`).
- **Windows**: test fixtures written with `write_text` get CRLF; compare bytes or
  strip. Git Bash heredocs mangle backslashes — write patch scripts or code with the
  file-writing tool, not via `cat <<EOF`. `shlex.split` must use `posix=False` on
  Windows; resolve `.cmd` launchers with `shutil.which`.
- **Docker** is not running on the owner's machine. Contract-test Docker code by
  stubbing `subprocess.Popen`/`run_container`, and add a live test guarded by an
  environment variable (like `DIKDIK_RUN_DOCKER=1` in `tests/test_sandbox.py`).
  Never report a Docker feature as live-verified without that live run.
- **Model providers**: no real key is configured. For end-to-end checks, run a local
  OpenAI-compatible stand-in server (stdlib `http.server`) that checks the API key,
  supports `tools`, and streams Server-Sent Events when `stream: true`; point a
  `local` model profile at `http://127.0.0.1:<port>/v1`.

### Browser checks

Playwright is available at `upstream/OpenHands/node_modules/playwright-core`
(import it with a `file:///C:/Projects/Dikdik/...` URL) and Chrome at
`C:/Program Files/Google/Chrome/Application/chrome.exe`. Start the server with
`--no-browser --port 8798 --data <scratch dir>`, read the token from
`<data>/web.token`, open `http://127.0.0.1:8798/#token=<token>`, and script the
real user flow. Wait for state with `waitForFunction`, not fixed sleeps (the machine
is slow while Chrome runs). Check `pageerror` is empty. Look at screenshots.

---

## 5. Phases

Order matters: each phase builds on the previous one. Families are `F##` from the
CSV (`app` = OpenHands app map, `srv` = agent server/SDK map).

### Phase A — Agent capabilities

1. **Terminal panel** (app F27 `terminal-*`, srv F13 bash): a Terminal tab in the
   right panel showing the agent's sandbox commands and live output (stream chunks
   over the existing SSE stream), history after reload, plus a bash API on the
   sandbox (`execute`, async start, events, stop, timeout, output paging).
2. **Browser tool** (app F27 `browser-*`, srv F27 `browser-*`): a headless browser in
   a Docker image (Playwright/Chromium), separate explicit approval for network access,
   screenshot results shown in chat and in a Browser tab.
3. **Planning mode and task list** (app F05 `slash-plan-*`, `plan-preview`,
   `build-plan-shortcut`, F27 `planner-*`, `tasklist`): a planner that writes
   `PLAN.md` for review, "Build" hands it to the agent; an agent `task_tracker` tool
   with a visible checklist.
4. **Slash commands and composer** (app F05): `/` menu, `/model`, `/plan`, `/code`,
   `/btw` side questions, `/goal`; drafts per conversation; attach files and images
   (size limits, image-only send); paste; context-window meter.
5. **Run control** (srv F05, F06, F10, F11): pause/resume, interrupt a tool call,
   stuck detection with recovery, max-iteration resume, fork a conversation (whole or
   from an event), navigate/branch, condense long histories (app F15 condenser
   settings), ask the agent a side question, judged goal loops.
6. **Per-action confirmation** (srv F08, app F06 `confirmation-*`, F15
   `confirmation-mode-*`): policies (always / risky only / never), accept/reject in
   the chat with keyboard shortcuts, a security analyser for risky tool calls.
7. **Hooks, subagents, custom tools** (srv F27): project/user hooks that can block
   tool calls (run in the sandbox, never on the host), built-in and project
   subagents, client-defined tools.
8. **Usage** (app F27 `usage-*`, F07 `display-cost`): token usage per task and per
   turn from provider reports; no invented costs.

### Phase B — Interface and organisation

1. **Command menu** (app F02 `command-menu-*`, F09 `entry-command-menu`): Ctrl+K
   palette for tasks, settings sections and actions (move "new task" to Ctrl+N or keep
   it as a command).
2. **Onboarding and checklist** (app F01, F02 `checklist-*`, F03 banners): first-run
   setup of a model profile and key, "say hello" step, getting-started checklist;
   composer blocked with a clear banner when no model is configured.
3. **Conversation organisation** (app F04): folders, tags, filter chips, sort,
   presets, hide older, load more, hover preview, delete all, collapsed rail.
4. **Chat polish** (app F06): copy a message, thinking/reasoning display when the
   provider sends it, grouped events, Markdown alerts and tables, clickable workspace
   paths that open the Files tab, load older history, failed-send recovery.
5. **Files panel** (app F08): real tree with expand/collapse and resize, open tabs,
   pinned tabs, rich Markdown/image preview, auto refresh, open from chat and tool
   rows, split diff view, Markdown diff preview, resizable drawer remembered per task.
6. **Settings shell and app settings** (app F09, F16): URL routes per settings
   section (deep links, unknown-path handling, phone hub/back), language
   (internationalisation with English as the source), sounds, conversation-title
   model, Git identity, save states, unsaved-change prompts.
7. **Conversation page extras** (app F07): status menu, skills/hooks/agent-tools
   modals, stop confirmation, download workspace as zip, branch from a message.

### Phase C — Models and secrets

1. **Provider connections** (app F11, srv F23): connections (provider + key) separate
   from model profiles, row menus, rotate, delete guarded when referenced, load errors.
2. **Model profiles UX** (app F10, srv F20): duplicate, rename, model picker from a
   local catalog, create prefill, API-key help, schema validation, optional *live
   validation* (explicit button; never automatic), corrupt-file handling, store busy.
3. **Model routing** (app F12, srv F22): routers that pick a model per message (e.g.
   first message to a strong model, the rest to a fast one), router profiles,
   activation; measured, not assumed, quality.
4. **Switch models mid-conversation** (srv F09, app F05 `llm-profile-picker`).
5. **Secrets** (app F14, srv F08/F18): edit, rename, scoped secrets that the
   *sandbox* can read as environment variables when the user allows it (with output
   redaction), key rotation with previous keys, empty-value rules.
6. **Agent behaviour settings** (app F15, srv F18/F21): critic/verification settings,
   agent context, per-profile behaviour, secrets scoped per agent profile.

### Phase D — Extensions and automations

1. **Skills marketplace** (app F18, srv F25): search, facets, detail view, copy
   source, install from a local folder or Git URL (pinned ref, validated front matter),
   refresh, self-heal, uninstall. Fetching from the network needs explicit approval.
2. **Plugins** (app F19, srv F26): a plugin format bundling skills, MCP servers,
   hooks and agent profiles; install from local/Git, enable/disable, trust review
   before first enable, launch parameters. Plugins never widen approvals.
3. **MCP completeness** (app F17, srv F19): catalog/library, search, edit, install
   options and arguments, OAuth for remote servers (local callback on loopback), per
   conversation secrets at rest encrypted.
4. **Extension apps** (app F20, srv F28/F29): installable app pages shown in a
   sandboxed iframe (`sandbox` attribute, separate origin path, no access to the
   session cookie), an optional app backend run in Docker and reached through a
   loopback proxy that strips credentials; enable/disable/uninstall lifecycle.
5. **Automations** (app F21–F23, F28): dashboard (search, filters, sort, overview
   tiles, card/list views), detail page with activity log and logs, templates,
   import/export, cron schedules, event triggers (e.g. Git push detected locally),
   drafts with a test run, repositories per automation, agent-assisted setup.
6. **Automation Git sync** (app F24): sync automation definitions to a Git
   repository the user chooses, with encryption for secrets and explicit approval for
   each push.

### Phase E — Platform and APIs

1. **Workspaces** (app F03, srv F17, srv F04 `create-worktree`): open more than one
   approved project, folder browser limited to user-approved roots, workspace
   registry, isolated Git worktrees per task.
2. **Backends** (app F25 non-cloud rows): connect the UI to another Dikdik server
   (URL + key), switch per tab, health status, recovery. This is the seam for
   Dikdik Cloud — see below.
3. **Server APIs** (srv F01–F07, F12–F16, F18, F31, F32): health/ready/info/OpenAPI;
   multiple API keys and rotation; CORS only for configured origins; deferred
   initialisation; a WebSocket session socket alongside SSE; events search/count/
   batch; files upload/download/archive; Git diff by commit and search; workspace
   file serving with range requests; VS Code (code-server) in Docker; runtime info;
   persistence leases, eviction, retention and config precedence; outgoing webhooks
   (opt-in, never telemetry).
4. **OpenAI-compatible gateway** (srv F30): `/v1/chat/completions` and `/v1/responses`
   backed by the agent, loopback only, keyed.
5. **Clients** (srv F33, F34): finish the Python SDK (workspace exec, settings,
   pause, fork, secrets, reconnect, custom tools) and build a TypeScript client package
   (Bun-built, typed, tested against the real server).
6. **Runtime variants** (app F26): `--version`/`--info`, flag conflicts, port-in-use
   message (no traceback), a Docker image of Dikdik itself, a library build of the
   UI, and a desktop app. Prefer **Tauri** (Rust) for the desktop shell; record the
   decision in `docs/DECISIONS.md`.

### Phase F — Optional (ask first)

- ACP agents (Claude Code, Codex, Gemini CLI) as alternative agents when installed.
- ChatGPT-subscription login.

---

## 6. Verification (run before every commit)

```powershell
python -m unittest discover -s tests
bun run typecheck:web
bun run test:web
bun run build:web
cargo test
python scripts/evaluate.py
```

Current baseline: 223 Python tests pass (1 live-Docker test skipped), 45 Bun tests,
15 Rust tests, evaluation 4/4. Counts must only go up; no test may be deleted to make
a change pass unless the behaviour it tested was intentionally removed (say so in the
commit message).

---

## 7. Dikdik Cloud seams (design for, do not build)

- Keep the **API the only interface**: the web UI, Python client and TypeScript client
  must talk to a server only through HTTP/SSE/WebSocket APIs, with a configurable base
  URL. No UI code may assume the server is on the same machine except for the default.
- Keep **auth pluggable**: the session cookie for the local browser, bearer API keys
  for clients and other backends. Add scopes to keys rather than special cases.
- Keep **data per workspace and per user** in the data directory, with no global
  singletons that would block multiple users later.
- Keep the **sandbox behind an interface** (`runners.py`) so a remote sandbox
  provider can be added without touching the agent.
- Do not add sign-up, billing, organisations, sharing links or hosted infrastructure.

---

## 8. Reporting back

At the end of each phase, give the owner:

- what was built (by audit ID family), with the new audit totals;
- test counts for Python, Bun and Rust;
- what was verified in a browser, and what was verified only against stand-ins;
- anything skipped, blocked (for example: needs Docker running, needs a real key), or
  needing a decision.

Do not claim parity with OpenHands until the audit says so row by row.
