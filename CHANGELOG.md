# Changelog

## Unreleased

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
