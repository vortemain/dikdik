# Security model — local developer preview

## Local mode (the default since 2026-10-28)

The owner chose to make Dikdik work like Claude Code: in a project you trust, the
agent edits your real files and runs commands **on this computer**, with your own
tools and environment. Dikdik has no container sandbox: the Docker runner was removed
on 2026-11-02 at the owner's request, and nothing in Dikdik calls Docker. This
deliberately relaxes the earlier rule that no repository code runs on the host. What
protects you instead:

- **One trust decision per project.** The first message in a folder asks whether
  Dikdik may read it, send what it reads to your model provider, edit it and run
  commands there. Each request still carries explicit `approve_external` and
  `approve_execution` Booleans; the interface fills them only for a trusted folder.
- **Permission modes.** *Ask* asks before commands, data-sending browser actions and MCP calls;
  *Auto* executes the requested work without per-tool confirmation prompts in a
  trusted project. Explicit allow/deny/ask rules remain authoritative. *Plan* only reads.
  *Always allow* remembers one exact command for one
  project; the list is shown and can be cleared (`/api/commands/allowed`).
- **Local UI verification.** `preview_app` starts a supervised project server on
  an assigned ephemeral loopback port. A dedicated browser proxy maps only the
  reserved preview hostname to that port. Other loopback, LAN and metadata URLs
  remain denied; ordinary public browsing receives no local-access exception.
  The preview browser uses a fresh profile and stops with its server after the check.
- **Checkpoints and Undo.** Before a turn first changes a file, its previous content
  is recorded; Undo restores every file the turn changed, including files its
  commands changed, unless you changed it since. Writes never overwrite a file that
  changed outside the agent after it last looked.
- **Limits that still apply:** original tests are protected (a command that edits
  them is undone), `.git` is never written, commands have a time limit and an
  output cap, and Stop ends the whole process tree.
- **What it cannot protect against:** a command you allow runs with your user's
  rights and can reach the network and any file you can. Use *Classic* mode (an
  isolated copy, syntax checks only, no commands, Apply to write) for repositories
  you do not trust; there is no way to run an untrusted repository's code isolated.

## The computer's own browser

When Playwright's Chromium is not installed, page captures use the Edge or Chrome on
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

The local operator, host OS, Python installation and private data directory are
trusted. Repository contents, objectives, model
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
- **Stored secrets** use format v2 (`dikdik/secrets_store.py`): separate encryption and MAC
  keys derived from the master key, an HMAC-SHA256 keystream over a random 16-byte nonce per
  sealed value, and encrypt-then-MAC. Restoring an old copy of the store can no longer reuse a
  keystream. Values sealed by earlier releases are authenticated and re-sealed as v2 when the
  engine starts; a value that fails authentication stops the upgrade with the file unchanged.
  It remains a standard-library construction, not a reviewed AEAD library.
- **Diagnostics logs** (`<data>/logs/dikdik.jsonl`) mask credential shapes, the session token
  and any field named like a secret before writing. Treat them as private anyway.
- **Backups** (`python -m dikdik.backup`) contain everything in the data directory, including sealed
  keys and the sign-in token; store them like the directory itself. Restore refuses while Dikdik runs.
- **Traces** (`DIKDIK_OTEL_ENDPOINT`) export only span names, durations, counts and outcomes, never tool
  arguments, file contents or model text; plain HTTP is accepted only for a loopback collector.
- **Local resource limits:** each local command tree runs in a Windows Job Object capped at 8 GB committed
  memory and 1,024 processes by default (`local_limits`); this bounds runaway commands, it is not isolation.
- **Load tests, DAST and fault tests** reach only the project's own servers on Dikdik-assigned loopback ports.
  `dast_scan` sends GET requests only (POST forms are listed, never submitted) and its reflection probe is an
  inert marker, not script; links to other origins are never followed. The `fault_test` proxy listens on
  127.0.0.1 only. Both are confirmed like `run_command`, and a permission rule must allow both of a fault
  test's commands, while a deny rule matching either one refuses it.
- **Visual baselines** are accepted only by the owner (`POST /api/visual-baselines/accept`); the agent can
  record a pending capture but never change the accepted look.
- **Parallel writers** (`parallel_edit`) edit only the files they own, cannot run commands, and each edit
  passes the same permission checks as `edit_file`; a writer that trespasses or does not finish is not merged.
- **SQL construction:** values are always `?` parameters; identifiers and fixed fragments go
  through `dikdik/sql.py` allowlists only. CodeQL (security-extended) adds data-flow analysis in CI.
- **Security scanning:** `python -m dikdik.security_scan [--deps]` and the agent's
  `security_scan` tool apply the same deterministic secret, code-pattern and OSV dependency
  rules; a turn that introduces a critical finding cannot finish. CI runs the scanner, pip-audit,
  bun audit and Gitleaks on every push (`.github/workflows/security.yml`), and a test asserts
  every API route refuses anonymous and foreign-Host requests (`tests/test_route_authorization.py`).
  Pattern rules are not proof of security.
- **Device pairing** (`POST /api/pair`) is a second way to hand out that secret, so
  only the signed-in owner can mint a code (API keys cannot, even with `admin`).
  A code is 8 hex characters, single use, valid 10 minutes, at most five at once.
  Redeeming it (`POST /api/pair/verify`) needs the same exact Host/Origin as every
  other route and counts toward the server sign-in lockout; five wrong codes revoke
  every outstanding code.
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
- **The agent's shell** (`run_command`) exists only in local mode with execution
  approval, in a trusted project. It runs in the project folder on this computer with
  the user's rights, under the permission mode, a time limit, an output cap and
  process-tree supervision. Classic mode offers no shell. Original tests stay
  protected and new files need an approved path.
- **The task Bash API and Terminal** run only for a local-mode task whose latest
  persisted turn has literal Boolean execution approval, in a trusted project. There
  is no global shell route. Commands accept a 1–300 second timeout and a safe relative
  working directory inside the project, and run there on this computer. Output is
  capped at 2 MiB and persisted in SQLite, so it can contain repository or
  command-produced secrets. Treat the private data directory as sensitive and clear
  history when appropriate.
- **MCP servers** are added and enabled by the user. HTTP servers must use HTTPS
  (or plain HTTP on loopback), with redirects refused. *Local command* servers run
  on the host by design, like any MCP client: they get a minimal environment plus
  the single stored key the user assigns, never the server's environment or other
  secrets. MCP tool output is treated as data. Each agent task connects to enabled
  servers only and stops command servers when the task ends.
- **Browser captures** require separate literal Boolean browsing and execution
  approvals. They use Playwright's Chromium when installed, otherwise the computer's
  own Edge or Chrome, always with a fresh empty profile and no workspace access.
  Every request goes through Dikdik's public-only proxy: each DNS answer and redirect
  is checked and local, private and special-use targets are refused. See
  `docs/BROWSER.md` for limits.
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
- Every task that runs commands requires Boolean execution approval; every external
  model task requires separate Boolean disclosure approval. Strings do not grant
  consent. Requests that still ask for `docker` verification are refused.
- **Automations** never run commands: scheduled runs are syntax-checked candidates,
  and saved runs that used the removed Docker verification were migrated to that.
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
go to a separate candidate directory in Classic mode. Local Agent mode
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

## Execution boundary

There is no container or VM boundary. Repository tests and commands run only in
local mode, on this computer, in the project folder, with the user's rights
(`dikdik/local_exec.py`):

- Only after the project is trusted and the task carries literal Boolean execution
  approval; the permission mode decides which commands ask first.
- A time limit, an output cap and supervision of the whole process tree, so Stop and
  timeouts end every child. On Windows a Job Object also bounds committed memory and
  live processes; on macOS/Linux only an explicitly configured memory limit applies.
- Approved secrets reach the command as environment variables; their values are
  replaced in streamed and stored output.
- The shell is Git Bash when installed, otherwise PowerShell 7 or Windows PowerShell.
  For PowerShell, Dikdik translates `&&`/`||`, `/dev/null` and `$NAME` for approved
  variables, adds stand-ins for common POSIX commands, keeps the real exit code, and
  starts it with `-ExecutionPolicy Bypass` for that process only (Windows' default
  policy otherwise blocks `npm.ps1`); no system or user policy is changed.
- A suite passes only when its command exits 0; Python's unittest exits non-zero when
  it found no tests.

Classic mode runs no repository code at all: it syntax-checks an isolated candidate.
Tests can lie, bypass their own assertions, or forge runtime signals; process success
is not proof against malicious test code or satisfaction of the objective. Do not
run code from a repository you do not trust.

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
requires disclosure approval, plus execution approval for local execution,
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
