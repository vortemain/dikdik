# Handoff: where Dikdik stands and what to do next

**Read this first.** It is kept up to date after every phase so any agent can take
over mid-way. Newer than `docs/CODEX_HANDOFF.md` and `docs/NEXT_PHASES_PROMPT.md`
(both are history now); where they differ, this file wins.

- **Owner:** Elijah Mwai Kibuchi. **Repository:** `C:\Projects\Dikdik` (Windows 11,
  Git Bash and PowerShell, Python 3.13, Bun 1.4, Rust).
- **Working branch:** `claude/verify-requirements-testing-588525`, a worktree at
  `C:\Projects\Dikdik\.claude\worktrees\verify-requirements-testing-588525`.
  `main` was last fast-forwarded to `e9d5fcb`. Never push or open PRs unless the
  owner asks; merging into `main` locally only when the owner asks.
- **Last updated:** 2026-10-28, after phase 9 (projects in view, provider catalog, Git accounts, VS Code-style files).

## 1. What Dikdik is now (owner decisions, 2026-10-28)

The owner decided Dikdik should work **like Claude Code / OpenCode**, locally first,
also able to run in the cloud later, and start projects from a folder or a remote
repository. Concretely, already built:

1. **Projects.** `dikdik` (or `python -m dikdik.server`) opens the folder it is
   started in; `dikdik C:\path` opens another. In the app, the project name at the
   bottom of the sidebar opens **Projects**: recent list, **Open folder** (typed path
   or Browse…, which shows this computer's own folder picker via PowerShell /
   osascript / zenity), **New project** (optional `git init`), **Clone repository**
   (https/ssh/git@/file URLs; hooks off, no submodules, uses the computer's own Git
   sign-in, no token stored). One server serves one project at a time and switches
   in place (`dikdik/projects.py`, `_Live`/`_Wiring` proxies in `server.py`).
   All projects share `~/.dikdik`: keys, model profiles, MCP, skills, settings.
   Conversations carry a `workspace` column; a store bound to a folder only sees
   that folder's tasks (`Store._mine`, `_owned`), so a task can never be applied
   into another project. Old single-folder data directories are migrated.
2. **Local mode (default).** Composer picker: **Ask before commands** (default),
   **Auto**, **Plan only**, **Classic (copy, then Apply)**. Ask/Auto send
   `mode: agent, verification: "local", approve_external, approve_execution,
   confirmation: "ask"|"risky"`. The agent edits the real folder and runs commands
   on this computer (`dikdik/local_exec.py`: Git Bash on Windows if installed, else
   PowerShell; time limit, output cap, process-tree kill). Tests run locally too.
   Policy `ask` (`control.needs_approval`) asks for run_command, browse_url, MCP.
   **Always allow** remembers an exact command per project (`/api/commands/allowed`).
3. **One trust question per project** (`/api/projects/trust`, `engine.trusted()`),
   asked on the first send; the hidden approval boxes are filled from it.
4. **Direct edits with checkpoints** (`dikdik/direct.py`): writes only if the file
   still holds what the agent last saw; a checkpoint per turn; **Undo changes**
   (`POST /api/tasks/<id>/undo`) restores unless the user edited since. Apply is
   refused for direct turns. `SECURITY.md` "Local mode" explains the trade-off.

Also done earlier this session: verification fixes (10 defects incl. two data-loss
bugs), ACP agents run in a throwaway copy, sandboxed extension apps, real backend
health checks, first live model run (OpenRouter `z-ai/glm-5.2`, $0.0028).

## 2. Phase status

| # | Phase | Status | Commit |
|---|---|---|---|
| 1 | Projects: open/new/clone/recent, `dikdik` in any folder | Done, browser-checked | `4d1add4` |
| 2 | Run on this computer, permission modes, Always allow | Done, browser-checked with stand-in model | `4d1add4` |
| 3 | One-time trust, simpler composer | Done, browser-checked | `4d1add4` |
| 4 | Direct edits, checkpoints, Undo | Done, browser-checked | `4d1add4` |
| 5 | Code colouring (`web/highlight.ts`) + files view (type badges, indent guides, breadcrumb) + Open in VS Code (`/api/files/open-in-editor`) | Done, browser-checked (Open in VS Code tested with a mocked launch only) | `f39da64` |
| 6 | Git: remote status, fetch, pull (ff-only), push (approval per push), branch create/switch, Create PR via `gh` (`gitops.remote_info/fetch/pull/push/switch_branch/create_pr`, `/api/git/*`) | Done, browser-checked against a local bare remote; Create PR tested with `gh` mocked | `f39da64` |
| 7 | Browser: computer's Edge/Chrome when Docker is not ready (`host_browser.py`: fresh profile, public-only proxy), panel redesigned like a browser (tab, Back/Forward/Reload, address bar, padlock, history) | Done; live capture of example.com/.org through the proxy; browser-checked | `b9f2802` |
| 9 | Projects in view (sidebar picker, top-bar project button, start cards on the empty chat, your GitHub repos in Clone); Models as an OpenRouter-style provider catalog (`model_catalog.py`: 18 providers, Connect with a key, live model lists with context and price, one-click Use, custom OpenAI-compatible providers; old connections/profiles/routers under Advanced); Settings → Git (`git_accounts.py`: gh/glab status, browser sign-in with code, `gh auth setup-git`, repo list); Files like VS Code (explorer + editor side by side, folder and file icons, tabs, gutter, status bar, language/stack detection) | Done, browser-checked; GitHub sign-in button not pressed (it starts a real login) | see `git log` |
| 8 | On a server: `--listen/--public-url/--tls-cert/--tls-key/--behind-proxy/--projects-root` (`remote.py`), `Dockerfile`, `docs/CLOUD.md` | Done; HTTPS, host/origin, Secure cookie, sign-in limit tested on loopback; Dockerfile not built (no Docker engine) | `b9f2802` |

Update this table (and section 1 if behaviour changed) at the end of every phase.

## 3. Design notes (all built; kept for reference and follow-ups)

Possible next steps, none started: a sidebar entry for extension apps; switching
backends inside one tab; MCP sign-in callbacks on a remote server; a hosted
multi-user Dikdik Cloud (accounts, per-user data) once the owner decides hosting;
the remaining OpenHands parity rows in `docs/parity-audit.csv`.

**5. Code and files.** No syntax colouring exists anywhere (code is plain
`textContent`; `codeBlock()` in `app.ts` ~line 134 builds every code block). The web
UI must stay free of runtime npm dependencies, so write a small original tokenizer
module (`dikdik/web/highlight.ts`) for Python, TypeScript/JavaScript, JSON, shell,
Rust, HTML/CSS, YAML, Markdown and diff, producing `<span class="tok-…">` via safe
DOM nodes (never innerHTML with file text). Colours as CSS tokens for light and dark.
Use it in chat code blocks, the file preview and the diff view. Files view: file-type
icons, indent guides, line numbers, a path bar, find-in-file, and **Open in VS Code**
(`POST /api/files/open-in-editor {path}` running `code -g <file>` on this computer;
only for paths inside the project; refuse if `code` is not on PATH).

**6. Git without tokens.** `dikdik/gitops.py` has status/history/commit only (hooks
off via `core.hooksPath`, `--no-verify`). Add pull (`--ff-only`), push (needs a
separate explicit approval per push), branch create/switch, and Create PR via the
`gh` CLI when it is installed and signed in (`gh pr create --fill`), all using the
computer's own credentials (Git Credential Manager / SSH); keep
`GIT_TERMINAL_PROMPT=0` and never store a token. Show remote, ahead/behind and the
sign-in state in the Git tab.

**7. Browser panel.** The tool (`dikdik/browser_service.py`) captures pages in
Docker and shows screenshots. Redesign the panel like a browser: address bar with
padlock, back/forward/reload, page title, loading bar, larger viewport, visit
history. A live clickable view needs Docker or a host browser via the Chrome
DevTools Protocol; running a host browser is a safety decision for the owner.

**8. Cloud.** Seams exist: dormant start + `/api/init`, remote backends, API keys
with scopes, CORS opt-in. Missing: binding to a non-loopback address behind an
explicit flag with TLS/reverse-proxy guidance, a login suitable for the internet,
per-user data. Ask the owner how "Dikdik Cloud" should be hosted before building.

## 4. How to work here

- **Run:** `pip install -e .` then `dikdik` inside a project, or
  `python -m dikdik.server --workspace examples/repair --data "$HOME/.dikdik-demo"`.
- **Tests:** `python -m unittest discover -s tests` (~6–10 min, 558 tests, 4 skipped
  live gates; `DIKDIK_RUN_HOST_BROWSER=1` adds a real page load); `bun run typecheck:web`, `bun run test:web`, `bun run build:web`
  (commit the rebuilt `dikdik/web/app.js`); `cargo test --workspace`.
- **Model for browser checks:** `python scripts/stub_model.py --port 8899 --key sk-local-test`,
  then save a secret `stub-key` = `sk-local-test` and a profile
  `{provider: local, endpoint: http://127.0.0.1:8899/v1, model: stub-model,
  credential_secret_ref: stub-key}` via `/api/secrets` and `/api/profiles`. The
  objective word `FIX` scripts edit → run_command → run_tests → answer. Report
  stand-in checks as stand-in checks, never as live-provider runs.
- **Browser checks:** start a scratch server with its own `--data` in the
  scratchpad, read the token from its startup line, open `/#token=…`. The in-app
  browser pane blocks iframes (`ERR_BLOCKED_BY_CLIENT`) and is narrow; set a 1280×800
  viewport to see the sidebar.
- **Gotchas:** in tests register `server_close` before `shutdown` (cleanups run in
  reverse); write Python patches as script files (shell heredocs mangle `\\` and
  `$`); keep files UTF-8 without BOM and LF; never print secrets or session tokens.
- **Rules kept:** loopback only; secrets never in events/logs/API; untrusted text
  stays data; Git hooks off; no telemetry, CDNs or runtime npm dependencies; honest
  labels for anything not verified live.

## 5. Not verified live (still true)

Docker engine (not running on this machine), a real ACP agent CLI (Codex is
installed but not `codex-acp`), ChatGPT sign-in (not implemented: no real OpenAI
device flow), VS Code in Docker, any provider other than the one OpenRouter run.
