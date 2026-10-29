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
- **Last updated:** 2026-10-28, phase 17 (long tasks without cumulative token cutoffs).
  Phase 13–17 changes are in the working tree, uncommitted; phase 10–12 is `16336d4`.

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

| 13 | NVIDIA public catalog and default Lightning preset, official NVIDIA SVG, logos on banner/model suggestions, Dikdik logo on model controls; replaces every native JS prompt/confirm with themed async forms and confirmations | Implemented; 44 model/credential tests and 125 web tests, typecheck/build pass. Live public NVIDIA directory returned 85 including the preset; real authenticated inference needs the owner's key. | Working tree |

| 14 | Large text assets no longer abort project indexing; explorer includes oversized files, bounded source preview with explicit context/truncation notices, exact file copying and bounded editor rendering | Browser opened a project containing a 312KB `client/public/Cover.svg`, then opened its source. 65 Python checks passed, 1 skipped; HTTP suite passed separately after a transient 3-second timeout. 126 web tests, typecheck/build passed. | Working tree |

| 15 | Filesystem-backed Agent/Plan read/search/discovery beyond the initial cache; line+column paging for minified assets; targeted large-file edits with conflict checks, Undo/resume; full diff downloads and labelled shortened previews | 107 Python checks: 106 passed, 1 skipped. 126 web tests, typecheck/build passed. Scripted model edited a 600KB SVG after reading beyond byte 600,000; real local tests, Undo, resume, conflict refusal and complete patch retention verified. | Working tree |

| 16 | Folder-aware file listing and actionable schema errors; bounded recovery from repeated failures; larger default run budget; automatic working-context trimming; saved-progress pauses and valid interrupted-batch resume | 144 Python tests passed (35 agent/recovery, 58 local/control/planning/provider/usage, 50 HTTP/engine/conversation, 1 additional verified-local-resume regression). 126 web tests and typecheck/build passed. Browser fixture recovered after two errors, completed at 48,000 reported tokens with real local tests, and verified the saved-progress pause; no paid inference. | Working tree |

| 17 | Removes token enforcement completely; default main-agent calls/tools/time/repairs are unlimited, with automatic context refresh and optional explicit profile limits | Agent/recovery regressions: 38 passed, including 75 model calls, 219 tools and 5,040,000 reported tokens with context refresh, simulated two-hour model call, progressive repairs beyond the old 3-repair cap and explicit-limit resume. Integration/web verification recorded below. | Working tree |

Update this table (and section 1 if behaviour changed) at the end of every phase.

### Phase 17 notes

- The owner explicitly rejected phase 16's higher token cap. `max_total_tokens`
  is removed from AgentBudget and all parent/helper enforcement. Usage counters
  and cost telemetry remain cumulative, with no control-flow effect.
- Default main-agent `max_model_calls`, `max_tool_calls`, `max_repair_attempts`
  and `deadline_seconds` are `None`. A long task continues until completion,
  user Stop/Pause, a genuine execution/provider/verification problem, or the
  existing no-progress recovery policy. There is no default 60-call, 200-tool,
  30-minute or 3-repair substitute for the removed token limit.
- Explicit profile call/time limits still pause with a saved transcript. Null
  profile fields inherit no limit. Settings labels mark these fields optional
  and show **No limit** when blank. Delegated read-only investigations still
  have their separate 6-call/20-tool/120-second bounds, without token enforcement.
- Per-request working context/output and per-command limits remain distinct
  from task duration. Existing context compaction keeps the objective, edit and
  verification facts, and recent complete tool groups. Full file access and
  history stay available. This is not a claim about unlimited provider context
  windows or complete product parity with any other coding agent.
- Regression log: `build/long-task-agent-tests.txt` (38 passed). Synthetic usage
  verifies behavior without paid external inference.

### Phase 16 notes

- Historical default ceilings/token enforcement below are superseded by phase 17.
- Read-only inspection of the owner's failed task events confirmed `list_files`
  calls with `path: "client/src"` were rejected as invalid keys, and cumulative
  provider totals exhausted the old 32,000-token ceiling after 5–6 calls. Usage
  accounting itself was correct; cumulative billing totals are not context size.
- `list_files` now accepts a relative folder path (or `.`), query and paginated
  offset/limit. Schema errors list missing/unexpected/allowed fields. Missing
  reads tell the model to list the containing folder and use an exact returned
  path; directory reads point to `list_files`.
- Default AgentBudget is 60 model calls, 200 tool calls, 1,000,000 reported total
  tokens, 1800 seconds. Custom profile call/time limits remain respected. The
  existing 3-repair limit after actual test failures remains. Usage/cost reporting
  remains cumulative and honest; a final paid response is processed before a
  token cap prevents the next request.
  Read-only helpers retain their 6-call/20-tool/120-second bounds but allow
  250,000 reported tokens, replacing the equally restrictive 16,000 ceiling.
- At 75% of the 128KiB working prompt, deterministic compaction removes older
  observations in complete tool groups, retaining the system rules, original
  objective, exact file/verification state and recent groups. Files and full
  SQLite events remain available. This is not an LLM summary or extra API call.
- Two identical failures add specific change-approach feedback. The same failed
  action is not executed a third/fourth time without intervening progress; four
  failures pause with the cause instead of the old generic task failure. A
  successful different action permits fresh attempts. Successful action loops
  also get recovery guidance before a persistent loop pauses.
- Run/time/tool/token limits save a transcript and produce `paused` with
  `pause_reason`; the UI shows **Progress saved** and Resume, not Task failed.
  Unfinished tool batches are closed with explicit interrupted results, so
  Resume neither submits orphan calls nor blindly replays a completed edit.
  Finishing a resumed local turn recognizes its saved pre-edit state even when
  the source folder already contains its verified edits, avoiding a spurious
  "Completion requires net candidate changes" error. Its real local regression
  is in `build/agent-recovery-verified-resume-test.txt`.
  This does not promise that any provider/model will always solve a request.
- Regression fixtures/logs: `tests/test_agent_recovery.py`,
  `build/agent-recovery-initial-tests.txt`,
  `build/agent-recovery-integration-tests.txt`,
  `build/agent-recovery-web-tests.txt`. Disposable browser fixture:
  `build/agent-recovery-ui-smoke/` via `build/agent_recovery_smoke.py`.
  Screenshot: `build/ui-proof/agent-tool-recovery.jpg`.

### Phase 15 notes

- The owner rejected phase 14's model-access exclusion. `inventory()` is now an
  initial cache, not the scope of Agent/Plan filesystem access. Files exceeding
  256KB, the 8MB cache budget or 1000 cached files remain discoverable via
  `WorkspaceFiles`; `large_files` metadata includes files deferred for any of
  those reasons. Root discovery still has 10,000-entry / depth-40 guards.
- `workspace_files.py` streams reads/searches without loading a whole minified
  line. `read_file` accepts `start_line`, `start_column`, `max_lines`, `max_chars`
  and supplies explicit continuation positions. `search_text` returns line and
  column with a bounded snippet around the actual match and paginated results.
  Model output/context budgets still apply; entire projects are not automatically
  inserted into a prompt. Existing ignore/secret/link policies remain in force.
- Agent exact edits lazily adopt an existing deferred file into the original and
  candidate states. Materialized working state is bounded to 64MB/file and 64MB
  total, 10,000 files. Larger-file reads/searches remain available; streaming
  commands use existing command approval. Protected tests and external-change
  checks remain enforced. Classic single-proposal mode keeps its older limits.
- Local edits register original bytes with DirectWrites before modifying the
  source. Pause/resume persists adopted originals; local verification and shell
  synchronization include tracked deferred files. Tests cover Undo after an
  edit and after resume, and refusal to overwrite an external change.
- Agent diffs above 1MB no longer fail a valid edit. Full patches live at
  `data/diffs/<task>.patch`, are included as `changes.patch` in the task archive
  and available through authenticated `GET /api/tasks/<id>/diff`. UI previews
  label omissions, hide partial Copy actions and link the complete patch.
  Apply still uses complete candidate files and original hashes, independent of
  the preview. Direct local turns show Undo rather than the Classic Apply bar.
- Industry references for bounded, on-demand file access:
  [Claude Code tools](https://code.claude.com/docs/en/tools-reference) and
  [OpenCode tools](https://dev.opencode.ai/v2/docs/tools/). This is not a claim
  of complete product parity. Verification used a scripted tool-calling model;
  no paid external inference was made for this phase.
- Disposable browser fixture: `build/model-workspace-ui-smoke/`; screenshot:
  `build/ui-proof/model-large-file-access.jpg`. Regression log:
  `build/workspace-context-regression-tests.txt` (107 run, 1 skipped).
- Final focused run passed 17 large-file/local checks and 126 web tests; typecheck
  and production build passed. A terminal output-reader close race surfaced
  during cancellation cleanup and now exits quietly on intentional stream close.
  All 7 real-command/streaming/cancellation terminal checks passed afterward
  (`build/workspace-terminal-regression-tests.txt`). Local result labels and the
  initial workflow steps now describe local edits accurately.

### Phase 14 notes

- Historical implementation below is superseded by phase 15 for model access.
- The reported `Text file exceeds 256KB: client/public/Cover.svg` came from the
  canonical repository scanner, also used by `/api/project`. It treated the
  model-context bound as a project-opening failure. `_inventory_python` now omits
  oversized files completely from model snapshots, without truncating their
  content. Total snapshot/entry/depth limits and edit-size guards remain.
- `workspace_metadata` returns `large_files` separately; the explorer merges
  their paths into its tree/count. `workspace_text` provides read-only previews
  under the scanner's existing ignores, credential-path and link policy. Reads
  cap at 4MB with UTF-8 boundary handling and explicit truncation metadata.
  SVG is shown as text, never executed as page markup. Assets on disk are unchanged.
- `boundedFilePreview` caps editor rendering at one million characters / 20,000
  lines. Notices explain omitted model context and shortened previews. Copy uses
  the original loaded text, preserving line endings; it is hidden for incomplete
  server previews. Full files can be opened in VS Code.
- Native metadata-only entries still trigger the canonical Python fallback, now
  able to handle large assets; a regression test covers this. No native ABI or
  Rust code change is required.
- Regression coverage includes exact project-open path, large SVG source access,
  no partial model context, preview byte/UTF-8 limits, ignored/secret paths,
  candidate edits and HTTP routes. The project test helper now waits for task
  cleanup as well as terminal state before switching projects, removing its race.
- Disposable browser reproduction used `build/large-svg-ui-smoke/`; screenshot:
  `build/ui-proof/large-svg-project-open.jpg`. The user's project was not modified.

### Phase 13 notes

- NVIDIA (`https://integrate.api.nvidia.com/v1`) is first and is `DEFAULT_PROVIDER`.
  Default model is `nvidia/nemotron-3.5-lightning-30b-a3b`; official model page
  showed Free Endpoint available. The older Nemotron 3 Super page showed its free
  endpoint deprecated, so it was not chosen. See `MODEL_DISCOVERY.md`.
- User must connect a real NVIDIA key in Settings → Models → NVIDIA. The new
  Connect & use default action creates/selects the Lightning profile. No key was
  available for a real inference call. CLI users can set `NVIDIA_API_KEY` or
  `NVIDIA_NIM_API_KEY`; explicit DIKDIK environment configuration wins. No saved
  default is silently overwritten on application upgrade.
- Anonymous live NVIDIA discovery returned 85 models and contained the preset.
  Public metadata is not proof that every listed model has free inference.
  The UI was checked with synthetic keys and fixture discovery in a disposable
  project. Do not describe this as a real authenticated inference test.
- NVIDIA SVG is unmodified from its official brand page under `assets/brands/`.
  Model entry points use Dikdik's `/icon.svg`; provider suggestions retain actual
  provider logos. Provider descriptions now use the endpoint's actual provider
  name rather than calling all OpenAI-compatible services OpenAI.
- `web/dialogs.ts` / `dialogs.css` supply async confirmations and multi-field forms
  for Git, project trust, platform keys/backends/webhooks, ACP and local apps;
  skills/plugins/MCP confirmations also use them. Cancellation/Escape return
  null/false, restore focus and never approve actions. Required/URL/JSON errors
  appear inside the dialog; a regression test forbids native alert/prompt/confirm
  calls throughout app TypeScript. One-time API keys have a read-only copy view.
- Browser checks: NVIDIA setup/default selection, trust Cancel with no task
  started, inline invalid-URL feedback, conditional ACP fields, Escape cancellation,
  and light/dark rendering. Screenshots in ignored `build/ui-proof/`:
  `nvidia-model-banner.jpg`, `modern-trust-dialog.jpg`,
  `modern-backend-dialog.jpg`, `modern-agent-dialog-dark.jpg`.

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
