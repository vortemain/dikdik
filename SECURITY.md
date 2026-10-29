# Security model — local developer preview

## Local mode (the default since 2026-10-28)

The owner chose to make Dikdik work like Claude Code: in a project you trust, the
agent edits your real files and runs commands **on this computer, outside Docker**,
with your own tools and environment. This deliberately relaxes the earlier rule that
no repository code runs on the host. What protects you instead:

- **One trust decision per project.** The first message in a folder asks whether
  Dikdik may read it, send what it reads to your model provider, edit it and run
  commands there. Each request still carries explicit `approve_external` and
  `approve_execution` Booleans; the interface fills them only for a trusted folder.
- **Permission modes.** *Ask* asks before every command, web visit and MCP call;
  *Auto* asks only for high-risk commands (deleting, installing, network, rewriting
  history); *Plan* only reads. *Always allow* remembers one exact command for one
  project; the list is shown and can be cleared (`/api/commands/allowed`).
- **Checkpoints and Undo.** Before a turn first changes a file, its previous content
  is recorded; Undo restores every file the turn changed, including files its
  commands changed, unless you changed it since. Writes never overwrite a file that
  changed outside the agent after it last looked.
- **Limits that still apply:** original tests are protected (a command that edits
  them is undone), `.git` is never written, commands have a time limit and an
  output cap, and Stop ends the whole process tree.
- **What it cannot protect against:** a command you allow runs with your user's
  rights and can reach the network and any file you can. Use *Classic* mode (an
  isolated copy, Docker for tests, Apply to write) for repositories you do not trust.

## The computer's own browser

When Docker's browser image is not ready, page captures use the Edge or Chrome on
this computer: headless, a new empty profile per capture (no cookies, logins or
extensions), deleted afterwards. The browser is pointed at a proxy inside Dikdik that
resolves every host itself and connects only to public addresses on ports 443
(CONNECT) and 80 (GET/HEAD); localhost, private, link-local and special-use targets,
including redirects and page resources, are refused. Page text and screenshots are
untrusted data, as before.

## On a server

See `docs/CLOUD.md`. Listening beyond loopback needs `--listen` plus `--public-url`,
HTTPS (built in or `--behind-proxy`), and then only that host/origin and 127.0.0.1 are
accepted; cookies are `Secure`; ten failed sign-ins per address lock it for ten
minutes; `--projects-root` confines projects; the folder picker and Open in VS Code
are disabled. It is single-owner: anyone with the sign-in link has full control.

## Trust and permissions (Classic mode)

The local operator, host OS, Python installation, Docker CLI/daemon/image and
private data directory are trusted. Repository contents, objectives, model
proposals and tool output are untrusted. An objective cannot change policy.

- Opening a project (startup folder, Open folder, New project, Clone) approves read
  access to that folder; switching closes the previous project's engine.
- API calls require a random session secret and exact loopback Host. Browser
  Origins must match exactly. There is no CORS opt-in.
- The secret lives in `<data>/web.token` (created with owner-only permissions) and
  is printed only to the operator's terminal, inside the URL *fragment* of the
  sign-in link, which browsers never send to a server. The page exchanges it once
  via `POST /api/session` for an `HttpOnly; SameSite=Strict` cookie scoped to the
  port, then removes it from the address bar. Scripts may still send
  `Authorization: Bearer <secret>`. `--rotate-token` invalidates every session.
  There is no query-string token, JavaScript-readable token storage, access
  logging or analytics.
- Provider keys stored under Settings → API keys are decrypted in-process only
  when the default profile's model is called (`SecretsStore.reveal`, which no
  route exposes). The key lives in memory for that task and is never persisted
  in task events, results or logs; `/api/model` reports the profile, provider,
  model and host only.
- **Writing to the workspace** happens only through *Apply to workspace* (or the
  client's `apply`), which requires `confirm: true`, writes only the task's changed
  files inside the approved root, and refuses the whole apply if any of them no
  longer matches the content the candidate was built from.
- **Git** runs with fixed arguments, no shell, a timeout, no pager or prompts,
  `core.fsmonitor=false`, and an empty hooks directory plus `--no-verify`, so a
  commit made from Dikdik never runs repository hooks.
- **The agent's shell** (`run_command`) exists only when Docker execution is
  approved. It runs on a throwaway copy of the candidate in the same restricted
  container profile as tests (no network, read-only root, no capabilities, user
  65534, bounded memory/PIDs/time, an in-container `timeout`). Text files it changes
  are kept only under the edit rules: original tests stay protected and new files
  need an approved path.
- **The task Bash API and read-only Terminal** reuse that Docker-only boundary.
  There is no global or host-shell route. Every command is scoped to an existing
  task whose latest persisted turn has literal Boolean execution approval, uses a fresh
  throwaway copy, receives no ambient host environment, and cannot write to the
  approved workspace. Commands accept only a supported image, a 1–300 second
  timeout and a safe relative working directory. Output is capped at 2 MiB and
  persisted in SQLite, so it can contain repository or command-produced secrets.
  Treat the private data directory as sensitive and clear history when appropriate.
- **MCP servers** are added and enabled by the user. HTTP servers must use HTTPS
  (or plain HTTP on loopback), with redirects refused. *Local command* servers run
  on the host by design, like any MCP client: they get a minimal environment plus
  the single stored key the user assigns, never the server's environment or other
  secrets. MCP tool output is treated as data. Each agent task connects to enabled
  servers only and stops command servers when the task ends.
- **Browser captures** require separate literal Boolean browsing and Docker
  approvals. Chromium runs only in an optional operator-built container, with no
  network, no workspace mount and the restricted Docker profile. A bounded trusted
  standard-library relay performs public HTTP/HTTPS GET requests: every DNS answer
  and redirect is checked, sockets are pinned to public addresses, and no host
  cookies, keys, proxy settings or authorization are forwarded. Forms, uploads,
  login, local targets and WebSockets are unsupported. The worker's Chromium
  sandbox is disabled under this profile, so the outer Docker boundary is
  essential. See `docs/BROWSER.md` for limits and the missing live-security gate.
- Browser screenshots are authenticated task-scoped PNG responses, stored in the
  private SQLite database with bounded retention. Task deletion removes their rows
  atomically with task events. URLs, screenshots and page text may themselves be
  sensitive. Network responses and page text remain untrusted data.
- **Planning** uses a separate conversation with only bounded read-only repository
  tools, a private plan writer and reported progress. Shell, tests, browser, MCP
  and candidate edits are absent from its tool schema. Build requires the reviewed
  planner identity and revision plus new code-turn approvals; neither plan text,
  planner permissions, profiles nor skills can grant execution or disclosure.
  The handed-off plan is immutable untrusted data. Stale/deleted planner identities
  and duplicate builds are refused. Plans never write to the source workspace.
- Execution/disclosure/browser approval Booleans are stored atomically in the
  existing `running` event for each turn. Task APIs use those persisted approvals,
  with old-data fallback to the original request, rather than borrowing grants
  from a related conversation. Checklist completion never establishes verification.
- Composer drafts are bounded plain text in the private workspace data database,
  scoped to Home or an existing conversation. Authenticated saves cannot submit
  tasks, grant approvals, contact a provider or write workspace files. Revision
  checks prevent stale tabs overwriting or resurrecting cleared drafts. Draft text
  may contain sensitive material and is not encrypted; protect the data directory.
  Conversation deletion removes its draft. Failed saves retain text in memory and
  warn before leaving; no browser-local plaintext fallback is stored.
- **Skills** reach the model inside an untrusted-data marker and cannot change
  tools, approvals or limits. **Agent profiles** can only narrow tool access.
- **Automations** store the mode and approvals the user chose; a schedule never
  grants consent. Automations created before this rule stay paused until edited.
- Every Docker task requires Boolean execution approval; every external model
  task requires separate Boolean disclosure approval. Strings do not grant consent.
- There is no global command API, host shell, source-write endpoint, deployment,
  installation, Git hook execution, production DB access or external publication.

## Filesystem and indexing

The inventory excludes hidden paths, common generated/dependency directories,
credential-like names, symlinks, junctions, non-UTF-8 content, binaries and files
outside a small text-extension allowlist. Workspace ancestors cannot be links or
junctions. Simple inherited `.gitignore` patterns are supported conservatively:
negations never re-include excluded content; this is **not** full Git semantics.
Discovery inspects at most 10,000 entries at depth 40. The initial text cache holds
at most 1000 files, 256KB/file and 8MB total; additional files are deferred rather
than blocking project opening. In Agent and Plan modes, filesystem tools discover,
read and search supported files beyond that cache. Reads page by line and column,
including long minified lines, with explicit continuation positions; searches
stream bounded fragments and return match positions and snippets. Tool results
are bounded to fit the model conversation. A file's entire contents are not
automatically injected into a prompt. Reads/searches have time and cancellation
budgets, with continuation metadata when incomplete.

Agent edits can materialize deferred text files within a 64MB/file and 64MB total
working-state budget (10,000 files). Larger files remain readable/searchable;
editing them requires a streaming command under the usual command-approval
policy. Existing ignore, credential-path, link and protected-test policies apply.
Classic single-proposal mode retains its bounded snapshot/edit limits.

Large files also remain listed in the explorer; authenticated read-only previews
use the same policy and read at most 4MB. The editor bounds rendered characters
and lines, labels shortened previews and offers Open in VS Code. Partial server
previews cannot be copied as whole files. No repository scripts execute during
inspection. Large agent diffs have a labelled shortened UI preview; the full
patch is retained in the task artifact and authenticated diff download.

Exact edits can address supported existing workspace text files. Test-named paths are
protected heuristically (`test*`, test directories, `.test.`, `.spec.`, conftest).
This is not a semantic proof that an edit cannot affect test behavior. Changes
go to a separate candidate directory in Classic/Docker mode. Local Agent mode
writes the source workspace with conflict checks and Undo checkpoints, including
deferred files materialized for editing (see Local mode above).

**Residual risks:** filename filtering cannot detect secrets embedded in ordinary
source or documentation. No arbitrary-content secret redactor is claimed. Output,
diffs and SQLite may contain source and test-generated sensitive data; do not use
secret-bearing projects. Concurrent local mutation can race path checks or create
an inconsistent snapshot. These Python path checks are not a kernel-enforced
filesystem sandbox. Hostile local users or actively mutating repositories are
outside the supported threat model. Keep data directories private with OS ACLs;
new POSIX data directories request mode 0700, but Windows/existing-directory
permissions must be managed by the operator. Disk retention/quota/secure deletion
and encrypted state storage are not implemented.

## Actual execution boundary

Repository tests run only through a fixed Docker invocation:

- Preloaded Linux `python:3.13-slim`, no automatic pull or dependency installation.
- Non-root UID/GID 65534; no capabilities; no-new-privileges.
- No network, read-only root filesystem and read-only candidate mount.
- No original repository, credentials, Docker socket or host-root mount.
- 256MB memory (no extra swap), one CPU, 64 PIDs, 128 open-file limit.
- 16MB noexec/nosuid temporary filesystem, disabled persistent container logs.
- 64KiB captured output limit, 60-second host monitor; in-container supervisor
  imposes a 55-second test-subprocess timeout even if the service disappears.
- Cancellation/timeout attempts container removal by recorded task-derived name,
  then terminates the Docker client. Killing a client alone is not cleanup.
- Startup attempts cleanup of previously approved stale Docker tasks and records
  warnings; exact restart/resume is not claimed. Cleanup failure requires operator
  action. Docker service outages can prevent proof of removal.

The host CLI is invoked with fixed argv and `shell=False`, not a repository shell.
No environment secrets are forwarded to the container. Docker daemon settings
and CLI lookup remain trusted host configuration. The image tag is mutable:
immutable digest pinning, vulnerability scans and distribution license review
are release gates, not completed security claims.

**Live container boundary tests could not run in this environment.** The Docker
daemon was unavailable. Command-construction and mocked cleanup tests are not
proof of actual isolation. Containers share a kernel and do not eliminate kernel
or daemon escapes. For actively malicious repositories, evaluate microVMs or an
equivalent stronger boundary before enabling execution. Tests can lie, bypass
their own assertions, or forge runtime signals; process success is not proof against
malicious test code or satisfaction of the objective. The runner now requires a
current-run nonce-tagged completion receipt, discovered==executed>0 and zero
failure/error/skip/expected-failure/unexpected-success counts in addition to exit 0.
This rejects accidental `os._exit(0)` during discovery with no completed tests.
It is **not cryptographic attestation**: repository code executes in the test
interpreter and can deliberately inspect or subvert that interpreter/harness.
Independent trusted acceptance checks outside that trust domain remain a future
requirement for stronger adversarial assurance.

## Model boundary

Only an explicitly configured HTTPS endpoint is supported. Redirects, URL
credentials/query/fragment, automatic retries and model-selected commands are
disallowed. TLS uses Python's default certificate verification. Keys live only
in server environment and outbound authorization, never a frontend setting.
Only allowlisted integer usage counters are persisted from provider metadata.
Network/HTTP error text is suppressed to avoid leaking auth/configuration.
Repository data is explicitly delimited as untrusted in prompts, but prompt text
alone is **not** a prompt-injection defense: deterministic edit validation, protected
source and test execution policy remain authoritative. A malicious proposal may
still introduce harmful code into a candidate; human review is mandatory.

Socket timeouts are not hard overall request deadlines; slow-drip providers and
proxy behavior can extend a call. Cancellation cannot instantly abort a blocking
provider operation. There is no pricing enforcement or reliable cost estimate.
No real-provider privacy/security test has been run.

## Original agent loop

Tool-using mode is original DIKDIK code, not an imported OpenHands runtime. It
requires disclosure approval, plus execution approval for local/Docker execution,
and exposes policy-checked workspace tools and the fixed test runner. Existing exact-edit restrictions
remain authoritative. A model cannot add a shell command argument to `run_tests`.
Every edit invalidates prior verification; finish requires net edits and current
test success. There is no cumulative token ceiling or implicit task-wide call,
time or repair cutoff for the main agent. Explicit profile call/time limits are
enforced when selected. Working context is compacted, and per-response/output,
file-policy, command-timeout and repeated-failure checks still apply. Token usage
is reporting only; absent or dishonest provider metadata prevents exact usage or
cost reporting. Read-only delegated investigations retain separate call/tool/time
bounds and have no token cap. Event
persistence failure stops further work. Cancellation acceptance and terminal commit
are serialized so accepted finalization-time cancellation cannot become success.
Model and verifier callbacks remain cooperative rather than instantly abortable.

## Service boundary and release limitations

The standard-library HTTP server is loopback-only and intended for one trusted
operator. It is not hardened against local denial-of-service, header slowloris,
unbounded concurrent TCP clients, local account compromise, or a stolen bearer
session secret or cookie. Do not reverse proxy it, share sign-in links, expose it publicly, or use it as a
multi-tenant service. Browser rendering uses textContent, not repository HTML;
static responses include CSP, no-store and nosniff headers.

Report defects privately to the project owner; no public security-reporting
address has been established. Independent security review remains required.
