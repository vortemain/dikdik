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
                 └── runners.py + sandbox.py: fixed Docker shell/test runners
                          │ read-only candidate mount, no source mount
                          ▼
                    restricted container
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
history, and `settings(key, value)` for workspace binding.
Schema is created idempotently at startup; there is no existing schema to migrate.
Future schema changes require explicit migrations before wider release.

States: `queued → running → completed | failed | blocked | cancelled`.
Abandoned queued/running tasks become `interrupted` on restart. Terminal state and
its evidence event commit in one transaction; task reads use a consistent SQLite
snapshot. Events have global monotonically increasing sequence IDs. No exact
filesystem/DB transaction, pause/resume or automatic task replay is promised.

Flow: validate permissions → inspect source → record plan → propose edits →
validate → generate diff and isolated artifact → parse Python syntax → optionally
execute Docker tests → record truthful checks/outcome. Cancellation is cooperative
between steps and actively monitored during Docker execution. A running model
request may delay cancellation. Exceptions cause failure, not synthetic success.

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
| `POST /api/tasks/{id}/bash/execute` | command, optional image/timeout/cwd | final output after the Docker command exits |
| `POST /api/tasks/{id}/bash/start` | command, optional image/timeout/cwd | 202 command identity before completion |
| `POST /api/tasks/{id}/bash/commands/{command}/stop` | `{}` | whether cancellation was requested |
| `GET /api/tasks/{id}/bash/events` | query: after, limit, kind, command_id | ordered task-scoped command/output page |
| `GET /api/tasks/{id}/bash/events/{event}` | — | one event or 404 |
| `DELETE /api/tasks/{id}/bash/events` | — | cleared count; refused while a command runs |

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
`verification`: syntax or docker. Offline requires 1–20 exact replacement objects.
Model requires approval and server-side provider configuration; it ignores edits
as a proposal source. Docker and external approvals must be literal JSON `true`.
Agent mode accepts an objective with no edits, requires both approvals, and requires
Docker (syntax-only is rejected). It uses original in-memory tools and candidate-
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
