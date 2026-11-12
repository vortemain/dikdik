# Changelog

## 0.3.8 — 2026-11-12

Browser:
- The Browser panel shows the page live (a screencast that updates as the page changes). Clicks,
  double and right clicks, hover, typing, paste and scrolling work in the live view and return at
  once; a hidden panel streams nothing and an unavailable stream falls back to the screenshot.
- The agent has 18 browser tools. It reads the page as an accessibility tree with stable element
  refs and acts by ref, role and name, label, text or CSS. It types key by key (masks and live
  validation run), fills, selects, checks, hovers and drags, and switches to dark mode or a device
  size. It sees screenshots (an element, a zoomed region, the full page) and reads computed
  styles, the console and network requests, including the data a form sent, and it can run
  JavaScript. Each action returns the updated tree and any new errors or failed requests.
- preview_app takes up to 20 steps (type, check, hover, double click and more) found by role,
  label or text, each with an optional expected text. A failing step is reported by number with a
  capture and the run stops there. It adds a dark-mode capture and reports console errors and the
  data forms sent. The app keeps running and opens in the agent's browser for step-by-step
  testing, which also counts as checking the requested interaction.
- Fixed: forms on public pages could never submit (the request was passed in place of the page
  state), and reading a page while a click navigated failed.

Desktop window and layout:
- Windows: one integrated title bar. The native caption is removed before the window first shows
  (the resizable frame stays). The page's title bar drags the window with Snap and double-click to
  maximize (WebView2 non-client regions), and the page draws its own minimize, maximize and close
  buttons, the only Python it can call. The window's first colour follows the system theme instead
  of green, and a maximized window stops at the taskbar. A WebView2 runtime without drag regions
  keeps the native title bar.
- The title bar, sidebar, conversation and panel keep fixed places at every width: between 761 and
  1100 px an open panel no longer drops under the conversation; the sidebar folds to a rail and
  opens as a drawer.
- Sidebar: Today, Yesterday, previous 7 and 30 days, then older; marks for failed runs and runs
  that need you; how long ago each was active; sort and filters behind a button.
- A calm start-up screen that appears only when start-up is slow, and a welcome page with the
  project, quick starts and recent conversations.

Setting up the computer:
- environment_check reports the OS, package managers and installed toolchains, what the project's
  files call for (about 40 toolchains: Python, Node.js and its package managers, Go, Rust, Java,
  .NET, C/C++, PHP, Ruby, Dart/Flutter, Swift, Zig, R, Docker, PostgreSQL, Android tools, Arduino
  and more), what is missing, and exact non-interactive install and verify commands for winget,
  Scoop, Chocolatey, Homebrew, apt, dnf, pacman, zypper or snap.
- A failed command comes back with a diagnosis and the fix for this computer: a missing program,
  Python or Node module, a project binary, or the Microsoft C++ Build Tools.
- Commands find tools installed since Dikdik started (the Windows registry PATH and the usual tool
  folders), installers may run for 30 minutes, and installing software is a high-risk action.

Desktop app:
- Right-click works again: Cut, Copy, Paste and Select All appear in the desktop window on
  Windows (WebView2), Linux release builds (Qt) and Linux source installs (GTK). The earlier
  Windows fix targeted a class pywebview 6 renamed, so it silently did nothing; pywebview also
  blocks the menu on Linux outside debug mode. Developer tools stay off.
- No more terminal windows popping up on Windows. When Dikdik runs without a console (the
  desktop app), every program it starts (git, npm, node, Python, PowerShell, MCP servers, and
  tools that libraries start) now runs hidden, not only the call sites that remembered the flag.

## 0.3.7 — 2026-11-02

Previews of your app:
- Fixed previews that hung for minutes and then failed: the preview proxy closed a connection
  after 20 quiet seconds, while `next dev` compiles a route on first request for longer
  (ERR_EMPTY_RESPONSE), and the preview browser closed every WebSocket, so Next's live-reload
  client reloaded the page in a loop and froze the capture. A real Next.js shop that failed after
  about five minutes per attempt (31 minutes in one run) now previews in 29 seconds.
- For the project's own preview host only: compiles may take five minutes, live-reload sockets
  are tunnelled to the dev server, and any method and body reach the app (forms, server actions,
  uploads). Public pages keep their limits.
- The page is requested once before the browser opens it, so it arrives compiled.
- A dev server that listens only on IPv6 (Vite on Windows) no longer costs about two seconds per
  request: the loopback address that answered is remembered.
- The dev server keeps running after a preview. The Browser panel shows the app live with its
  full address (localhost:port/path), Reload, Desktop/Mobile, a working "Open in your browser"
  and Stop; typing a path loads it there. The next preview of the same dev or static server
  reuses it and starts at once; a production server (next start) is restarted to serve the
  latest build. The app runs on a different host name from Dikdik, so it never receives
  Dikdik's sign-in cookie.
- Dev servers never outlive Dikdik: on Windows they join a job that ends with Dikdik's process
  (even when killed or replaced by an update), and the updater stops previews before exiting.
- The conversation says "Previewing your app" instead of "Opening public web page".

## 0.3.6 — 2026-11-02

Building a new app from a prompt:
- An empty project (or a request for a website, mobile app, API, desktop app, CLI and the like)
  gets the new `new-app` skill. The agent first works out who uses the product, on what, its data,
  integrations and realtime/offline/SEO needs, then chooses the architecture from them: Astro for
  content sites, Next.js or Vite + React for web apps, Expo for mobile, Hono, FastAPI or Go for
  APIs, Tauri for desktop, Go/Rust/Python for CLIs, Python with uv for data and AI. The user's or
  the project's stack always wins, and the reason for the choice is stated.
- Missing database or service credentials no longer mean fake code: real engines that need no
  setup (PGlite for PostgreSQL, SQLite, mongodb-memory-server, redis-memory-server), local adapters
  for email, storage and payments behind the production interfaces, network-edge mocks in tests
  only, migrations, seed data and an `.env.example`, so going live is configuration only.
- The project's lockfile picks the package manager; with none, bun when installed (then pnpm,
  then npm; uv for Python). A bare `bun run dev`, `pnpm dev` or `yarn dev` preview gets the port.
- The agent is told which runtimes and package managers are installed (Node, bun, pnpm, uv, Go,
  Rust, Java, .NET, Flutter, ...), which of PostgreSQL, MySQL, MongoDB and Redis are listening,
  and whether the project's .env sets DATABASE_URL (only its scheme is read). Tool presence is
  cached for ten minutes and the port probes run in parallel (about 0.3 s per run).
- Preview no longer loops: `path: "."` or a file name means the page path, a static site needs no
  command (Dikdik's Python serves it instead of `npx http-server`), `http.server 5173` and a bare
  `npm run dev` get the assigned port, and errors say what each field is.
- `run_command` refuses servers sent to the background (`&`, nohup, Start-Process): it waited for
  them until its time limit, about a minute a try, and each retry picked another port.

Desktop updates:
- Fixed on Windows: the updater ran the Inno Setup installer with NSIS's `/S` (so the wizard
  opened), relaunched the old app after one second while files were being replaced, and deleted
  the installer from a temporary folder while it ran. It now waits for Dikdik to exit, installs
  with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS` and reopens the installed
  copy; the installer closes processes still holding Dikdik's files and never runs twice at once.
- macOS keeps the app's symlinks and signature (ditto instead of a plain copy), updates the
  bundle where it actually lives, clears quarantine, and falls back to opening the disk image.
- Linux installs with `pkexec apt-get install` (dependencies resolved, system password dialog).
- A Python install gets the pip command for the new wheel instead of a desktop installer.
- The download runs in the background with progress and Cancel; the checksum is required; the
  release notes show in Settings; running tasks are confirmed before restarting; the sidebar
  shows progress; the next launch says whether the update took effect; checks repeat every six
  hours and are reused for 15 minutes.

Settings:
- The dialog no longer scrolls as a whole (a hidden screen-reader label stretched it), which
  slid both columns up and left blank space; only the section scrolls.

Files panel:
- Local runs write each create and edit to the folder immediately, so the Files panel and the
  user's editor follow the agent; the Explorer shows new files, reveals them and reloads (or
  opens) the file being written while the run is still going.
- VS Code source-control decorations: U (added), M (modified), D (deleted) with their colors, and a
  dot on folders that contain changes. The stack chips and the "read on demand" note are gone.
- Files, Terminal and Browser stay open across new tasks, and a finished run no longer pulls the
  panel away from the tab being watched.

## 0.3.5 — 2026-11-02

The v0.3.4 tag failed its release checks and was never published; 0.3.5 contains everything
listed under 0.3.4 below, plus:
- Fault testing: the proxy's `down` mode shuts its listener down before closing it, so Linux
  refuses new connections immediately instead of completing the handshake and resetting them.
- `question_answered` is recorded in the agent's thread before it continues, so it always
  precedes what the agent does next (it could previously land after the task completed).
- `planning.plans` routes its workspace clause through `sql.clause` (security gate PY008).
- The benchmark's `git archive` export has a timeout; a failing preview browser close is logged.

## 0.3.4 — 2026-11-02 (not published)

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
- CI: security workflow (scanner, pip-audit, bun audit, Gitleaks); route-authorization test over
  every API path.

Agent speed and reliability, from traces of slow real runs (40–60 minute footer
redesigns that ended paused or blocked after 87–103 model calls).

- `edit_file` matches across LF/CRLF and keeps each file's own line endings.
  `read_file` hides `\r`, so every multi-line edit to a Windows file used to fail.
- `create_file` takes `overwrite: true` to replace an existing file the agent has
  read (tests stay protected), so redesigns no longer write source through the shell.
- The syntax gate judges only files the change touched and ignores problems those
  files already had; JSX text like `Kenya's` no longer reads as an open string.
  Before, one untouched file anywhere in the project could block every completion.
- Local commands default to UTF-8 Python I/O (`PYTHONUTF8=1`, unless already set),
  so scripts no longer write source in the Windows ANSI code page.
- A file changed outside the agent costs one retry instead of failing every later
  command, check and preview: the agent adopts the folder's version (never
  overwriting it), and a file a command deleted or left non-UTF-8 is restored.
- Hosted models get a 320 KiB working context (local servers keep 128 KiB); a
  provider context-window rejection compacts and retries. Compaction now frees real
  room instead of re-running on almost every step and discarding the prompt cache.
- Only `planning` is always-on; `research-first` and `code-review` load by trigger.
  The system prompt asks for proportional context gathering, batched reads and
  file changes through the file tools only.
- Checkpoints hold only changed files instead of the whole project after every
  tool call (7.6 MB → 0.8 MB per save on a 350-file project); resume rebuilds the
  rest. Dikdik's own per-step overhead fell by about a third.
- A resumed run that already did its work can finish with a plain reply; it was
  told it had "not used any workspace tools" and could end blocked.
- `python -m dikdik.bench` (docs/BENCHMARKS.md): replay real tasks on throwaway
  project exports and score them, measure Dikdik's own overhead without a model,
  summarize past runs, and compare reports.
- File text reaches the model as numbered plain lines (`read_file`) and
  `path:line:column` rows (`search_text`) instead of JSON, where every quote became
  `\"` and every non-ASCII character `\uXXXX`. Compaction now keeps excerpts of
  what was read; before, it kept only the path.
- `edit_file` takes `edits` for several replacements in one file; all or none apply.
- Local tasks start with a measured project outline (stack, scripts, main folders,
  line endings) and the test/build commands that worked or were missing in earlier
  tasks, kept in Dikdik's data folder.
- When tests fail, the same checks run once on a copy of the project from before the
  change. If every remaining failure was already there, the task can finish as
  "no new failures", never reported as a test pass. Unidentifiable failures still fail.
- Each agent run reuses one SQLite connection for its writes (opening one cost about
  25 ms on Windows, several times per step) and closes it when the run ends; other
  callers still open and close their own. `list_files`/`search_text` reuse the file
  listing until a command runs. With the checkpoint change, Dikdik's own time per
  model call on a 350-file project fell from 1.4–1.9 s to about 0.46 s.
- `Engine` defined `close()` twice, so the second silently replaced the first and
  background shells were never stopped on shutdown. One `close()` now does both.
- With a model router active, read-only subagents and conversation summaries use its
  fast model. Anthropic models through OpenRouter get prompt-cache markers.
- Tool use, from an audit of real runs: in local mode `run_command` no longer claims
  to run in a Docker sandbox, which led models to invent `/workspace/...` paths. File
  tools accept `./x`, `x\y`, `/workspace/x`, `/` and absolute paths inside the project;
  `read_file` accepts `end_line`/`offset`/`limit`; out-of-range budgets are clamped and
  arguments to `finish` ignored instead of failing the call. Reading files through the
  shell (up to 55 times in one run) gets a one-time steer to the file tools. Six refused
  `finish` calls end the run as blocked with the reason (one run had 34). The prompt
  says when to browse, to act on design requests rather than ask for preferences, and
  to ask for missing values rather than invent them. `create_file` on an existing file
  explains `overwrite`/`edit_file` instead of a policy error.
- Benchmark reports and `history` show tool use: tools per call, duplicate reads,
  shell reads, time to first edit and the most common errors per tool.
- Design work is verified in the rendered page. `preview_app` takes `focus` (screenshots
  of one element at desktop and mobile width) and `inspect` (exact computed borders,
  shadows, backgrounds, spacing and the gap to the next element). Screenshots are shown to
  the model as images, only the newest pair kept; a model that rejects images continues
  without them. Design requests get before/after preview instructions, and preview no
  longer needs the public-web browsing approval (it opens only the project's own local
  server; command approval and the browser switch still apply). A project whose preview
  cannot start twice finishes with the visual check reported as missing instead of looping.
- Design intent covers redesign, header, footer, navbar, spacing, colours, skeletons and
  more; such requests always load the `ui-ux-design` and `browser-verification` skills.
  Skill triggers match whole words ("ui" no longer matches inside "build"). Bundled skills
  update in place when the user has not edited them; `browser-verification` now teaches
  the before/after loop with Dikdik's real tools.
- `read_file` takes `paths` to read up to eight files in one call.
- Real-project preview fixes, found by live runs on Vite/TanStack projects:
  `preview_app` never worked through the engine (its stop signal had no `wait()`);
  dev servers bound to IPv6 `localhost` (`::1`, Node's default on Windows) were never
  reached; readiness waits 90 s and page loads 90 s for cold SSR compiles; a dev
  server regenerating a file (`routeTree.gen.ts`) keeps the file instead of failing
  the preview; a 500 page includes the dev server's console output; preview seams
  list every border, shadow, gap or background change; literal port flags become
  `{port}`; text-only models refusing images with HTTP 404 continue without them.
- Checks: a noisy check (lint printing 5,000 CRLF errors) runs to its exit code
  instead of failing at the output cap; ESLint errors are compared before and after
  a change by file, rule and message, so pre-existing lint failures no longer block;
  editing lint/test/type-check configuration gets an immediate warning, and the
  prompt forbids relaxing checks to pass (a live run had downgraded a rule).
- The Browser panel opens by itself when the agent starts showing a page, as Claude
  Code's preview pane does (only for something happening now, never on reopening an
  old conversation). It is a plain browser window: one tab, back/forward, a reload
  button that becomes stop while loading, an address bar and the page; zoom, page text
  and history sit in a More menu, and the status line appears only for errors.
  Previews show `localhost` instead of the internal preview host, switch between their
  desktop and mobile captures, and are not offered as live links once their server stops.
- The Terminal panel works like VS Code's: one continuous screen with the prompt after
  the output, `folder $ command` lines, a hollow or red gutter mark for success or
  failure, click-anywhere to type, `cd` and `clear` handled like a shell, and small icon
  actions (new, stop, copy, clear). The welcome card, Run/Stop buttons and Folder field
  are gone.
- New terminals open as tabs inside the Terminal panel instead of switching the chat to
  a "Workspace tools" conversation. Each terminal keeps its own output, folder and
  history, closes with its tab, and comes back after a reload; an Agent tab holds the
  commands run in the open conversation. Browsing with no conversation open also stays
  in the Browser panel. Terminal and browser sessions no longer appear in the
  conversation list.
- The Planner lists every plan in the project with where it stands (planning, ready to
  build, building, built, failed). A plan opens in place; New plan starts the planner
  from the panel and Build hands the reviewed plan to the code agent, without leaving
  the conversation on screen. "Discuss plan" opens the planner conversation on request.
- The Task List tab is always available, with the agent's steps, a done count and a
  progress bar. The agent is now asked to keep it for work with three or more steps.
- Commands may not `cd` into an existing folder outside the project. Benchmark tasks
  install dependencies inside each copy (`setup`) instead of linking the real
  project, answer the agent's questions, and flag only genuinely mixed line endings.

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
