# Changelog

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
