# Dikdik

**Small name. Serious intelligence.**

> **Current direction:** DIKDIK's core is independently engineered in Python,
> Bun/TypeScript and justified Rust components. OpenHands is a research and
> comparative-testing reference, **not a shipped code dependency or renamed fork**.
> Its UI and Python runtime are cloned separately under `upstream/`. See
> [current status](PROJECT_STATUS.md) and [source provenance](docs/UPSTREAM.md).
> The preview is a conversational coding agent with its own tools, sandbox,
> Git/apply workflow, skills, MCP servers, agent profiles and automations. It
> still covers only part of OpenHands (see [the parity audit](docs/PARITY.md)),
> and no run against a real model provider or a live Docker engine is recorded yet.

An evidence-first local engineering workspace. This is an original **0.1 developer
preview**, built from an empty directory—not an OpenHands fork or a complete
autonomous engineering platform.

## What works

- **Conversations.** Each task is a conversation: reply, ask follow-ups, queue a
  message while the agent works, stop and continue. Rename, pin, archive, search,
  delete and export tasks; every task has a link (`/?task=<id>`).
- **An agent with real tools.** It lists, reads, searches, edits and creates files,
  runs your tests and repairs failures within limits, or simply answers questions.
  With Docker approval it also gets a sandboxed shell (`run_command`).
- **A durable, read-only Terminal.** Agent commands append live and survive reloads.
  Authenticated task-scoped Bash endpoints can start, stop, inspect, page and clear
  commands only for tasks that already have explicit Docker execution approval.
- **Browser captures.** A separate browsing approval enables the Docker-only
  `browse_url` tool. Page text and authenticated screenshots appear in chat and the
  Browser tab; manual URL captures have their own approval. The optional browser
  image must be built by the operator. Docker capture is contract-tested, with
  live execution still blocked here. See [browser setup and limits](docs/BROWSER.md).
- **Planning and checklists.** Plan in a separate read-only conversation, review
  its private `PLAN.md`, then Build a chosen revision with fresh permissions.
  `/plan` and `/code` switch conversations; the agent's reported checklist lives
  in Task List. See [planning and Build](docs/PLANNING.md).
- **Composer drafts.** Home and each conversation keep unsent text across navigation
  and reload. Conflicting tabs offer an explicit choice; failed sends retain text.
  Drafts stay in your private server data. See [drafts](docs/COMPOSER.md).
  Drag the composer's top edge to resize; keyboard arrows resize and Home resets.
- **Run control.** Stop an active sandbox action and Resume from its recorded
  interruption, or Pause after the current step and Resume from the exact step; ask before
  actions (never, risky only, or every action) with Allow/Reject cards; block
  tools with `.dikdik/hooks.json`; let the agent delegate to read-only subagents
  (`.dikdik/agents/*.md`). Attach images and text files (+, paste or drop). Type
  `/` for commands: `/goal` (judged multi-turn loop), `/btw` (side question),
  `/model`, `/condense`, `/fork`, `/plan`, `/code` and your skills. Fork any
  conversation or branch from an earlier turn or message; a meter shows context use, and the
  Usage tab lists provider-reported tokens per turn (cost only when the provider
  reports it; never estimated).
- **Find and organise.** Ctrl+K opens a command menu for every action, panel,
  settings page and conversation. File conversations in folders, tag them, sort
  and filter the sidebar, and download a conversation as a .zip. Files has a
  tree (with image previews), pinned tabs that survive reloads and Markdown
  preview; Changes shows a diff, a side-by-side split or the new version; paths
  in replies open the file. Conversations get a short title after their first turn.
- **Models and keys.** Provider connections share one key across profiles;
  profiles can be renamed, duplicated, tuned and tested with one click; a router
  can send first messages to a strong model and follow-ups to a fast one, or let
  a classifier model pick from your table; the composer pill switches models,
  and Test explains failures in plain words (rejected key, out of credit…). Keys can be rotated with undo, renamed, and
  (only when a task allows it) exposed to sandbox commands, redacted from output.
- **Tests for several stacks** in network-less Docker containers: Python
  `unittest`, `bun test`, `npm test` and `cargo test --offline`, picked from the
  project's files. Without Docker, tasks are syntax-checked and labelled so.
- **Safe apply.** Work happens on an isolated copy. **Apply to workspace** writes
  the files only after you confirm, and refuses if you edited them meanwhile.
- **Files and Git.** Browse and read files (workspace or candidate version), see
  Git status and history, and commit what you applied, with repository hooks off.
- **Your models and keys.** Model profiles and encrypted API keys in Settings
  drive every call (OpenAI, Anthropic, Azure or any local OpenAI-compatible
  server); environment variables remain a fallback. Replies stream as they arrive.
- **Skills, MCP servers and agent profiles.** Skills (guidance files) reach the
  agent as marked, untrusted context; install them from a folder, a Git source
  or a marketplace you add, and browse, filter and switch them on the Skills page.
  MCP servers (HTTP or local command) add tools; browse the built-in server library,
  edit key mappings, test tools, and sign in to supported remote servers with OAuth.
  Agent profiles bundle instructions, a model, limits and tool access.
- **Plugins** bundle skills, block rules, subagents and MCP server definitions.
  Install from a folder, Git or a marketplace; nothing loads until you review
  what a plugin adds and trust it, and its MCP servers arrive switched off.
- **Automations** on daily, interval, cron or local Git-commit triggers. Search,
  filter and sort the dashboard; start from built-in templates; save/test drafts;
  import/export portable JSON; inspect every run. Sync definitions to a Git
  repository you pick inside the workspace: secret-free deterministic exports,
  a preview of what will change, hook-free commits and an explicit approval for
  every push. Runs use the approvals you set.
- **Python client and CLI** (`python -m dikdik.client`) and a native Rust
  repository indexer loaded automatically when built.
- Loopback-only server, automatic browser sign-in, SQLite history, crash recovery.

**Not yet:** extension apps,
WebSocket and TypeScript clients, a desktop app, and any recorded run against a
live model provider or Docker engine. See [the parity audit](docs/PARITY.md).

## Run without installation

Prerequisite: **Python 3.13+**. No runtime Python packages, Node/Bun, database
server, model key, or Docker are needed for the syntax-only workflow.

From this project directory:

```powershell
python -m dikdik.server --workspace examples/repair --data "$HOME/.dikdik-demo" --port 8787
```

`--version` and `--info` answer stand-alone before startup; a missing workspace, an
occupied port or a data directory already in use each stop with one plain line.

Your browser opens Dikdik and signs in automatically; there is no token to
copy. The terminal also prints a one-time sign-in link (`http://127.0.0.1:8787/#token=…`)
in case the browser didn't open, or for a second browser. After that the browser
stays signed in for 30 days, across server restarts. Pass `--no-browser` to skip
opening it, and `--rotate-token` to sign out every browser. Stop with Ctrl+C.

`--workspace` is the project approval boundary. `--data` must be a separate,
private directory, neither inside nor an ancestor of the workspace. Each data
directory is bound to one workspace and locked against concurrent services.
Do not share it with untrusted users. `--port` defaults to 8787. The service
always binds IPv4 loopback; it cannot be configured to listen publicly.

### Reproducible offline demo

1. Connect to the example project using the command above.
2. In the composer choose **Offline edits** and **Syntax check**. The edit field
   opens with this replacement prefilled:

   ```json
   [{"path":"calculator.py","before":"return a - b","after":"return a + b"}]
   ```

3. Type **Fix addition without changing the tests.** and press Enter.
4. Inspect the steps and the `-` to `+` diff in the **Changes** panel. The result
   says **Not test-verified**: syntax parsing is evidence, not a passing test suite.
5. The example stays broken until you click **Apply to workspace** and confirm.
   The candidate lives under `<data>/artifacts/<task-id>/`; history lives in SQLite.

To run real tests, start Docker Desktop's **Linux engine** and pull the images
for your stacks yourself, after reviewing their trust and license implications:

```powershell
docker pull python:3.13-slim
docker pull oven/bun:1
docker pull node:22-slim
docker pull rust:1-slim
```

Then choose **Docker tests** and tick **Run tools in Docker**. Dikdik never pulls
images, installs dependencies or uses the network inside containers, so projects
whose tests need packages that are not in the repository will fail to verify.
Python runs need at least one executed, non-skipped test.

If Docker is unavailable, the task is **blocked** and retains its candidate diff.
Do not interpret that as a test pass. A passing process is still not independent
proof that every natural-language acceptance criterion is satisfied.

### Optional model mode

**Set up a model in the app.** Click the model button in the composer (it reads
*Set up a model* until one works) or open **Settings**:

1. **API keys:** store your provider key under a name, e.g. `openai-key`. It is
   encrypted on this machine and never shown again.
2. **Models:** create a profile with the provider, model ID and that key name.
   The first profile becomes the default; switch defaults with *Edit*.

Every model and agent task uses the default profile. `openai` and `anthropic`
profiles use the providers' public chat-completions endpoints; `local` points at
a server on this machine (e.g. `http://127.0.0.1:11434/v1`) and needs no key;
`azure` needs an endpoint and sends the key as an `api-key` header. Endpoints
must be HTTPS, except plain HTTP to localhost. The key is decrypted only for the
call, kept in memory for that task, and never written to task history.

**Or use environment variables.** When no profile exists, the server falls back
to these. Set them on the server, **not** in source files or committed config:

| Variable | Meaning |
|---|---|
| `DIKDIK_MODEL_URL` | Exact HTTPS chat-completions endpoint, e.g. `https://your-provider/v1/chat/completions` |
| `DIKDIK_MODEL` | Your provider's model ID; must support instruction-following JSON responses |
| `DIKDIK_API_KEY` | Server-side API credential; do not print or share it |

Choose **Model proposal** and explicitly approve disclosure of the snapshot and
objective. This can incur provider charges. One request proposes existing-file
replacements, subject to the same validator and verification. No tools, streaming,
automatic retry, routing or repair are claimed. No request to a real provider
has been executed yet; the profile path is verified against a local stand-in
server. Context is capped at 128KB serialized UTF-8; output requests are capped
at 4096 tokens; network operations time out after the profile's timeout (default
60 seconds) or 30 seconds for environment configuration. Cancelling a model call is cooperative, not an
immediate network abort. Ambient provider proxy configuration is trusted.

### The agent

Choose **Agent** and tick **Share code with model**. It needs a model that supports
chat-completions function tools. With **Docker tests** (and **Run tools in Docker**)
it also verifies with your test suites and can use the sandboxed shell; with
**Syntax check** it works without Docker and never claims tests passed.

The agent can list/read/search files, make exact edits, create files in approved
paths, run commands and tests, repair failures within limits, call MCP tools, and
reply in plain text when no change is needed. Finishing a change requires net
changes plus a verified current candidate. Edits invalidate earlier verification;
an unchanged failing candidate cannot be blindly rerun. Follow-up messages continue
from the previous turn's files and conversation.
The sandbox runner also requires a nonce-tagged completed-run receipt and nonzero
executed test counts; exit code zero alone is insufficient. This is not protection
against deliberately malicious tests forging or subverting their own harness.

Default limits: 12 model calls, 40 tool calls, 3 repair edits, 32000 reported tokens,
180-second between-step deadline. Tool-mode output requests cap at 2048 tokens.
Usage may be absent; no exact cost cap is claimed. No OpenHands code or process
is used underneath this agent. See [agent contract and limitations](docs/ORIGINAL_AGENT.md).

Real-provider demonstration (may incur charges; **not executed here**):

```powershell
python scripts/demo_agent.py --approve-external --approve-execution
```

### Apply, Files and Git

Open the right-hand panel with **Changes** or the folder button. **Apply to
workspace** asks once more, then writes only the candidate's changed files; if any
of them changed on disk since the agent read them, nothing is written and the
conflicting files are listed. The **Git** tab shows the branch, changed files and
recent commits, and commits the files you tick (repository hooks are not run).

### Skills, MCP servers, agent profiles and automations

All four live in **Settings**. Skills are Markdown files in `.dikdik/skills/` (this
project) or the data folder (personal); `<name>/SKILL.md` folders with `name:` and
`description:` front matter work too. MCP servers start disabled: install one from
the built-in library or add it manually, test it, then enable it for agent tasks.
A *local command* server runs on this machine with a minimal environment plus only
the encrypted API keys you map to it. Supported remote servers use authorization-code
OAuth with S256 PKCE and a loopback callback; access and refresh tokens stay in the
encrypted key store and are not listed under API keys. Agent profiles appear in the
composer in Agent mode. Automations run daily, every N minutes, on a five-field cron
schedule in an IANA time zone, or after a local Git revision changes. Their templates,
drafts, import/export and bounded run logs are under Automations; imported definitions
start disabled. Older automations made without approvals wait for an edit.

**Automation Git sync** writes every automation to one managed directory of a Git
repository in this workspace (default `.dikdik/automations`). Exports are
deterministic and can never contain secrets. **Preview** lists the pending
changed and deleted files; **Sync now** writes them, stages only that directory
and commits without running any Git hook. Pushing to the remote happens only
when you separately tick the push approval for that one sync, and the tick is
never remembered. If the directory holds files Dikdik did not export, sync
refuses instead of overwriting them.

### Python client and command line

```powershell
python -m dikdik.client --data "$HOME/.dikdik-demo" ask "Fix addition" --share-code
python -m dikdik.client --data "$HOME/.dikdik-demo" tasks
python -m dikdik.client --data "$HOME/.dikdik-demo" apply <task-id> --yes
```

`from dikdik.client import DikdikClient` gives `submit`, `send`, `events` (live),
`wait`, `apply`, `git`, `commit` and more. The token comes from `--data`/`data=`,
`--token`, or `DIKDIK_TOKEN`.

### Native indexer

`python scripts/build_native.py` builds `crates/dikdik-index` (Rust) and places it
in the package; the server then reports **Indexer: Rust** in Settings → General.
Its output is checked against the Python rules, and any disagreement falls back
to the Python walk.

## Development and evaluation

```powershell
python -m unittest discover -s tests -v
bun install --frozen-lockfile --ignore-scripts
bun run typecheck:web
bun run test:web
bun run build:web
python scripts/evaluate.py --output docs/evaluation-results.json
python scripts/benchmark.py --samples 20 --output docs/benchmark-results.json
```

Current result (2026-10-27): **269 Python tests passed, 2 live-runtime checks skipped**
(271 total); **62 Bun tests passed**; **15 Rust tests passed**; strict frontend type
checking and build passed; deterministic syntax-only evaluation **4/4**. These are preview regression results, not proof of
general autonomous engineering capability.

Bun 1.4.0 is the tested frontend development tool; TypeScript is dev-only. The
generated browser bundle is included, so Bun is not required to run the backend.
The Python test command
uses no third-party test framework. Unit/contract tests use mocks for the optional
provider and Docker, while workflow and HTTP tests exercise actual files, SQLite,
threads and loopback requests. The live Docker security test is explicitly skipped
unless requested:

```powershell
$env:DIKDIK_RUN_DOCKER = "1"
python -m unittest tests.test_sandbox.LiveDockerTests -v
Remove-Item Env:DIKDIK_RUN_DOCKER
python scripts/evaluate.py --docker
```

The `--docker` evaluation flag explicitly authorizes test execution in restricted
containers. A missing runtime makes this evaluation **fail**, never pass by default.
The evaluation cases are deterministic orchestration checks, not a benchmark of
autonomous model problem-solving. See [measured results](docs/VERIFICATION.md).

Optional installation: `python -m pip install .` provides the `dikdik` console
command. It may resolve setuptools as a build dependency; source startup needs
no installation. Package discovery is restricted to `dikdik` so reference upstream
checkouts and frontend development dependencies cannot be bundled as packages.

## Troubleshooting

- **Sign-in page appears:** open the sign-in link printed in the terminal (needed once per browser, or after `--rotate-token`).
- **403:** use `127.0.0.1` and the correct port. Cross-origin access is refused.
- **Data already in use:** stop the other service. Do not delete a live lock file.
- **Different workspace:** use a new private `--data` directory.
- **Docker blocked:** check `docker version`, Linux-engine availability, and the
  locally installed image. No automatic daemon startup or image pull occurs.
- **No tests / skipped tests / missing dependencies:** runner returns non-success;
  inspect output rather than weakening tests. Python unittest discovery also
  follows its normal package-directory discovery conventions.
- **Replacement rejected:** `before` must match exactly once. No new files,
  test-file modifications, ignored paths, or path traversal are allowed.
- **Process interruption:** restart with the same workspace/data. Stale tasks are
  marked interrupted; approved stale Docker containers get a bounded cleanup
  attempt. Re-run as a new task. Exact resume is not implemented.
- **Cleanup warning:** inspect/remove the named `dikdik-<task-id>` container
  manually after restoring Docker. State cannot roll back filesystem artifacts.

## Engineering records

[Project status](PROJECT_STATUS.md) · [Initial audit](docs/AUDIT.md) ·
[Architecture/contracts](docs/ARCHITECTURE.md) · [Decisions](docs/DECISIONS.md) ·
[Security](SECURITY.md) · [Verification](docs/VERIFICATION.md) ·
[License status](LICENSE-STATUS.md)

**Release gates:** owner-selected license, real Docker boundary tests, live model
integration, immutable image pinning/review, stronger resource/secret controls,
broader end-to-end evaluations, and an independent security review. This preview
must not be exposed to a network or used with hostile repositories or secrets.
