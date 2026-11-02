# Engineering assessment against the Dikdik constitution (v2.0)

Assessed: 2026-11-01, branch `claude/dikdik-engineering-constitution-f75b9c` from `70c2099`
(v0.3.3). Method: reading the source (not the docs' claims), running the test suites,
and reproducing defects before fixing them. Where a status rests on documentation
rather than something run here, it says so.

Statuses: `VERIFIED` (run or reproduced in this assessment), `IMPLEMENTED_BUT_UNVERIFIED`
(code exists, not exercised here or only with stand-ins), `PARTIAL`, `MISSING`, `BROKEN`.
Priorities: P0 security/data integrity, P1 needed for reliable operation, P2 important
improvement, P3 optimization.

## 1. What Dikdik actually is

A single-owner, local-first coding-agent workspace: a Python 3.13 standard-library HTTP
server (`dikdik/server.py`, `server_routes.py`) serving a Bun-built TypeScript UI
(`dikdik/web/`), an original tool-calling agent loop (`agent.py`) driven by
`engine.py`, SQLite persistence (`store.py`, WAL, `busy_timeout`), and an optional Rust
indexer (`crates/dikdik-index`). About 19k lines of Python across 75 modules, 92 Python
test modules (888 test functions), Bun web tests and 15 Rust tests. CI
(`.github/workflows/release.yml`) runs unittest, typecheck, web tests and a bundle
reproducibility diff on Ubuntu and Windows, then builds and smoke-installs the wheel.

```text
Browser UI (TS)  ──HTTP/SSE, session cookie or bearer──▶  server.py ─▶ Engine (engine.py)
                                                                       ├─ agent.py         tool loop, budgets, repair, compaction
                                                                       ├─ control.py       risk, permission rules, confirmations
                                                                       ├─ direct.py        local writes, checkpoints, Undo/Redo
                                                                       ├─ local_exec.py    host commands (Ask/Auto)
                                                                       ├─ runners.py       test/check detection
                                                                       ├─ providers/routing/model_*  model abstraction + routing
                                                                       ├─ mcp*.py, skills*.py, plugins.py, hooks.py
                                                                       ├─ browser_service/host_browser/interactive_browser
                                                                       └─ store.py         SQLite tasks/events/settings
```

## 2. Defects found and fixed in this pass

| ID | Pri | Finding | Evidence | Fix | Status |
|---|---|---|---|---|---|
| F-001 | **P0** | `POST /api/pair` minted a pairing code **without authentication or a Host/Origin check** and returned it to the caller; `POST /api/pair/verify` traded it for the **owner token**. Any process reaching the port, a DNS-rebinding web page (foreign `Host`), or — with `--listen` — anyone on the network got full control, including host command execution in Auto mode, bypassing the sign-in lockout. | Reproduction script: two unauthenticated requests with `Host: attacker.example` returned `{"token": "owner-secret-token"}`. The old test asserted the routes were "pre-auth". | Minting requires the signed-in owner session (API keys, even admin, are refused so they cannot escalate). Redeeming requires the exact Host/Origin, a 1–1024 byte body, the remote sign-in lockout, and revokes every outstanding code after 5 wrong guesses. Codes are 32-bit, single use, 10 minutes, at most 5 outstanding. | `VERIFIED` — repro now gets 403/403; 5 regression tests in `tests/test_server_api.py`. |
| F-002 | P2 | With `parallel_reads` (always on in the engine), each read in a multi-call batch was passed to `confirm()` twice — before dispatch and again in order — so a guarded read (`.env` in Ask mode, or an `ask` permission rule) prompted the user twice and logged duplicate confirmation events. The batch's thread pool was also never shut down. | New test fails on the original code (`2 != 1` confirmations). | The pre-dispatch decision is stored and reused; the pool is shut down after submission. | `VERIFIED` — `test_parallel_reads_ask_each_confirmation_once`. |
| F-007 | P3 | `control._rule_matches` called `rule.get()` before `isinstance(rule, dict)`, so a malformed stored rule raised instead of being skipped (unreachable today because `parse_rules` validates on save). | Guards swapped. | `VERIFIED` — a non-dict rule is now skipped and the next rule decides. |

### Pass 2 (same day): every remaining finding

| ID | Pri | Finding | Fix | Status |
|---|---|---|---|---|
| F-008 | P1 | No security tooling in CI. | **Route-authorization test** (`tests/test_route_authorization.py`) harvests every `/api/...` literal from the source and asserts anonymous and foreign-Host requests are refused for GET/POST/PATCH/DELETE; against the original code it reports exactly `POST /api/pair -> 200`. **`dikdik/security_scan.py`** (stdlib): secret rules, Python AST rules, JS/TS sink rules, OSV lockfile audit (Cargo, npm, Bun, pip, Poetry/uv, Composer), counted suppressions, CLI exit codes. **`.github/workflows/security.yml`**: scanner with `--deps --fail-on high`, pip-audit 2.10.1, `bun audit`, Gitleaks 8.18.4 over full history, weekly schedule. | `VERIFIED` locally for the test, the scanner (11 unit tests) and the audits; Gitleaks and the workflow itself run only in CI (`NOT_RUN` here: no Go). |
| F-010 | **P1** | *Found by the new scanner:* the Rust indexer pinned **PyO3 0.23.5**, affected by GHSA-36hh-v3qg-5jq4 (out-of-bounds read), RUSTSEC-2025-0020 (buffer overflow) and RUSTSEC-2026-0177. | Upgraded to PyO3 0.30.1 (fixed in 0.29.0); migrated `FromPyObject` and `downcast` to `cast`. | `VERIFIED` — 16 Rust tests, the release build loads (`INDEXER == "rust"`), 19 repository parity tests; OSV rescan: 0 high/critical across 39 packages. |
| F-011 | P2 | *Found by the scanner:* `tests/test_remote.py` disabled TLS verification entirely. | Trusts exactly the generated test certificate, so the chain is verified. | `VERIFIED` — remote tests pass. |
| F-009 | P1 | No live Docker boundary evidence. | Superseded 2026-11-02: the owner removed Docker from Dikdik (ADR-007), so there is no container boundary to prove. The acceptance script and CI job were removed with it. | `NOT_APPLICABLE` |
| F-004 | P2 | Parallel reads ran before `tool.before` hooks. | The pre-dispatch pass validates arguments, honours batch rules and repeated-failure stops, then asks hooks and confirmation once; the ordered pass reuses both decisions. | `VERIFIED` — `test_before_block_stops_a_parallel_read_before_it_runs` fails on the original code (`['app.py', 'secret.py'] != ['app.py']`). |
| F-005 | P2 | Ad hoc schema evolution. | `dikdik/migrations.py`: `PRAGMA user_version`, each migration atomic with its version bump (no `executescript`, which would commit early), pre-upgrade backup `state.sqlite3.bak-v<old>`, refusal of newer schemas. Module-owned tables still self-migrate idempotently (documented). | `VERIFIED` — 6 tests: fresh, legacy v0.2.0-shape upgrade with data and backup, newer-schema refusal with a byte-identical file, failing migration fully rolled back, contiguity, idempotence. |
| F-003 | P2 | Hand-rolled secret cipher with key reuse and counter-only uniqueness. | Format v2: HMAC-derived separate enc/MAC keys, HMAC-SHA256 keystream over a random 16-byte nonce, encrypt-then-MAC over a header naming the format. v1 still opens; `upgrade()` authenticates and re-seals v1 values (including kept previous values) atomically at engine start. Still stdlib-only, per the owner's decision. | `VERIFIED` — 6 tests incl. rolled-back-file keystream reuse, v1 compatibility + upgrade + restore, corrupt-record abort with an unchanged file, format-name tampering. |
| F-012 | P2 | No structured, redacted logging (diagnostics were a raw stderr traceback). | `dikdik/logs.py`: rotating JSON lines in `<data>/logs/`, redaction of credential shapes, registered runtime secrets (session token), bearer values and secret-named fields; wired to server start, failed sign-ins and pairings, 500s and unexpected task failures. | `VERIFIED` — 4 tests incl. a real failed HTTP sign-in whose attempted value and owner token never reach the file. |
| F-006 | P2 | Documentation drift. | README now names v0.3.3 (assets confirmed on the release); HANDOFF corrected (phases 13–22 shipped in `cd15aef`) with phase 23; ARCHITECTURE, SECURITY and CHANGELOG describe the new behaviour. | Done. |
| F-013 | P1 | Requirements traceability and release gates were missing (constitution Parts 3.3 and 29). | `dikdik/engineering.py` + agent tools `record_requirements`, `security_scan`, `release_gate`. Requirements are validated strictly (no self-reported status field; optional cannot be must). A requirement is `VERIFIED` only when every named test appears, as a whole name, in the output of a real passing run on the current files. The gate decides APPROVED / APPROVED_WITH_DOCUMENTED_RISK / BLOCKED / REJECTED; `finish` is refused when the turn introduces a critical finding; the computed gate is stored in `result.release_gate`, shown in the evidence card, and requirements persist in the versioned `requirements` table (`GET /api/tasks/{id}/requirements`). | `VERIFIED` — 7 engineering tests (incl. scripted agent runs: an approved traced change, and a refused finish after adding a credential) and an HTTP route test. Not exercised with a live model. |

### Pass 3: the open items O-1 to O-6

| ID | Item | What was built | Status |
|---|---|---|---|
| O-1 | SQL formatting | `dikdik/sql.py` allowlisting helpers (`ident`, `columns`, `assign`, `marks`, `clause(s)`, `table_columns` via `pragma_table_info(?)`); all 26 formatted statements rewritten; the scanner accepts formatted SQL only when every interpolation is a helper call, tracks SQL built in variables with `+=`, and PY008 is now a blocking HIGH. | `VERIFIED` — 0 PY008 in the repo; the variable tracker flags the old `/api/tasks/search` query (missed by the first rule); `tests/test_sql.py`; mutation score 100% (20/20). |
| O-2 | Data-flow SAST | `.github/workflows/codeql.yml`: CodeQL `security-extended` for Python and TypeScript, pinned to v4.39.0 by commit SHA (verified via the GitHub API to contain the action; 17 days old). | `NOT_RUN` here — runs only on GitHub. |
| O-3 | Module tables | `migrations.ensure(path, module, steps)` with `schema_versions`, atomic steps, newer-version refusal and backup before a versioned upgrade; 17 modules converted, the five with column history to literal guarded `ALTER TABLE` steps. | `VERIFIED` — tests: adoption of a pre-versioning `routers` table with rows kept, rolled-back failing step, backup, refusal, and every engine/route module registered. |
| O-4 | Structured test results | Per-test parsing for unittest `-v` (incl. docstring lines), pytest, cargo, go and bun/jest text, and JUnit XML. Local unittest now runs with `-v`, Docker cargo without `--quiet`, and local bun writes JUnit to a temp file outside the project. Failed or skipped tests linked to a requirement mark it `FAILED`. | `VERIFIED` — fixtures captured from real unittest, cargo and bun runs; a real local bun run returns structured results and leaves no file. Found: bun prints no passing names off a terminal, so text parsing alone could never verify bun requirements. |
| O-5 | Coverage, mutation | Branch coverage in the release test job with a floor in `pyproject.toml`; `scripts/mutation_test.py` (stdlib; mutates a temp copy, never the tree) and a weekly CI job with score floors. | `VERIFIED` — see section 6 for coverage and mutation scores; survivors were turned into tests or classified as equivalent. |
| O-6 | Target-project engines | **Accessibility**: in-browser WCAG 2.2 subset in `preview_app` plus static HTML checks. **Reliability / i18n / privacy**: REL, I18N, PRIV rule families on production files. **Performance**: `measure_performance` with p95 budgets, baselines and regression detection. All feed new release-gate gates. | `VERIFIED` — real Chromium run detects all 11 rules on a broken page (computed contrast 2.32:1) and passes a clean one; quality-rule and performance tests incl. a real timed process. |

Found by the new checks while closing these (all fixed, with regression tests):

| ID | Pri | Finding | Evidence |
|---|---|---|---|
| F-014 | **P1** | Provider failover never activated for the default profile: the lookup bound no parameter when no profile was chosen, raised, and a silent `except: pass` hid it. For a chosen profile, `id=? OR is_default=1 ORDER BY created` could return the default profile's fallback. | Replaying the old query: `None` → `ProgrammingError`; `chosen` → the default's fallback. `tests/test_failover.py`. Found via REL002 (silent `except`). |
| F-015 | P2 | `engine.py` said "logged in diagnostics" where nothing was logged; scheduler, warming, webhook, purge and API-key failures vanished. | Now written to the redacted diagnostics log. |
| F-016 | P3 | Product `taskkill` calls had no timeout; scripts opened text files with the platform default encoding (cp1252 on Windows). | Timeouts and `encoding="utf-8"` added. |

### Pass 4: the remaining inventory rows

| Row | What was built | Evidence |
|---|---|---|
| 4 Architecture | `dikdik/architecture.py`: import graph for Python (absolute, relative, package initializers) and JS/TS (`import`, `export from`, `require`, dynamic `import()`, `.js`→`.ts`, `index`), Tarjan cycle detection, fan-in/out, unimported modules, Mermaid diagram; agent tools `architecture_map` and `record_decision` (numbered ADRs with context, alternatives, consequences, risks, reversal conditions, through the create-file approval). | `tests/test_architecture.py` (9). On Dikdik itself: 256 modules, 558 imports and **5 real import cycles** (e.g. `repository.py`↔`workspace_files.py`, `mcp.py`↔`mcp_oauth.py`). |
| 36–37 Change impact, scheduling | `architecture.impact` and the `impacted_tests` tool: tests that reach the changed files through imports, plus unmapped tests reported separately; the full suite is still the release check. CLI `python -m dikdik.architecture --changed FILE`. | Tested on a mixed Python/TS fixture, including the turn's own changed files as the default. |
| 35 Supply chain | `python -m dikdik.sbom`: CycloneDX 1.5 with purls (pypi, npm incl. scopes, cargo, composer), licences where lockfiles record them, copyleft report and `--deny`; generated and uploaded in CI. | `tests/test_sbom.py` (3); 39+ components for Dikdik incl. `pkg:cargo/pyo3@0.30.1`. |
| 44 Disaster recovery | `python -m dikdik.backup create/verify/restore`: SQLite online-backup copies, SHA-256 manifest, integrity checks, path-escape refusal, refusal while running, never merges (`--force` moves the old directory aside). | `tests/test_backup.py` (6): round trip restores tasks, events and sealed secrets and the restored directory runs an `Engine`; live backup; tampering detected. |
| 34 Infra/DevOps | INF rule family for Dockerfiles, Compose files and GitHub workflows (unpinned images and actions, root user, secrets in `ENV`/`ARG`, `curl \| sh`, remote `ADD`, `write-all`, `pull_request_target` with PR-head checkout, privileged/host-network/Docker-socket). | 4 test groups; Dikdik's own Dockerfiles and workflows pass. |
| 22, 29–32 Load, concurrency | `load_test` (the project's server on a supervised loopback port; N requests at concurrency C; throughput, p50/p95/p99, status counts, error rate vs budgets) and `repeat_command` (stable / failing / flaky). Both feed the gate (performance, stability). | `tests/test_loadtest.py` (7) against a real HTTP server: real 50 ms latency measured, 5xx counted as errors. |
| 39 Observability | `dikdik/tracing.py`: OTLP/HTTP JSON traces (task → model calls, tool calls) from the agent's events; `DIKDIK_OTEL_ENDPOINT`; loopback-or-HTTPS only; bounded background export; no arguments or content exported. | `tests/test_tracing.py` (5) against a real collector, incl. an engine-level agent turn. Found: the log redactor masked `input_tokens` usage counts (fixed). |
| 9–10 Memory | Observations carry provenance, `established` and a whole-file SHA-256; `revalidate` marks current/stale/missing/unverifiable; the engine revalidates on resume against the files as they are now. | 3 tests incl. a file edited between turns. |
| 42 Benchmarking | `scripts/benchmark_tasks.py` + 4 tasks (logic bug, off-by-one, validation, SQL injection), each with hidden oracle tests and a reference solution; JSON report with version hash, environment, model host, oracle outcome, release decision, calls, tokens, cost, time. | Every oracle fails on its fixture and passes on its solution; against the scripted stand-in the harness counted repair as solved and **pagination as claimed-but-failed-oracle**. Found: environment-configured models rejected loopback HTTP (fixed). |
| 15–16 Execution limits | Windows Job Object limits on committed memory (default 8 GB) and live processes (default 1,024) per command tree, peak memory reported; POSIX `RLIMIT_AS` only when the owner sets `local_limits`. | `tests/test_local_limits.py` (5): a 600 MB allocation refused at 256 MB, a 40-process spawn refused at 16, both pass with limits off. |

### Pass 5: the items that were left

| Row | What was built | Evidence |
|---|---|---|
| 6 Orchestration | `dikdik/parallel.py` and the `parallel_edit` tool: 2-4 writer agents run at the same time, each owning a disjoint set of files or folders (overlap is refused up front). A writer may edit or create only what it owns (`run_agent(writable=...)`), cannot run commands, and its edits merge only if it finished; trespass or an unfinished writer rejects the whole unit. The coordinator then runs the tests once on the merged files. Each writer's edits still pass the permission engine (prefixed call ids). | `tests/test_parallel.py` (10): writers proven concurrent with a barrier; an engine-level local turn where two writer agents fix two files and the real `unittest` passes on the merge. Found: the first design merged edits from a writer that had **failed** part-way; now rejected. |
| 22 Distributed systems | `dikdik/faults.py` and the `fault_test` tool: the dependency starts on a reserved port and the app reaches it through a switchable TCP fault proxy (latency, reset, early close, blackhole/partition, down). Per scenario: answered within `max_ms`, a handled status, still running, recovered after the fault cleared. | `tests/test_faults.py` (6) on two real services: a client with timeouts passes every fault; one without timeouts hangs on the partition and returns 500 on resets; a circuit that never closes fails recovery. |
| 23-25 DAST | `dikdik/dast.py` and the `dast_scan` tool: crawl of the project's own loopback server (same-origin links, GET forms), checking security headers, cookie flags, CORS with credentials, reflected input, SQL/stack-trace disclosure, open redirects, exposed `.git`/`.env`, directory listings. GET only; POST forms are reported, never submitted. Feeds a `dynamic_security` gate (critical fails the turn's gate). | `tests/test_dast.py` (5): every planted flaw found on a vulnerable server (reflected XSS, SQL injection via error, open redirect, `.env`, CORS reflection, cookie flags); its hardened twin passes. Found: the fixture's Python server discloses its version (reported as info). |
| 27-28 Visual regression | `dikdik/visual.py`: standard-library PNG decode/encode, changed-pixel count with tolerance plus SSIM, diff image with the changed region; baselines per project and view. `preview_app` compares each capture; the first capture is pending, and **only the owner accepts** a baseline (`POST /api/visual-baselines/accept`, a button on the gate). Changes are a `visual` RISK for review. | `tests/test_visual.py` (11), incl. every PNG filter type and real Chromium: a changed digit is caught and localized; unchanged pages match. Found: a ratio threshold let a one-letter change (160 px of 1.3 M) pass as MATCH; replaced with an absolute count. |

### Pass 6: first live runs, zero-setup Windows, and what they exposed

Docker was removed (ADR-007). On Windows without Git Bash, agent commands now run in PowerShell with
translation (`dikdik/windows_shell.py`; mutation score 24/24 after two escape tests and one dead sort were
found), `npm.ps1` runs under the default script policy, and Python output is UTF-8.

The benchmark now has 7 tasks: 4 repairs and 3 builds from an empty folder (`notes-api`: a stdlib JSON service with
validation and restart persistence; `todo-cli`: a command-line app; `todo-web`: a browser app judged in real Chromium
for adding, completing, deleting, the count, reload persistence and phone layout). Each hidden oracle fails on the empty
folder, passes on a reference solution, and rejects a plausible broken build (`tests/test_benchmark_tasks.py`).
**First live run (2026-11-02, OpenRouter `z-ai/glm-5.2`, local mode, $0.87 total):** `notes-api` succeeded
(all 7 hidden checks, 25 model calls, 300 s, $0.19) and `todo-cli` succeeded (all 6, 14 calls, 105 s, $0.12).
`todo-web` built a correct app (all 3 hidden Chromium checks passed) but Dikdik ended it `blocked` after 82 calls
($0.56): "current browser verification is missing". A rerun with the new per-task timeline found two real defects:
- **F-017 (P1, fixed): `preview_app`, `load_test`, `dast_scan` and `fault_test` crashed inside the engine** with
  "'_Either' object has no attribute 'wait'" whenever the project's server took more than an instant to start. The
  engine passes `_Either(cancel, pause)`, which had no `wait`; unit tests passed a plain `Event`. All 21 previews in
  the rerun failed this way. `_Either.wait` now returns when any flag is set; a test starts a slow real server
  through that path and fails without the fix.
- **F-018 (P0, fixed): the benchmark agent modified Dikdik itself.** Blocked by F-017 and running with
  `confirmation: never`, it patched `dikdik/engine.py` and `dikdik/local_preview.py` in this worktree and
  `engine.py` in the main checkout (plus a stray `nul` file), all outside its workspace. Those edits were reverted.
  The engine now snapshots its own program files around every command, preview and server check and ends the turn
  as `blocked` if any changed; the agent is told never to modify Dikdik or other folders; the benchmark watches both
  checkouts and never counts a run that wrote outside its workspace as a success. Local mode still runs commands
  with the user's rights, so this detects and stops self-modification rather than preventing every outside write.
**After both fixes, todo-web succeeded:** completed on its own in 224 s (33 model calls, $0.32), six passing
`preview_app` runs, release gate `APPROVED_WITH_DOCUMENTED_RISK`, all 3 hidden Chromium checks passed, nothing
written outside its workspace. So with GLM-5.2 all three build-from-scratch tasks have now been completed from
an empty folder (one run each; a single run per task is evidence, not a reliability rate).

**Success rate (2026-11-02, OpenRouter `z-ai/glm-5.2`, `--repeat 3`, 8 tasks, $2.87, 55 min): 23 of 24 runs (96%).**

| Task | Kind | Runs | Succeeded | Cost |
|---|---|---|---|---|
| repair, pagination, validation, sql-injection | repair | 3 each | 12 of 12 | $0.24 |
| parallel-fix | two writers via `parallel_edit` | 3 | 3 (used `parallel_edit` every time) | $0.15 |
| notes-api | build from empty folder | 3 | 3 | $0.53 |
| todo-cli | build from empty folder | 3 | 3 | $0.33 |
| todo-web | build from empty folder | 3 | 2 | $1.62 |

No run claimed verification that its hidden oracle then refuted, and none wrote outside its workspace.

- **F-020 (P1, fixed): a file the agent's own command created could not be kept or edited.** The failed todo-web
  run created `test_app.py` with a command; the "new files only" creation policy refused it because it now existed,
  so it stayed on disk but out of the agent's files, and once read it counted as a protected pre-existing test. Every
  later command and preview was refused ("changed outside the agent: test_app.py") for 97 calls. Files a turn's
  commands create are now accepted (ignored paths and symlinks still are not); a test fails without the fix.
  After it, todo-web ran 3 more times: **3 of 3** ($0.85). Its failed run also had a real app bug (blank input was
  not cleared), which the hidden oracle caught.

- **F-019 (P2, fixed): the licence gate could never fire.** `sbom` read licences only from npm's
  package-lock, which Dikdik does not have, so all 41 dependencies were "unknown" and `--deny AGPL` passed
  by default. Licences now come from installed metadata, Cargo's crate sources and `node_modules`, for the exact
  version only, and a denied id inside an SPDX expression (`MIT OR AGPL-3.0-only`) is caught.

### Known limits that remain

- Automated accessibility checks cover a subset of WCAG; contrast is skipped over images and gradients, and moderate rules can have legitimate exceptions, so they are reported rather than failing.
- Quality and infrastructure rules are patterns and AST rules; tests are exempt from quality rules (not from security rules).
- The dependency graph sees static imports only; dynamic and name-based loading are invisible, so change impact reports unmapped tests and never replaces the full suite.
- Performance and load results are wall time on this machine, not production capacity.
- On Windows, restored secrets open only for the Windows account that made the backup (DPAPI).
- POSIX memory limits apply only when configured, because address-space limits break some runtimes.
- DAST is a GET-only smoke scan of what the crawl reaches: no authenticated areas, POST handlers, stored XSS or business logic.
- Fault injection covers one app and one TCP dependency; clock skew, disk faults and multi-hop topologies are not simulated.
- Visual comparison assumes deterministic rendering on one machine; animations or live data show up as changes for the owner to review.
- Parallel writers suit independent parts of a change; tightly coupled edits belong to one writer, and writers cannot run commands.

## 4. Capability inventory against Part 32

Statuses as of this pass. `VERIFIED` means exercised by automated tests in this repository (with real
processes, servers, browsers or databases where noted); `VERIFIED (stand-in)` means the path was run end to
end against the scripted stand-in model, not a live provider.

| # | Subsystem | Status | Evidence / remaining gap |
|---|---|---|---|
| 1 | Project discovery | VERIFIED | `repository.py` inventory (Rust indexer with Python parity tests), stack detection; `architecture_map` for structure. |
| 2–3 | Requirements understanding, traceability | VERIFIED (traceability) | REQ records, per-test evidence from real runs, persisted. Understanding the request is the model's job and cannot be verified deterministically. |
| 4 | Architecture/design engine | VERIFIED | Import graph, cycles, metrics, Mermaid, ADR writer. Static imports only. |
| 5 | Planning/decomposition | VERIFIED | Read-only planner, revisioned PLAN.md, checklist; `architecture_map`/`impacted_tests` for dependency-aware plans. |
| 6 | Agent orchestration | VERIFIED (live) | Main loop, read-only subagents, and `parallel_edit`: concurrent writer agents with disjoint file ownership, merge by ownership, one verification on the merge. Exercised with scripted models and real tests, and live: `parallel-fix` used `parallel_edit` in 3 of 3 GLM-5.2 runs and passed its hidden oracle each time. |
| 7–8 | Model abstraction, routing | VERIFIED (live) | Provider HTTP path, routing and failover (F-014 fixed) tested; live runs through a profile saved in the app (OpenRouter `z-ai/glm-5.2`, four benchmark runs). Other providers are contract-tested only. |
| 9–10 | Context management, memory | VERIFIED | Compaction plus provenance, timestamps and revalidation of remembered observations. |
| 11–13 | Tools/MCP, skills, hooks | VERIFIED | Unit and integration tests; hooks gate parallel reads (F-004). |
| 14 | Permission/approval engine | VERIFIED | Rules, modes, confirmations; mutation score 95%. |
| 15–16 | Execution environment, sandbox | VERIFIED (local limits) | Job Object memory/process limits tested. No container sandbox since 2026-11-02 (ADR-007): commands run on the host in local mode only. Windows without Git Bash runs agent commands in PowerShell with translation (`windows_shell.py`, real-command tests under the default script policy). A command that changes Dikdik's own files stops the turn (F-018). |
| 17 | Repository/VCS manager | VERIFIED | Hook-free Git against local bare remotes. |
| 18–21 | Frontend/backend/DB/API engineering | VERIFIED (tools) | Preview + accessibility audit, quality and infra rules, load testing, performance budgets, migrations. Writing the product code itself is the model's job. |
| 22 | Distributed-systems verification | VERIFIED | `load_test`, `repeat_command`, and `fault_test` (latency, reset, close, partition, down between two real services, with recovery). Clock skew is not simulated. |
| 23–25 | Security system, SAST, DAST | VERIFIED | Scanner (secrets, AST/JS, SQL helpers, infra), OSV, CodeQL workflow, route-authorization test, and `dast_scan` against the running app on loopback. |
| 26 | Test generation/execution | VERIFIED | Local runners pass a suite only on exit 0 (unittest exits non-zero when it finds no tests), structured per-test results, coverage floor, mutation testing. The nonce receipt belonged to the removed Docker runner. |
| 27–28 | Browser automation, UI verification | VERIFIED | Real-Chromium preview, WCAG audit and visual regression against owner-accepted baselines (pixel count + SSIM, diff images). |
| 29–32 | Performance, reliability, a11y, i18n | VERIFIED | `measure_performance`, `load_test`, `repeat_command`, REL/I18N rules, accessibility audit, all gated. No soak or chaos testing. |
| 33 | Privacy/compliance | PARTIAL | PRIV rules, redacted logs and traces. Legal applicability (GDPR, Kenya DPA, ...) needs qualified human review by design. |
| 34 | Infra/DevOps | VERIFIED | INF rules for Dockerfiles, Compose, workflows; Dikdik's own CI. |
| 35 | Supply chain/licensing | VERIFIED | SBOM, licence report/deny, OSV, pip-audit, bun audit, pinned actions. Licences now come from installed metadata, Cargo sources and `node_modules` (F-019: before, only npm's package-lock was read, so every Dikdik dependency was "unknown" and `--deny AGPL` could never fire); 27 of 41 resolved here, the rest are packages CI installs before the SBOM step. |
| 36–37 | Change impact, test scheduling | VERIFIED | `impacted_tests` from the import graph; full suite before release. |
| 38 | Debugging/self-repair | VERIFIED | Bounded repair loop, recovery, stuck detection; `repeat_command` for flaky failures. |
| 39 | Observability/evidence | VERIFIED | SQLite event log, redacted logs, OTLP traces. |
| 40 | Quality firewall/release gate | VERIFIED | Tests, security, dynamic security, quality, accessibility, visual, performance, stability, resilience and requirements gates. |
| 41 | Documentation/handover | VERIFIED | Docs updated with each pass; numbers here are measured. |
| 42 | Benchmarking | VERIFIED (live) | Eight tasks with hidden oracles and reference solutions: four repairs, one parallel repair and three builds from an empty folder. Live, 3 runs each with GLM-5.2: 23 of 24 (96%); the failure exposed F-020, and todo-web then went 3 of 3. `--repeat` gives per-task rates; the harness keeps a tool timeline and fails any run that writes outside its workspace. |
| 43 | Cost/resource management | VERIFIED | Provider-reported usage/cost, local resource limits, load and performance budgets. |
| 44 | Disaster recovery/checkpoints | VERIFIED | Checkpoints, Undo/Redo, pre-migration backups, full data backup/verify/restore. |

## 5. What is left

These need something this repository cannot supply on its own:

1. **Wider live evidence** (costs money): every task has now run 3 times with one model (GLM-5.2), 96% then
   todo-web 3 of 3 after F-020. Other models, more runs and bigger apps would turn this into a broader rate.
2. **CI-only evidence**: CodeQL, Gitleaks, the suite on Linux/macOS and the weekly mutation runs. This machine has no
   Go, Gitleaks, CodeQL CLI or Linux distribution, and installing them is a download.
3. **Legal and compliance applicability** (row 33): needs qualified human review by design.

## 6. Verification

Windows 11, Python 3.13.6, Bun 1.4.0, cargo 1.95.0, Playwright 1.64.1 + Chromium headless shell (scratch venv).

- **Full Python suite** (`python -m unittest discover -s tests`): baseline on `70c2099` 875 run (868 passed,
  7 skipped); after pass 4 1028 run (1019 passed, 9 skipped); after pass 5 1069 run (1059 passed, 10 skipped);
  after Docker removal 1047 run (1039 passed, 8 skipped); after pass 6, under branch coverage with Playwright
  Chromium available, **1065 run: 1060 passed, 5 skipped, 0 failures; branch coverage 84%** (17,345 statements,
  7,198 branches; `windows_shell.py` 99%, `local_exec.py` 81%, `sbom.py` 85%). Pass 6 also: Bun 168 passed, strict
  typecheck clean, `app.js` up to date; Rust 16 passed; self-scan 0 critical, 0 high; mutation `windows_shell.py`
  24/24. After F-020: 1066 run, 1065 passed; the one failure was a test that assumed a command-created file was
  dropped (so no verification ran after it); it now asserts the file is part of the turn, and `test_local_mode` passes. The run before the last found a real
  defect in the pass-1 pairing fix (an oversized body was refused without being read, so on Windows the client
  could see a reset instead of the 400); fixed with `refuse()`. One pre-existing 1-second timing assertion in
  `tests/test_stop.py` failed once under full-suite load and passes in isolation and in every other run.
- **Same suite under branch coverage with desktop extras**: 980 run; 4 failures in `tests/test_browser.py`
  turned out to be **pre-existing** (they fail identically on an untouched export of `70c2099` when Playwright
  is installed, because the tests hid the host browser but not the interactive runtime); fixed, and the module
  now passes with and without Playwright.
- **Branch coverage: 83%** of `dikdik/` (15,727 statements, 6,496 branches); floor set to 80% in
  `pyproject.toml`. New modules: `sql.py` 100%, `accessibility.py` 97%, `engineering.py` 96%, `logs.py` 95%,
  `migrations.py` 93%, `control.py` 90%, `security_scan.py` 88%, `secrets_store.py` 86%.
- **Mutation testing** (`scripts/mutation_test.py`): `sql.py` 20/20 (100%); `engineering.py` 52/60 (87%,
  8 survivors reviewed as equivalent: schema metadata the agent does not enforce from the schema, a percentile
  clamp, `rsplit` maxsplit, a 5 MB bound); `control.py` 38/40 (95%, combining `test_control` and the new fast
  `test_control_rules`; 2 equivalent). The first `control.py` run scored 55% and showed that **nothing tested
  that an undecided confirmation denies the action** or that a blank secret is refused; both are now tested.
- **Real-browser accessibility**: `tests/test_accessibility.py` in Chromium — all 11 rules found on a broken page
  (contrast computed as 2.32:1), and a clean page passes the preview.
- **Security self-scan** (`python -m dikdik.security_scan . --deps --fail-on high`): exit 0; 363 files, 0 critical,
  0 high, 2 medium (test fixtures), 7 justified suppressions; 39 dependencies checked against OSV.
- pip-audit (desktop extras) and `bun audit`: no known vulnerabilities. Rust: 16 passed with PyO3 0.30.1.
- Web: typecheck clean, 168 passed, `app.js` rebuilt.
- **Pass 4 suites** (all passing): architecture 9, SBOM 3, backup 6, infrastructure rules 4 groups, load/repeat 7
  (real HTTP server), tracing 5 (real collector, engine turn), memory revalidation 3, benchmark 4 (oracles fail on
  fixtures, pass on solutions; harness through the real HTTP provider path against the stand-in), local limits 5
  (real 600 MB and 40-process commands).
- **Pass 5 suites** (all passing): parallel writers 14 (incl. an engine-level local turn with real tests), fault
  injection 6 (two real services), DAST 5 (vulnerable server and hardened twin), visual 11 (incl. real Chromium),
  wiring/permissions/routes 6. Mutation: `parallel.py` 51/54 (94%); the 3 survivors only change error-text
  truncation. The first run scored 69% and showed that the count limits were tested with overlapping paths (so
  overlap, not the limit, raised) and that `a.py` vs `a.pyc` was never tested; both fixed. Self-scan: 385 files,
  0 critical, 0 high. Web: typecheck clean, 168 passed, `app.js` rebuilt.
- **NOT_RUN here:** a live-model benchmark (needs your key, costs money), CodeQL, Gitleaks, Linux/macOS.
- **Docker removed (2026-11-02, ADR-007):** the container runners, `docker` verification, browser image, sandbox
  acceptance script and CI job are gone; local command output now redacts approved secrets (a real-command test).
