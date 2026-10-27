# Handoff: finish Dikdik's remaining OpenHands-parity work

**For:** the next coding agent (Codex). **From:** the agent that built phases A–C.
**Date:** 2026-10-27. **Repository:** `C:\Projects\Dikdik` (Windows 11; Git Bash and PowerShell).

Read this whole document before writing code. It is self-contained: it says what
exists, what is left, the rules you must keep, how this codebase is put together,
how to test and verify, and how to report. `docs/NEXT_PHASES_PROMPT.md` is the
original brief; where the two differ, **this document wins** (it is newer).

---

## 0. Your mission in one paragraph

Dikdik is an original, local-first AI coding agent (Python standard-library
backend, Bun/TypeScript web UI, a Rust indexer). The goal is every feature that
OpenHands offers **except OpenHands Cloud**, built to the existing standard:
working end to end, reachable in the UI, tested (including failure and security
paths), checked in a real browser, graded honestly in the parity audit, and
committed one feature at a time. Phases A, B and C are done. You will (1) close
the listed gaps left in A–C, (2) build Phase D (extensions and automations),
(3) build Phase E (platform and APIs), and (4) only if the owner says yes,
Phase F (ACP agents, ChatGPT login). Never push and never open pull requests
unless the owner asks.

---

## 1. State of the repository

### Commits so far (newest first)

| Commit | What it added |
|---|---|
| `ad15ac1` | Phase C — provider connections, profile rename/duplicate/test/settings, rule-based routers, pill model picker, key undo/rename, redacted sandbox secrets, agent-profile critic/memory/secret scope, secret-counter fix |
| `fab216f` | Phase B — Ctrl+K command menu, onboarding card and missing-model banner, folders/tags/sort/age filter, grouped steps, Thinking rows, Markdown tables/alerts, clickable paths, file tree/tabs/preview, resizable panel, `#settings/<page>` routes, Git identity, finish chime, agent-info dialog, .zip download, confirming Stop, OpenHands-style stuck detection |
| `b39ddee` | Phase A run control — pause/exact resume, per-action confirmation, hooks, subagents, attachments, slash menu (`/plan /code /goal /btw /model /condense /fork`), side questions, condensing, context meter, judged goals, fork/branch, per-turn usage |
| earlier | Terminal, Docker browser, planning/PLAN.md, drafts, composer grip, conversations, apply/Git, MCP, skills, automations, Python client, Rust indexer |

Check for uncommitted work first: `git status --short`. At handoff time the only
uncommitted files should be this document and `scripts/stub_model.py`; ask the
owner whether to commit them as a baseline before you start.

### Test baseline (must only go up)

| Suite | Command | Baseline |
|---|---|---|
| Python | `python -m unittest discover -s tests` | 307 run, 305 pass, 2 skipped (live Docker tests: `DIKDIK_RUN_BROWSER=1`, `DIKDIK_RUN_DOCKER=1`) |
| Web type check | `bun run typecheck:web` | clean |
| Web tests | `bun run test:web` | 87 pass, 428 assertions |
| Web build | `bun run build:web` | writes `dikdik/web/app.js` (served file; commit it) |
| Rust | `cargo test --manifest-path crates/dikdik-index/Cargo.toml` | 15 pass |
| Evaluation | `python scripts/evaluate.py` | 4/4 (syntax-only; not model-success evidence) |

The full Python suite takes about 2–5 minutes; run it in the background while you
do browser checks.

### Parity audit (source of truth for remaining work)

`docs/parity-audit.csv` — 1,749 rows, columns `map,family,id,status,note,desc`.
Status: `Y` has it, `P` partly (note says what is missing), `N` missing, `E` needs
an outside service, `X` not applicable by design.

**Totals now:** Y 325, P 212, N 1,131, E 61, X 20. `docs/PARITY.md` has the table
with one column per phase; add a column for each phase you finish.

Each row's behaviour is described in the read-only reference maps:
`upstream/OpenHands/.agents/skills/verify-openhands/references/feature-map/F*.md`
(app map) and
`upstream/software-agent-sdk/.agents/skills/verify-agent-server/references/feature-map/F*.md`
(server/SDK map). Read them to learn expected behaviour. **Never copy OpenHands
source into Dikdik** (see `LICENSE-STATUS.md`).

To list open rows for a family (prints UTF-8 safely on Windows):

```bash
PYTHONIOENCODING=utf-8 python -c "import csv; [print(r['id'], r['status'], '|', r['note'][:60], '|', r['desc'][:120]) for r in csv.DictReader(open('docs/parity-audit.csv', encoding='utf-8')) if r['family'].startswith('F19') and r['status'] in ('N','P')]"
```

To update rows, use a small script (keep `lineterminator="\n"`, UTF-8, no BOM;
the file must stay LF):

```python
import csv, io
from pathlib import Path
UPDATES = {"F19.example-id": ("Y", "Plain-words note of what Dikdik does")}
path = Path("docs/parity-audit.csv")
rows = list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8"))))
for row in rows:
    if row["id"] in UPDATES:
        row["status"], row["note"] = UPDATES[row["id"]]
out = io.StringIO(); w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
w.writeheader(); w.writerows(rows); path.write_bytes(out.getvalue().encode("utf-8"))
```

Assert every ID you update exists. Grade honestly: `Y` only when the behaviour in
the reference map works in Dikdik; otherwise `P` with a note naming what is missing.

---

## 2. Rules that must never be broken

A feature that needs to break one of these must stop and ask the owner.

1. **Loopback only.** The server binds 127.0.0.1; Host/Origin checks stay. Remote
   access (other backends, gateways, CORS) is opt-in, explicit and keyed.
2. **No repository code runs on the host.** Agent commands, tests, browsers,
   language servers and extension backends run in the restricted Docker profile in
   `dikdik/runners.py` (`--network=none`, read-only root, `--cap-drop=ALL`,
   user 65534, memory/PID/time limits, in-container `timeout`, `--pull=never`).
   The only host processes are ones the user configured (MCP stdio servers), with
   a minimal environment.
3. **Consent is explicit and per task.** `approve_external` (send code to a model),
   `approve_execution` (run in Docker), `approve_browser`, `approve_secrets` are
   Booleans the user sets. Schedules, profiles, plugins, skills, routers and hooks
   can never grant or widen them. New risky actions (sandbox network, Git push,
   publishing, fetching from the internet) need their own explicit approval.
4. **Secrets never leak.** Keys are decrypted only in-process (`SecretsStore.reveal`,
   `sandbox_values`) for the call that needs them; never returned by an API, logged,
   written to events/results, put on a command line, or shown in the UI. Sandbox
   secrets go to Docker as `--env NAME` (value via the child process environment)
   and are redacted from output (`runners.Redactor`). Every new feature touching
   keys needs a test asserting the value never appears in responses or events.
   Secret counters only grow (`secrets_store._next_counter`) so no keystream is
   ever reused — keep it that way for any new sealing code.
5. **Untrusted data stays data.** Repository text, tool/MCP output, skills, plugins,
   memory files, web pages and plans are wrapped in untrusted markers and never
   treated as instructions or permissions.
6. **The workspace changes only through Apply** (confirm + conflict check) or an
   equally explicit user action. Git runs with hooks disabled.
7. **No telemetry, no CDNs, no tracking.** Fonts/assets are bundled; CSP stays
   `'self'` (`img-src 'self' data:`). Outgoing webhooks, if built, are opt-in and
   user-configured, never telemetry.
8. **Dependencies.** Python runtime: standard library only. Web UI: no runtime npm
   dependencies (Bun bundles `app.ts` to an IIFE). Rust crates only with measured
   benefit. Any new dependency gets a line in `docs/DECISIONS.md` and
   `LICENSE-STATUS.md`.
9. **Honesty.** Never call something live-verified unless it ran against the real
   thing (a real provider, a live Docker engine, a real MCP/OAuth server). Stand-ins
   and contract tests are labelled as such in docs, audit notes and commit messages.

---

## 3. How the code is organised

### Backend (`dikdik/`, Python 3.13, stdlib only)

| Area | Files | Notes |
|---|---|---|
| HTTP server, auth, SSE, static files | `server.py` | Task routes, `/api/attachments`, `/api/settings/app`, `/api/agent-info`, `/api/tasks/<id>/{pause,resume,confirm,ask,condense,fork,goal/*,download,stream}`. Body limit 256 KB (4.4 MB for attachments). |
| Settings-style resources | `server_routes.py` | `ROUTE_PREFIXES` + `TABLE`. Handlers return `(status, dict)`. `ApiError`→its status, `ValueError`→400, `KeyError`→404, `SecretsError`→500 with its (secret-free) message, `sqlite3.OperationalError`→503 "store is busy". Prefixes: profiles, secrets, skills, automations, mcp, agent-profiles, drafts, connections, routers, model-catalog. |
| Engine | `engine.py` | Task lifecycle, `_validate` (the public request contract — add new request keys there), `_run` → `_agent_workflow` / `_planning_workflow` / model proposal, `_model_for` (explicit pick → agent profile → active router → default), `_history` (uses the latest `condensed` summary), `fork`, `resume`, `confirm`, `app_settings` (`APP_DEFAULTS`: confirmation, condenser, git_name, git_email, sounds, router), `agent_info`, `archive`. One task runs at a time. |
| Store | `store.py` | SQLite (WAL). Tasks (title, pinned, archived, folder, tags) + events. Migrations in place via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`. |
| Agent loop | `agent.py`, `agent_provider.py`, `control.py` | `run_agent(...)` with pause/confirm/checkpoint/resume/images/delegate/subagents/system. `stuck_loop` (4 identical in a row or A/B×3). `ModelResponse.reasoning` → `thinking` event. `control.py`: risk assessment, confirmation policy, hooks (`.dikdik/hooks.json` block rules), subagents (`.dikdik/agents/*.md`), side questions, summaries, goal judge, attachments store. |
| Models | `providers.py`, `model_config.py`, `profiles.py`, `connections.py`, `routing.py`, `model_catalog.py` | `chat_completion` (streams with fallback; applies profile temperature/top_p/max_output_tokens). `ModelResolver.resolve(profile_id)` decrypts the key only at call time; profiles may link a connection (`conn.<id>` key ref). |
| Secrets | `secrets_store.py` | Encrypt-then-MAC store (DPAPI-wrapped key on Windows). `set` keeps one `previous`; `restore_previous`, `rename` (re-seals), `set_env`, `sandbox_values`. Keys named `conn.*` belong to connections and are hidden from `/api/secrets`. |
| Sandbox | `runners.py`, `sandbox.py`, `bash_service.py`, `browser_service.py`, `browser_network.py` | `container_command(..., env_names=...)`, `run_container(..., env=...)` with redaction, `run_shell(..., env=...)`. Docker is **not running** on the owner's machine. |
| Other | `gitops.py` (commit takes an identity), `mcp.py`, `skills.py`, `agent_profiles.py` (critic, memory, secret_refs), `automation.py`, `planning.py`, `drafts.py`, `client.py`, `repository.py` |

### Web UI (`dikdik/web/`)

| File | Role |
|---|---|
| `index.html` | All markup, the SVG icon sprite (`<symbol id="i-…">`), dialogs (settings, palette, meta, info), the composer. |
| `style.css` | Design tokens on `:root` with dark-mode variants; phase blocks are appended at the end of the file. |
| `contracts.ts` | Types, request builders (`buildTaskRequest`), parsers, **`eventToMessages`** (event → chat message mapping; every new event kind needs a case), slash/goal/context/confirmation helpers. Also the old `profilesPanel`/`secretsPanel` widgets, now unused by the app but still covered by `tests/web/m4.test.ts` — remove both together if you clean up. |
| `organise.ts` | Pure helpers: sidebar organisation, fuzzy command ranking, file tree, Markdown table/alert parsing, workspace-path detection. |
| `models-pane.ts` | Settings → Models (connections, profiles, routing) and Settings → API keys. |
| `drafts.ts`, `composer-size.ts` | Composer drafts and resize grip. |
| `app.ts` | All UI logic (~2,600 lines). Key functions: `renderMessage`/`addToStream` (grouped steps), `stepLabel` + `TOOL_LABELS` (compact rows), `resultCard`, `observe` (polling) and `startStream` (SSE), `renderRunState` (pause/goal/context meter), `renderHistory` (sidebar), `paletteCommands`, `openSettings`/`showPane`/`routeSettings`, `syncMode`/`syncSend`, the task-form submit handler (slash commands via `runSlash`). |
| `app.js` | Bun build output — never edit by hand; rebuild and commit it. |

### Tests

- `tests/*.py` — unittest. HTTP tests start a real loopback server (`tests/test_http.py`
  helpers `call`, `wait_terminal`). Agent tests script models with a `Script` class
  (`tests/test_control.py`). Use real SQLite and temp dirs.
- `tests/web/*.test.ts` — bun. **`tests/web/review-polling.test.ts` imports `app.ts`
  for real with a tiny DOM double.** If you add an `input(...)`, `button(...)` or
  `select(...)` lookup that runs at load, add its id to the lists on lines ~40–42;
  if sign-in calls a new endpoint, add it to the mock `fetch` (otherwise it counts
  as a task GET and the request-count assertion fails).
- `crates/dikdik-index/tests` — cargo.

---

## 4. Conventions and gotchas (learned the hard way)

- **Match the surrounding code**: small focused modules, short docstrings that say
  *why*, plain user-facing words ("Apply to workspace", "Allow", "Not test-verified").
- **Adding a request field**: add it to `TaskRequest` (contracts.ts), `buildTaskRequest`,
  `composerRequest` (app.ts), and the `clean` key list + validation in
  `Engine._validate`. Unknown fields are dropped silently by design.
- **Adding an event kind**: emit with `self.store.event(task, kind, data)` (agent
  events are prefixed `agent_`), add a case to `eventToMessages`, and a `stepLabel`
  case for a compact row. Never put secret values or raw keys in event data.
- **Module-level `let` used at load time** must be declared near the top of `app.ts`
  (temporal-dead-zone crashes happened twice). Functions are hoisted; `let`/`const`
  are not.
- **Element types**: `el(id).hidden` is typed `boolean | "until-found"`; compare with
  `=== true` when assigning to a boolean.
- **After editing TS**: `bun run typecheck:web && bun run build:web`, then the web tests.
- **Windows shell**: Git Bash heredocs mangle backslashes (`\n` becomes a newline) and
  sometimes fail on quotes. Write patch scripts and file contents with your
  file-writing tool, then run them with `python`. `write_text` produces CRLF in
  fixtures — compare bytes or strip. `shlex.split(..., posix=False)` on Windows;
  resolve `.cmd` launchers with `shutil.which`.
- **Processes**: to stop a background server/stub, find it by command line
  (PowerShell: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'dikdik.server' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`).
  Restart the server after any Python change; the UI only needs `build:web` + reload.
- **Docker**: not running here. Contract-test Docker paths by stubbing
  `subprocess.Popen`/`run_container`; add live tests guarded by an environment
  variable (pattern: `DIKDIK_RUN_BROWSER=1` in `tests/test_browser.py`). Never report
  a Docker feature as live-verified without that run.
- **Model providers**: no real key is configured. Use the stand-in (section 5).
- **Timing in browser scripts**: wait for state (`waitForFunction`, `waitForSelector`)
  rather than fixed sleeps — earlier "failures" were scripts checking too early.

---

## 5. Running it and checking it in a real browser

```bash
# terminal 1 — stand-in model (scripted replies; see the docstring for trigger words)
python scripts/stub_model.py --port 8899 --key sk-local-test --log stub.jsonl
# terminal 2 — Dikdik on a scratch data dir and a copy of the example project (outside the repo)
mkdir -p C:/dikdik-check && cp -r examples/repair C:/dikdik-check/ws
python -m dikdik.server --no-browser --port 8798 --data C:/dikdik-check/data --workspace C:/dikdik-check/ws
```

Sign in: read the token from `<data>/web.token` and open
`http://127.0.0.1:8798/#token=<token>`. In Settings → API keys save `sk-local-test`
as `local-key`; in Settings → Models add a profile: provider Local, model
`stub-model`, endpoint `http://127.0.0.1:8899/v1`, key `local-key` (or add a
connection and link it). Tick **Share code with model** in the composer.

Browser automation: Playwright is at
`file:///C:/Projects/Dikdik/upstream/OpenHands/node_modules/playwright-core/index.mjs`,
Chrome at `C:/Program Files/Google/Chrome/Application/chrome.exe`. Minimal script:

```js
import { chromium } from "file:///C:/Projects/Dikdik/upstream/OpenHands/node_modules/playwright-core/index.mjs";
import { readFileSync } from "node:fs";
const token = readFileSync("C:/dikdik-check/data/web.token", "utf8").trim();
const browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errors = []; page.on("pageerror", e => errors.push(String(e)));
await page.goto(`http://127.0.0.1:8798/#token=${token}`);
await page.waitForSelector("#workspace:not([hidden])");
// … drive the real flow; wait on state, e.g.:
// await page.waitForFunction(() => ["completed","failed"].includes(document.getElementById("task-state")?.textContent ?? "") && document.getElementById("working")?.hidden);
await page.screenshot({ path: "check.png" });
console.log("page errors", errors);  // must be []
await browser.close();
```

Check desktop (1440×900) and phone (390×844), light and dark, and **look at the
screenshots** — several real bugs were only visible there. Keep scratch scripts
outside the repository.

---

## 6. Work still open in phases A–C (do these first)

Each item maps to audit rows still `N`/`P`; read their notes.

**Run control / chat (Phase A)**
- Interrupt a running tool call (today Pause waits for the step; Stop cancels the run). srv `F05.interrupt-tool-call`, app `F06.stop-resume`.
- Usage tab: per-turn and per-task token breakdown from provider reports (input/output/cache when reported), never invented costs. app `F27.usage-*`, `F07.display-cost`.
- Image lightbox for attached images (`F06.image-attachments`).
- Branch from an individual message (user message: fork without it; agent message: inclusive). Needs fork by event seq, not only finished turns (`F06.branch-from-here`, `F07.branch-from-message`, srv `F10.fork-from-event`).
- Confirmation shortcuts Ctrl/Cmd+Enter (allow) and Shift+Ctrl/Cmd+Backspace (reject) in addition to Y/N.

**Interface (Phase B)**
- Language switching (i18n with English as source; `<html lang>`), title generation with a chosen model (`F16.language`, `F16.title-model*`).
- Settings phone hub with Back, save states and unsaved-change prompts (`F09.phone-*`, `F16.save-states`).
- Conversation status menu (`F07.status-menu`), hover preview, collapsed rail, folder "+" to start a conversation in it, presets (`F04.*`).
- Files: image preview, split diff, Markdown diff preview, pinned tabs, open tabs kept across reloads, tree resize (`F08.files-*`, `F08.diff-*`).
- "Show N earlier messages" was never browser-checked (needs a conversation with >150 messages).

**Models and secrets (Phase C)**
- Classifier-based routers (a prompt template with `{{ instance_text }}` and a classifier model, model table) alongside the rule-based ones (`F12.*`, srv `F22.*`).
- "Add models from a connection" in bulk (`F10.add-models`, `F11.add-models`).
- The agent's `request_secret` tool showing a "The agent needs a secret" card (`F14.agent-request`).
- Settings schema endpoints, profile limit (50) and connection limit (64), typed validation errors, phone layouts for these panes.
- Sandbox secrets have never reached a real container — add a live test guarded by `DIKDIK_RUN_DOCKER=1` (like `tests/test_sandbox.py`) and run it if the owner starts Docker.

**Flaky test to investigate:** once during the final handoff check, a full
`python -m unittest discover -s tests -v` run reported `errors=1`; two reruns right
after were clean (307 run, 2 skipped). A third run identified it: `tests/test_control.py::ControlTests::test_goal_capped_and_stopped` failed with "task did not finish" (its 20-second idle wait timed out under load). If you
see it, record which test it was and fix the cause (likely a timing or port race in
an HTTP or engine test) rather than retrying.

---

## 7. Phase D — Extensions and automations

Build in this order; one commit per numbered item (or per sub-feature if large).

1. **Skills marketplace** (app F18, srv F25): search, facets, detail view, copy
   source, install from a local folder or a Git URL at a pinned ref (validated front
   matter, size limits), refresh, self-heal of broken installs, uninstall. Any
   network fetch needs explicit approval at the moment of install; clone into the
   data directory, never execute anything from it.
2. **Plugins** (app F19, srv F26): a plugin manifest bundling skills, MCP servers,
   hooks and agent profiles; install from local/Git, a trust review screen listing
   everything the plugin adds before first enable, enable/disable, launch
   parameters. Plugins can never widen approvals or add host processes without the
   user approving each MCP stdio command.
3. **MCP completeness** (app F17, srv F19): catalog/library, search, edit, install
   options and arguments, OAuth for remote servers (authorization-code + PKCE with a
   loopback redirect `http://127.0.0.1:<port>/api/mcp/oauth/callback`; tokens sealed in
   `SecretsStore`; refresh handling), per-server secrets.
4. **Extension apps** (app F20, srv F28/F29): installable app pages in a sandboxed
   iframe (`sandbox` attribute without `allow-same-origin`, served from a separate path,
   no access to the session cookie; CSP updated narrowly and documented), an optional
   app backend run in Docker reached through a loopback proxy that strips cookies and
   auth headers; enable/disable/uninstall.
5. **Automations suite** (app F21–F23, F28): dashboard (search, filters, sort, overview
   tiles, card/list views), detail page with activity and logs, templates,
   import/export (JSON, secrets excluded), cron schedules (write a small stdlib cron
   parser with tests), event triggers (e.g. a local Git commit detected by polling),
   drafts with a test run, per-automation repository, agent-assisted setup. Stored
   approvals stay exactly what the user set (existing rule).
6. **Automation Git sync** (app F24): sync automation definitions to a Git repository
   the user picks; secrets are never written (or are sealed with a user-held key);
   every push needs explicit approval; hooks disabled.

## 8. Phase E — Platform and APIs

1. **Workspaces** (app F03, srv F17, srv F04 `create-worktree`): several approved
   projects, a folder browser limited to user-approved roots, a workspace registry,
   isolated Git worktrees per task. Today the server is started with one `--workspace`
   and the data dir is bound to it (`Store.bind_workspace`) — design the migration.
2. **Backends** (app F25 non-cloud rows): connect the UI to another Dikdik server (URL +
   key), switch per tab, health, recovery. This is the Dikdik Cloud seam (section 10).
3. **Server APIs** (srv F01–F07, F12–F16, F18, F31, F32): health/ready/info/OpenAPI;
   multiple API keys with scopes and rotation; CORS only for configured origins;
   deferred initialisation; a WebSocket session socket next to SSE; events
   search/count/batch; file upload/download/archive; Git diff by commit and repo
   search; workspace file serving with range requests; VS Code (code-server) in Docker;
   runtime info; persistence retention and config precedence; outgoing webhooks
   (opt-in, never telemetry).
4. **OpenAI-compatible gateway** (srv F30): `/v1/chat/completions` and `/v1/responses`
   backed by the agent; loopback only; keyed.
5. **Clients** (srv F33, F34): finish the Python SDK (`dikdik/client.py`: workspace
   exec, settings, pause/resume, fork, ask, secrets, reconnect, custom tools) and a
   TypeScript client package built with Bun, typed, tested against a real server.
6. **Runtime variants** (app F26): `--version`/`--info`, flag conflicts, a clean
   "port in use" message (no traceback), a Docker image of Dikdik itself, a library
   build of the UI, and a desktop shell (prefer **Tauri**; record it in
   `docs/DECISIONS.md`).

## 9. Phase F — Optional (ask the owner first)

ACP agents (Claude Code, Codex, Gemini CLI) as alternative agents when installed
(`acp-*` rows), and ChatGPT-subscription login (srv F24). Both depend on outside
products; build only on an explicit yes.

**Out of scope entirely:** OpenHands Cloud and DigitalOcean rows (leave `E`),
telemetry rows (leave `X`), sign-up, billing, organisations, public share links,
hosted infrastructure.

---

## 10. Dikdik Cloud seams (design for, do not build)

- The API is the only interface: UI and clients talk HTTP/SSE/WebSocket with a
  configurable base URL; no UI code assumes the server is local except the default.
- Auth is pluggable: session cookie for the local browser, bearer keys (with scopes)
  for clients and other backends.
- Data is per workspace and per user under the data directory; no global singletons.
- The sandbox stays behind `runners.py` so a remote sandbox provider can be added.

---

## 11. Per-feature workflow (do every step)

1. Read the audit rows and the reference-map section for the feature.
2. Design it the Dikdik way (original code, section 2 rules, existing patterns).
3. Build backend + route + UI together; the UI must be reachable and usable.
4. Tests: Python unittest (real loopback HTTP, real SQLite, failure paths, the
   security rules — especially "secret never appears"), bun tests for UI logic and
   parsers, cargo for Rust. Update the DOM double if needed.
5. Browser check with the stand-in (section 5), desktop and phone, light and dark;
   `pageerror` must be empty; look at screenshots.
6. Update audit rows (status + plain-words note) and user docs.
7. Run all of section 1's suites. Everything green.
8. Commit (one feature per commit, clear message, body says what was verified and
   what was only checked against stand-ins). Do not push.

## 12. Per-phase report to the owner

- What was built (by audit family), new audit totals, and the updated
  `docs/PARITY.md` table column; `PROJECT_STATUS.md` (milestone + test counts) and
  `README.md` ("What works" / "Not yet") updated.
- Test counts for Python, web and Rust.
- What was checked in a real browser, what only against stand-ins, what not at all.
- Anything skipped, blocked (needs Docker, a real key, an OAuth provider) or needing
  a decision.

Do not claim parity with OpenHands until the audit says so row by row.
