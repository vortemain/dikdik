# Handoff: where Dikdik stands and what to do next

**Read this first.** It is kept up to date after every phase so any agent can take
over mid-way. Newer than `docs/CODEX_HANDOFF.md` and `docs/NEXT_PHASES_PROMPT.md`
(both are history now); where they differ, this file wins.

- **Owner:** Elijah Mwai Kibuchi. **Repository:** `C:\Projects\Dikdik` (Windows 11,
  Git Bash and PowerShell, Python 3.13, Bun 1.4, Rust).
- **Current checkout:** `main` at `C:\Projects\Dikdik`, started at `343c601`.
  Phase 10–12 changes are committed locally as `feat: polish workspace UI, model discovery and folder selection`.
  Never push or open PRs unless the
  owner asks; merging into `main` locally only when the owner asks.
- **Last updated:** 2026-10-28, phase 12 (working folder chooser and Grok product logo).

## 1. What Dikdik is now (owner decisions, 2026-10-28)

The owner decided Dikdik should work **like Claude Code / OpenCode**, locally first,
also able to run in the cloud later, and start projects from a folder or a remote
repository. Concretely, already built:

1. **Projects.** `dikdik` (or `python -m dikdik.server`) opens the folder it is
   started in; `dikdik C:\path` opens another. In the app, the project name at the
   bottom of the sidebar opens **Projects**: recent list, **Open folder** (typed path
   or Browse…, which opens Dikdik's folder chooser with Home, drives and folder
   navigation), **New project** (optional `git init`), **Clone repository**
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
| 9 | Projects in view (sidebar picker, top-bar project button, start cards on the empty chat, your GitHub repos in Clone); Models as an OpenRouter-style provider catalog (`model_catalog.py`: 18 providers, Connect with a key, live model lists with context and price, one-click Use, custom OpenAI-compatible providers; old connections/profiles/routers under Advanced); Settings → Git (`git_accounts.py`: gh/glab status, browser sign-in with code, `gh auth setup-git`, repo list); Files like VS Code (explorer + editor side by side, folder and file icons, tabs, gutter, status bar, language/stack detection) | Done, browser-checked; GitHub sign-in button not pressed (it starts a real login) | `cda78f8` |
| 8 | On a server: `--listen/--public-url/--tls-cert/--tls-key/--behind-proxy/--projects-root` (`remote.py`), `Dockerfile`, `docs/CLOUD.md` | Done; HTTPS, host/origin, Secure cookie, sign-in limit tested on loopback; Dockerfile not built (no Docker engine) | `b9f2802` |
| 10 | Shared SVG file/folder symbols throughout explorer, tabs, chat links, tool/code headers and attachments; actual marks for all 18 providers and endpoint-aware model picker; Microsoft stable blue VS Code SVG on editor actions/settings/toasts; 193 bundled syntax grammars and a language-mode selector; broader Python/Rust text indexing; resizable side-by-side explorer with fixed 27px rows and editor tabs/gutter; theme-aware manual local terminal with command entry, streamed output, history, Stop, clear/copy and folder selection; browser history/text/zoom, standalone workspace tools, bounded loading and process-pipe fix | Implemented; browser checked in light/dark; real local terminal command and real Edge public HTTPS capture passed. See phase 10 notes below for tests/limits. | This commit |

| 11 | Searchable chat model picker with saved models/provider drill-down, full paginated provider directories and explicit connection/error states; redesigned credential settings with provider logos, masked rows and folded Manage controls; selected-model status follows chat override; no-key local connection fix | Implemented and browser checked; 70 model/credential tests, HTTP regression suite and 123 web tests. Authenticated cloud lists require the owner's provider keys; see discovery notes. | This commit |

| 12 | Reliable in-app local folder chooser for Open/New/Clone; Open folder start card launches it directly; Grok product symbol replaces the xAI company mark throughout provider/model UI | Browser verified navigation, selection, cancel, invalid paths and actual disposable project creation. Project/remote tests: 17 passed, 1 skipped; web: 123 passed; typecheck/build passed. | This commit |

Update this table (and section 1 if behaviour changed) at the end of every phase.

### Phase 12 notes

- Native Windows `FolderBrowserDialog` calls hung without a visible window in the
  current background launch environment, including a direct dialog without an
  owner. Default browsing now uses `web/folder-picker.ts` and authenticated
  `GET /api/projects/folders`, independent of a desktop session. No native process
  is launched when the user presses Browse. The legacy `/api/projects/pick` API
  remains but now reports process failures separately from cancellation and
  escapes titles correctly.
- The chooser lists only folders, supports Home/drives, parent navigation and a
  typed path, and returns the chosen path to the originating field. Open folder
  on the start screen or Projects tab launches it immediately. Cancel/Escape preserve existing
  input. Choosing a location alone does not switch projects or create anything.
  Lists are capped at 1,000 directories with an explicit notice; users can enter
  a path directly. Remote deployments cannot use this whole-computer listing;
  their typed paths remain constrained by the existing project-root policy.
- `assets/providers/grok.svg` comes unmodified from pinned Lobe Icons 1.95.1;
  `providerSymbol("xai")` now maps to it. Groq remains its separate provider.
  The bundler retains the Grok symbol on future asset refreshes.
- Browser verification used only `build/model-ui-smoke/` and its disposable
  shared data. It successfully created/opened `folder-chooser-proof` under the
  chosen parent; the 390px narrow layout was also checked. User projects and credentials were not changed. Screenshots:
  `build/ui-proof/project-folder-chooser.jpg`, `project-folder-created.jpg`,
  and `grok-provider-logo.jpg`.

### Phase 11 notes

- Read [MODEL_DISCOVERY.md](MODEL_DISCOVERY.md) for endpoints, authentication,
  pagination, verification and the distinction between suggestions and live lists.
- `model_discovery.py` provides bounded native/compatible directory fetching;
  `web/model-picker.ts`, `web/keys-pane.ts` and `web/model-settings.css` hold the new UI.
  Catalog now includes 19 entries: Perplexity Orchestrator is separate from legacy
  Sonar, which has no directory. No new runtime dependencies.
- Chat selection uses `/use` with `default:false`; `/api/model?profile=<id>`
  describes that choice without changing the global default. Overrides remain
  tab-scoped. Local connected loopback models can omit a key; explicit missing
  key references still fail clearly.
- API key UI was checked with synthetic disposable credentials, including add,
  rename and replace. User credentials were not modified. Light/dark screenshots
  are in ignored `build/ui-proof/model-*.jpg` and `api-keys-*.jpg`.
- Public live metadata probes succeeded for OpenRouter (464) and Cerebras (2);
  later OpenRouter requests timed out and the UI retry state was verified.
  Contract tests cover provider-specific authentication and pagination; do not
  claim real authenticated access was verified for every provider.

### Phase 10 notes

- **Owner's follow-up:** terminal must look like an integrated editor terminal and
  use Dikdik's actual light/dark/system theme. Files must use a proper explorer
  beside the editor with spacing, and every VS Code action must carry its actual
  logo. `web/workbench.css` supplies these workspace-specific layout rules after
  the main stylesheet. Explorer width is draggable/keyboard-resizable and remembered;
  its toggle gives the editor the full panel width.
- **Icons:** `web/icons.ts` resolves the pinned Material Icon Theme associations,
  including compound extensions and known filenames, across every file surface.
  `scripts/bundle_editor_assets.py` / `bun run assets:web` regenerates the theme and
  provider assets. `web/assets/NOTICES.md` records versions, origins and licenses.
  Provider identity comes from the serving connection endpoint, not model-name guesses.
- **Languages:** Highlight.js 11.12.0 bundles 193 grammars, offline. Its custom
  emitter makes text tokens, never source HTML. Copying retains original source;
  unknown/oversized input stays plain. This does **not** add 193 language servers,
  compilers, debuggers or guaranteed semantic support. Executing/testing other
  languages uses the project's installed tools.
- **Indexing:** Python and Rust accept many more language extensions plus common
  extensionless files (Dockerfile, Makefile, etc.). Native binaries must expose
  `TEXT_POLICY_VERSION = 2`; old installed binaries fall back to Python until
  `python scripts/build_native.py` is run. `.dikdikignore` has conservative ignore
  rules independent of Git, used here to omit the generated browser bundle,
  icon assets/manifest and large parity CSV from bounded model snapshots.
- **Terminal:** `/api/terminal/sessions` creates workspace tools without a model
  call. Manual local commands require explicit execution consent and project trust.
  Existing task terminals retain their approved local/Docker runtime. Output is
  streamed and durable; Stop kills the process tree; unfinished commands become
  interrupted after a restart. ANSI control sequences are stripped safely for display.
  This is a **command terminal, not a persistent PTY**: each command has a fresh
  shell; full-screen TUIs, interactive stdin and persistent `cd`/environment are
  not implemented. Use the Folder field or combine shell commands when needed.
- **Browser:** public URL capture remains a **snapshot preview**. Back/Forward
  select captured history, Page text shows that capture's text, Fit/zoom controls
  the screenshot, and Open in your browser enables real links/forms. Live
  interactive embedding/local development targets remain unimplemented. Fixed a
  stdout-pipe deadlock in host-browser DOM extraction, added Chromium's bounded
  page-load capture timeout, and closed cancelled-process pipes. A real Edge
  HTTPS capture of example.com returned its actual title/text and a PNG; the UI
  capture, Page text and zoom were also checked. No certificate checks were bypassed.
- **Verification:** 122 web tests pass, strict TypeScript/build pass; all 16 Rust
  behavior tests pass. Real local-terminal integration
  covers early streaming, process-tree stop, Unicode, cwd boundaries, trust,
  restart and failed launches. Full Python regression: **575 run, OK, 5 skipped**;
  the last terminal/browser feature run also passed (13 run, 1 skipped). The full
  Windows suite still emits existing socket-cleanup/thread teardown warnings.
  Initial tests run under a temp folder inside this Git checkout
  produced misleading Git-fixture failures; use the OS temp directory for tests.
  UI proof screenshots are under `build/ui-proof/` (scratch, not versioned).

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
