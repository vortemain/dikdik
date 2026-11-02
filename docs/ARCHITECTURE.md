# Architecture and API contract (preview protocol 1)

```text
Browser (original Bun-built TypeScript, HttpOnly session cookie)
    │ same-origin loopback HTTP; real-event polling
    ▼
server.py ── Engine (one worker + cancellation flag)
                 ├── repository.py: bounded UTF-8 inventory, hashes, edit validator
                 ├── providers.py: explicit edits OR one approved HTTPS proposal
                 ├── agent.py + agent_provider.py: original bounded tool/repair loop
                 ├── store.py: SQLite tasks + ordered events, atomic transitions
                 ├── bash_service.py: durable task-scoped command/events API
                 ├── browser_service.py: fresh-profile browser capture + scoped screenshots
                 ├── planning.py: separate read-only planners + review/progress metadata
                 ├── runners.py: which test suites and declared checks a project has
                 └── local_exec.py: commands and tests on this computer (local mode only)
                          │ project folder, user's rights, time/output limits,
                          ▼ process-tree supervision, secret redaction
                    this computer (no container; Docker was removed 2026-11-02)
```

`locking.py` holds the exclusive data-directory OS lock. `web/` contains the
original wordmark/icon, design tokens, responsive workspace and accessible labeled
controls. `examples/repair` is an intentionally broken two-test demonstration.
`scripts/evaluate.py` and `scripts/benchmark.py` are outcome/measurement entrypoints.
No remote database or broker is required. The optional Rust indexer accelerates
repository scanning and safely falls back to Python. Bun + one TypeScript
dev dependency build/check the original frontend; generated assets are packaged,
so neither is a backend runtime requirement. No OpenHands runtime is imported.

## Persistence and lifecycle

SQLite tables: `tasks(id, created, state, request, result)`,
`events(seq, task_id, time, kind, data)`, `bash_events` for ordered durable command
history, `browser_snapshots` for bounded task-scoped PNGs, and `settings(key, value)`
for workspace binding and workspace browser enablement.
Additive `plans` and `task_progress` tables own planner/code links, revisioned plan
content and reported checklist state. Private PLAN.md artifacts are mirrors of the
SQLite content; no cross-filesystem/DB atomicity is claimed.
The core schema (`tasks`, `events`, `settings`, `requirements`) is versioned in SQLite's
`PRAGMA user_version` by `dikdik/migrations.py`: each migration and its version bump commit in
one transaction, an existing database is copied to `state.sqlite3.bak-v<old>` before it is
upgraded, and a database from a newer Dikdik is refused instead of opened. Every module that
owns tables (profiles, automations, MCP servers, connections, planning, terminal history, ...)
versions them through `migrations.ensure(path, module, steps)`, recorded in `schema_versions`,
with the same atomic-step, refusal and pre-upgrade-backup guarantees. Their first step is an
idempotent baseline (literal `ALTER TABLE` statements guarded by `migrations.add_column`) so
databases from before versioning are adopted without loss.

SQL text never contains formatted values. Identifiers and fixed WHERE fragments that cannot be
`?` parameters go through `dikdik/sql.py`, which accepts only explicit allowlists (or the real
columns of a table, read with `pragma_table_info(?)`); the scanner's PY008 rule is a blocking
HIGH for anything else.

Agent turns also carry engineering evidence (`dikdik/engineering.py`): requirements the
model records with the tests that prove them, a deterministic security scan of changed files
(`dikdik/security_scan.py`) and a computed `result.release_gate` (APPROVED,
APPROVED_WITH_DOCUMENTED_RISK, BLOCKED or REJECTED). `GET /api/tasks/{id}/requirements`
returns the latest recorded list. Requirement evidence comes from per-test outcomes: unittest
`-v`, pytest, cargo, go and bun/jest text, and JUnit XML (local `bun test` writes one to a temp
file outside the project). The gate also covers:

- `quality_check`: reliability (timeouts, silently ignored errors), i18n (naive timestamps,
  platform encodings, money as floats) and privacy (credentials or personal data in logs) rules
  on changed production files, plus static HTML accessibility (`dikdik/accessibility.py`).
- `preview_app`: an in-browser WCAG 2.2 subset (`accessibility.AUDIT`: names, labels, lang,
  title, duplicate ids, heading order, computed contrast, target size, visible focus). Serious
  failures fail the preview; moderate ones are reported for review.
- `architecture_map` / `impacted_tests` / `record_decision` (`dikdik/architecture.py`): the import graph,
  cycles and change impact, and numbered ADRs under `docs/adr/`.
- `load_test` (`dikdik/loadtest.py`) and `repeat_command`: concurrency against the project's own loopback
  server, and nondeterminism detection.
- `dast_scan` (`dikdik/dast.py`): GET-only crawl and dynamic security checks of the project's running server;
  a `dynamic_security` gate.
- `fault_test` (`dikdik/faults.py`): the app reaches one dependency through a switchable loopback fault proxy
  (latency, reset, early close, partition, down); a `resilience` gate.
- Visual regression (`dikdik/visual.py`): `preview_app` compares each capture with the owner-accepted baseline
  (`visual_baselines` table; `GET /api/visual-baselines`, `/image`, `POST /accept`); a `visual` gate.
- `parallel_edit` (`dikdik/parallel.py`): 2-4 writer agents with disjoint file ownership run concurrently on
  copies of the candidate; the coordinator merges by ownership and verifies once.
- Traces: `dikdik/tracing.py` turns agent events into OTLP spans when `DIKDIK_OTEL_ENDPOINT` is set.
- Operations: `python -m dikdik.backup`, `python -m dikdik.sbom`, `python -m dikdik.architecture`.
- `measure_performance`: repeated wall-time runs of a read-only command with min/median/p95/max,
  an explicit p95 budget and a per-project baseline (`engineering.Baselines`), offered only with
  execution approval and confirmed like `run_command`. Operational diagnostics go to redacted JSON lines in
`<data>/logs/dikdik.jsonl` (`dikdik/logs.py`); no route serves them.

States: `queued → running → completed | failed | blocked | cancelled`.
`draft` is an initialized conversation awaiting its first approved turn, not a
claim of completed execution. Planner children have separate histories and no
repository-write/execution tools. Build checks the planner identity and revision
and snapshots untrusted plan content into the code conversation's handoff event.
Abandoned queued/running tasks become `interrupted` on restart. Terminal state and
its evidence event commit in one transaction; task reads use a consistent SQLite
snapshot. Events have global monotonically increasing sequence IDs. No exact
filesystem/DB transaction, pause/resume or automatic task replay is promised.

Flow: validate permissions → inspect source → record plan → propose edits →
validate → generate diff and isolated artifact → parse Python syntax → record
truthful checks/outcome. Tests run only in local mode, on this computer.
Cancellation is cooperative between steps and actively monitored while a command
runs. A running model request may delay cancellation. Exceptions cause failure, not synthetic success.

`completed` means the selected workflow ended, **not necessarily test-verified**.
`result.tests_verified` is true only after a successful container runner and no
other failing checks. Python syntax can be passed/failed/not_run; container checks
also distinguish blocked, timed_out, output_limit, cancelled and inconclusive.
The `agent` mode implements bounded repair via model/tool observations; its
live-provider/sandbox success remains unverified. Single-proposal `model` and
explicit `offline` modes still have no automatic repair loop. See `ORIGINAL_AGENT.md`.

Candidate artifacts live at `<data>/artifacts/<task-id>/`. SQLite owns metadata and
evidence; artifact directories own candidate contents. Source workspace is not
modified. Artifacts may survive cancellation/failure. No retention daemon exists.

## HTTP protocol 1

The local preview uses `/api/` routes with this documented version-1 contract.
It is not a stable public API; future incompatible versions must get separate
routes/migrations. All API requests require the session cookie set by
`POST /api/session {"token": …}` (or `Authorization: Bearer <token>`) and
exact `Host: 127.0.0.1:<port>`; browser Origin, if present, must match. No CORS.
POST JSON bodies are limited to 262144 bytes; chunked transfer is unsupported.

| Method/path | Request | Response |
|---|---|---|
| `GET /api/project` | — | name, files[{path,bytes,sha256}], python_tests, manifests, limitations |
| `GET /api/tasks` | — | up to 100 task summaries, newest first |
| `POST /api/tasks` | task request below | 202 `{id}` |
| `GET /api/tasks/{id}` | — | id, created, state, request, result, events |
| `POST /api/tasks/{id}/cancel` | `{}` | `{requested: boolean}` |
| `POST /api/tasks/{id}/bash/execute` | command, optional timeout/cwd (local-mode tasks only) | final output after the command exits |
| `POST /api/tasks/{id}/bash/start` | command, optional timeout/cwd (local-mode tasks only) | 202 command identity before completion |
| `POST /api/tasks/{id}/bash/commands/{command}/stop` | `{}` | whether cancellation was requested |
| `GET /api/tasks/{id}/bash/events` | query: after, limit, kind, command_id | ordered task-scoped command/output page |
| `GET /api/tasks/{id}/bash/events/{event}` | — | one event or 404 |
| `DELETE /api/tasks/{id}/bash/events` | — | cleared count; refused while a command runs |
| `GET /api/browser`, `PATCH /api/browser` | optional `{enabled: boolean}` patch | browser runtime/approval metadata |
| `POST /api/tasks/{id}/browser` | URL and separate Boolean execution/browser approvals | capture status and optional scoped screenshot URL |
| `GET /api/tasks/{id}/browser/screenshots/{snapshot}` | — | authenticated task-scoped PNG |
| `GET/POST /api/tasks/{id}/planner` | `{}` to initialize | durable plan revision and planner/code IDs |
| `POST /api/tasks/{id}/planner/build` | review confirmation, planner ID, revision and new turn approvals | 202 code conversation ID |
| `GET /api/tasks/{id}/progress` | — | reported checklist and revision |

The browser implementation is a replaceable `capture` boundary. It uses an
operator-built Playwright/Chromium image with no container network or workspace
mount. Public GET resources cross a bounded stdin/stdout relay whose trusted
standard-library fetcher pins validated public DNS addresses and verifies HTTPS
hostnames. Runtime code, dependency/setup details and the remaining live-verification
gate are documented in [BROWSER.md](BROWSER.md).

Task request:

```json
{
  "objective": "Fix addition",
  "mode": "offline",
  "edits": [{"path":"calculator.py","before":"return a - b","after":"return a + b"}],
  "verification": "syntax",
  "approve_execution": false,
  "approve_external": false
}
```

`objective`: nonblank string, max 4000 characters. `mode`: offline, model or agent.
`verification`: syntax or local (`docker` is refused). Offline requires 1–20 exact
replacement objects. Model requires approval and server-side provider configuration;
it ignores edits as a proposal source. Execution and external approvals must be
literal JSON `true`. Agent mode accepts an objective with no edits, requires
disclosure approval, and supports syntax-only or explicitly approved local work. Plan mode requires
disclosure but forbids execution approval and uses only read-only inspection plus
private plan/progress tools. Agent mode uses original in-memory tools and candidate-
specific completion gating, with counters stored in `result.agent`. Every edit
invalidates previous passing evidence; no net change or no current test pass
prevents `finish`. Provider/tool failures remain real structured events.
Only one active agent turn is accepted. Bash commands are independently bounded
to two worker slots, require the target task's stored Boolean execution approval,
and never run on the host or write to the approved workspace. Proposal changes cannot add files or modify
test-named files; all replacements must uniquely match and change content.

Errors: 400 `{error}` validation/busy, 401 unauthorized, 403 host/origin,
404 missing route/task, 500 storage/repository unavailable. Worker failures are
terminal task states, not successful responses containing imaginary results.
Current error strings are human-oriented; stable machine-readable error codes,
idempotency keys and WebSocket transport are future work. Task and Terminal live
updates use the existing authenticated server-sent-event stream.

Result fields: `diff`, `changed_files`, `checks`, `tests_verified`,
`source_modified: false`, `artifact` task ID, measured `seconds`, `limitations`;
early failures may contain only `error`. Events: `{seq,task_id,time,kind,data}`;
Terminal events add a command/event UUID, per-command order and final status.
Neither objective nor source content is safe to insert as HTML. The UI renders
all backend data as text. No secret management endpoint exists.
