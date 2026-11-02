# Changelog

## Unreleased

Providers and model screens:
- Nebius shows its real logo, and xAI its own mark instead of the Grok logo ("xAI models").
- Provider keys, Nebius included, are saved encrypted again and stay connected after a restart
  (since v0.3.2 every new key lived only in memory for up to 8 hours). Disconnect deletes the record.
- Removed the invite-only sponsored gateway (`dikdik.sponsored_gateway`, its tests and deployment
  runbook). Everyone uses their own provider key.
- Provider cards no longer cut titles or descriptions off: long names and descriptions wrap inside
  the card. The sidebar's sort, time and status filters fit on one row (Recent / Any time / All).
- Connecting a model is one focused screen: choosing a provider replaces the grid with its page
  (back link, key box first with the cursor in it, then its models), so nothing opens below the
  fold. Once connected the key folds away, "In use" marks the active model, connected providers come
  first, and a provider without a model list leads with "type the model name". Web research moved
  below the providers as optional.
- Calmer chat page: the home screen is the greeting, four one-line suggestions and the message box
  (the project cards, "Get started" checklist and repeated project line are gone); the permanent hint
  under the message box moved into the mode selector's tooltip; turn statistics show on hover; the
  duplicate project block left the sidebar ("Rescan project files" is in Ctrl K); list filters
  appear once there are six conversations or a filter is set.

Docker removed (owner decision, ADR-007):
- Dikdik no longer uses Docker. The `docker` verification mode, container test and shell runners,
  the `dikdik-browser:1` image, the Docker GitHub MCP preset, the sandbox acceptance script and the
  CI `sandbox` job are gone. Requests asking for `docker` are refused with the alternatives.
- Tests and commands run only in local mode, on this computer. Classic mode syntax-checks an
  isolated candidate and runs nothing. Scheduled runs are syntax-only; saved Docker runs migrate to
  that. The CLI's `--run-tests` now means local mode.
- Approved secrets are now redacted from local command output too.
- The terminal runs only for local-mode tasks; browser captures use Playwright's Chromium or the
  computer's own Edge/Chrome.

- Fix: the SBOM licence gate never saw a licence for Dikdik's own dependencies (only npm's package-lock was
  read). Licences now come from installed Python metadata, Cargo's crate sources and `node_modules`, and a denied
  id inside an SPDX expression is caught.

- Fix: a file the agent's own command created (for example a new test) was refused and then treated as a
  protected existing file, jamming every later command with "changed outside the agent".
- Benchmark: `--repeat N` with per-task success rates and cost, a `parallel-fix` task for `parallel_edit`, and
  `tools_used` per run. Live, GLM-5.2: 23 of 24 runs succeeded before that fix; todo-web 3 of 3 after it.

Fixes found by the first live build-from-scratch run:
- `preview_app`, `load_test`, `dast_scan` and `fault_test` crashed in real tasks ("'_Either' object has no
  attribute 'wait'") whenever the project's server was not ready instantly.
- An agent that rewrites Dikdik's own program files now has its turn stopped (`blocked`), and the agent is told
  to change files only inside the project.

Benchmark:
- Three build-from-scratch tasks (`notes-api`, `todo-cli`, `todo-web`) start from an empty folder and are
  judged only by hidden checks: HTTP behaviour and persistence, CLI output and exit codes, and the web app
  in real Chromium. UI tasks give the agent `preview_app`.

Zero-setup on Windows:
- Commands work without Git Bash: PowerShell runs agent commands with `&&`/`||`, `/dev/null`,
  `$NAME` secrets, `rm -rf`, `mkdir -p`, `ls -la`, `touch`, `export`, `which`, `head`/`tail`
  translated, and the real exit code (it used to report every failure as 1). The agent is told
  which shell it has.
- `npm`, `npx` and other `.ps1` shims run under Windows' default script policy.
- Python output in commands and tests is UTF-8 (printing ✓ no longer crashes in a pipe), and
  Dikdik's own Python is used when PATH has none or only the Microsoft Store stub.

Security:
- Fix: `POST /api/pair` minted a pairing code without authentication or a Host check, and
  redeeming it returned the owner token. Minting now needs the signed-in owner (not an API
  key); redeeming needs the exact Host/Origin, counts toward the sign-in lockout, and five
  wrong codes revoke all outstanding codes.
- Upgrade PyO3 0.23.5 → 0.30.1 in the Rust indexer (GHSA-36hh-v3qg-5jq4, RUSTSEC-2025-0020,
  RUSTSEC-2026-0177).
- Secret store format v2 (separate keys, random nonce per value); v1 values are upgraded in place.
- Parallel reads now pass `tool.before` hooks and confirmation before they run, once each.

Platform capabilities:
- Architecture map (`python -m dikdik.architecture`, `architecture_map`): import graph, cycles, Mermaid; change
  impact (`impacted_tests`, `--changed`); numbered ADRs (`record_decision`).
- SBOM (`python -m dikdik.sbom`, CycloneDX 1.5) with licence report and `--deny`.
- Data backup/verify/restore (`python -m dikdik.backup`).
- Infrastructure rules for Dockerfiles, Compose files and GitHub workflows.
- `load_test` and `repeat_command` agent tools; stability gate.
- OpenTelemetry traces over OTLP/HTTP (`DIKDIK_OTEL_ENDPOINT`).
- Remembered observations carry provenance and timestamps and are revalidated on resume.
- Task-success benchmark (`scripts/benchmark_tasks.py`) with hidden oracles and reference solutions.
- Windows memory and process limits for local commands (`local_limits`, defaults 8 GB / 1,024).
- Environment-configured models accept plain HTTP on loopback (local Ollama, LM Studio).
- Fix: the log redactor masked token-usage counts such as `input_tokens`.
- `parallel_edit`: concurrent writer agents with disjoint file ownership, merged by ownership and verified once.
- `dast_scan`: dynamic security checks of the running app on loopback (headers, cookies, CORS, reflected input,
  SQL/stack-trace disclosure, open redirects, exposed `.git`/`.env`); `dynamic_security` gate.
- `fault_test`: dependency fault injection (latency, reset, close, partition, down) with recovery checks; `resilience` gate.
- Visual regression in `preview_app` against owner-accepted baselines, with diff images; `visual` gate and an
  Accept button on the gate.

Fixes found by the new checks:
- Provider failover never ran for the default profile (its lookup bound no parameter and the
  error was silently swallowed) and could use the default profile's fallback for a chosen one.
- Silent `except: pass` paths in background work (scheduler, warming, webhooks, event purge,
  API-key bookkeeping) now write to the diagnostics log; `taskkill` calls have timeouts.
- SQL is built only through allowlisting helpers (`dikdik/sql.py`); 26 formatted statements
  replaced, and PY008 is now a blocking HIGH. Every module's tables are schema-versioned.

Engineering:
- `quality_check` (reliability, i18n, privacy, static HTML accessibility), an in-browser WCAG 2.2
  audit in `preview_app`, and `measure_performance` (budgets, baselines, regressions); the release
  gate adds quality, accessibility and performance gates.
- Requirement evidence is parsed per test from unittest/pytest/cargo/go/bun output and JUnit XML;
  local unittest runs with `-v` and bun writes JUnit, so passing tests are reported by name.
- CodeQL (security-extended, Python and TypeScript), a real-Chromium accessibility job, branch
  coverage with a floor, and weekly mutation testing (`scripts/mutation_test.py`).
- `record_requirements`, `security_scan` and `release_gate` agent tools; completed turns carry
  a computed `release_gate`, and a turn that adds a critical finding cannot finish.
- `python -m dikdik.security_scan`: secrets, Python AST and JS/TS sink rules, OSV lockfile audit.
- Versioned, atomic schema migrations with a pre-upgrade backup and newer-schema refusal.
- Redacted JSON-lines diagnostics log.
- CI: security workflow (scanner, pip-audit, bun audit, Gitleaks) and a live Docker sandbox
  acceptance job; route-authorization test over every API path.

## 0.3.1 — 2026-10-30

- Default the composer to Auto, migrating the old implicit Ask selection once while
  preserving later explicit permission choices. Auto runs authorized tools without
  per-tool prompts; explicit permission rules remain in force.
- Recover repeated completion claims by running the host verifier through the
  normal tool path. Shell test output alone cannot establish verified completion.
- Clarify that the agent must call `run_tests` after its final edit and stop
  repeating checks once the current files have passed verification.
- Verify a completion reply immediately without extra model reminders. Reuse
  successful verification until an edit or command invalidates it, and use small
  revision-based loop signatures instead of serializing the project per tool.
- Retry brief Windows sharing locks during atomic transcript saves so a transient
  file lock does not turn a finished task into a checkpoint failure.
- Own executable work instead of handing commands back to the user, verify test-only
  requests without artificial edits, and reject redundant permission questions in Auto.
- Add supervised local UI preview, desktop/mobile measurements, interaction assertions,
  saved screenshots and cleanup. Design/build requests include declared static checks
  alongside tests, and completion requires current browser evidence when applicable.

## 0.2.0 — 2026-10-29

Desktop developer preview and combined agent update.

- Native desktop launcher, branded Windows/macOS icons and installer builds for
  Windows, macOS and Linux, with bundled Python and Chromium.
- Native task notifications, privacy controls, sounds and click-to-open task
  navigation; Windows Start menu registration enables notification banners.
- Animated Dikdik opening mark and subtle binary background, honoring reduced motion.
- Persistent interactive browser sessions with page inputs, actions and real web
  fetching, alongside the existing browser permission boundaries.
- Faster event rendering, task navigation, repository indexing and message updates.
- Colored terminal output, readable commands and improved process cancellation.
- Combined agent work on planning, context continuity, provider recovery, controls,
  local verification and ACP integration.
- Versioned desktop downloads and complete SHA-256 checksums in the release workflow.

Desktop installers remain unsigned developer previews. Windows source-desktop
notifications and browser interactions were exercised locally; macOS and Linux
require native installation and interaction validation.

## 0.1.0 — 2026-10-29

First GitHub developer-preview release.

- Original local-first coding workspace with Ask, Auto, Plan and Classic modes,
  durable conversations, checkpoints, Undo, review and Git operations.
- 34 bundled agent skills and six typed lifecycle events with ordered hooks,
  explicit decisions, timeouts and correlated execution records. Plugins cannot
  override runtime security policies or required approvals.
- Provider error categories, bounded response recovery, non-streaming fallback,
  adaptive output budgets and reasoning continuation support.
- Context compaction that retains requirements, plans and useful findings;
  resumed verification retains change and Undo evidence.
- Local test/static-check discovery and explicit unavailable-verification status.
- Readable Markdown, tables and charts, isolated HTML previews, themed dialogs
  and improved workspace file browsing.
- MIT licensing, third-party notices, portable Python distributions, SHA-256
  checksums and a tested release workflow.
- Windows time-zone data is declared as a dependency, and project boundaries
  normalize short/long path aliases before comparisons.

The Python indexer is included in the portable wheel. The optional Rust
accelerator can be built separately from the source distribution. Docker images,
ACP CLIs, model accounts and external tools are not included. Production security
review, broad live-provider evaluation and live Docker boundary verification
remain open; this release is a developer preview.
