# Workspace browser and agent web tools

Desktop installs include a persistent Chromium runtime. Browsing stays inside
the workspace Browser panel: the page renders in a separate browser process,
and the panel forwards clicks, typing, paste, scrolling and navigation. The
agent uses that same task-scoped session. There is no popup browser window and
nothing to install beyond Dikdik's desktop extras.

## Local application verification

In local Auto mode, `preview_app` can start the project's development server,
capture desktop (1440 × 900) and mobile (390 × 844) views, and exercise bounded
click/fill/select/key actions with expected visible text. It reports JavaScript
runtime errors, failed resources and horizontal overflow, saves screenshots in
the task's browser history, then closes its browser and stops its server.
An agent cannot satisfy a requested interaction merely by capturing a screenshot.
These are measured checks of the supplied page and actions, not proof of aesthetic
quality or every possible user journey.

The command uses `{port}` for an unused port chosen by the host. A separate proxy
maps `preview.dikdik.example` only to that assigned loopback port; arbitrary local
URLs remain blocked. Preview execution requires the task's local execution and
browser permissions and a profile that permits shell/browser work. Auto proceeds
without per-tool prompts; explicit permission rules and the browser switch still apply.

## Public browsing

`web_fetch` returns real text, HTML or JSON through a bounded, credential-free
public GET with validated redirects. `browse_url` loads the rendered page and
returns text, a screenshot and CSS selectors for visible controls.
`browser_click`, `browser_fill`, `browser_select`, `browser_press`,
`browser_scroll` and `browser_screenshot` operate on the retained page. Input
and clicks are classified as high-risk website actions by the approval policy;
page content never grants permission. Profiles and the global browser switch
still control whether these tools are offered.

Sessions have empty browser profiles and separate cookies per task. Browser
requests use the existing public-only proxy with pinned DNS. Service workers,
WebSockets, browser downloads and native permission dialogs are not supported.
Form interactions can send public HTTP/HTTPS requests after approval. Up to two
task sessions are retained; opening another evicts the oldest session. Browser
state ends on project/server shutdown, task deletion or eviction. Screenshots
and page text remain subject to existing history retention.

For source development install the desktop extra and run
`python -m playwright install chromium`. Desktop bundles include the matching
Chromium. Run real interaction tests with `DIKDIK_RUN_INTERACTIVE=1` and
`python -m unittest tests.test_interactive_browser -v`; the fixture exercises a
real browser with local-only forms and no external submissions.

## Fallback: the computer's own browser

Without the persistent browser runtime, a capture uses the Edge or Chrome already
on this computer (`dikdik/host_browser.py`), headless, with a new empty profile per
capture that is deleted afterwards: no cookies, logins, extensions or history. The
browser is pointed at a proxy inside Dikdik that resolves every host itself and
connects only to public addresses on ports 443 (CONNECT) and 80 (GET/HEAD). Redirects
or page resources aimed at localhost, the local network or Dikdik itself are
refused. Dikdik never runs a browser in Docker; the container worker was removed on
2026-11-02.

The agent's `browse_url` tool opens a public HTTP/HTTPS URL, returns bounded page
text as untrusted tool data, and stores a 1440×900 screenshot. The screenshot
appears in chat and in the Browser tab. The tab keeps the last successful image
when a later capture fails. Its external link opens with `noopener noreferrer`.

Select **Browse public web** and **Run commands on this computer** in the composer
to allow the tool for an agent turn. Browser execution can also be approved with
syntax-only verification; that approval still does not mean tests were run. Agent
profiles can disable browsing, and Settings → General can disable the feature
globally for this workspace. A profile or schedule cannot grant browsing consent.
A task cannot be deleted while its browser is running. When neither runtime is
available, a capture ends `blocked`.

JavaScript and page text are untrusted and cannot change approvals or tool policy.

Screenshots are authenticated PNG responses under the task's API scope. SQLite
stores at most 10 screenshots per task and 100 across the data directory (each
at most 8 MiB); an older chat image may become unavailable after retention.
Deleting a task atomically deletes its screenshot rows and ordinary event history.
URLs, titles, page text and screenshots can contain sensitive content, so keep
the private data directory protected. There is no general content-secret detector.
Interrupted captures are marked interrupted on restart.

## API

All routes use the existing session/bearer authentication and Host/Origin checks.

| Route | Behavior |
|---|---|
| `GET /api/browser` | enabled flag, availability (`interactive_ready`, `host_ready` or `unavailable`), runtime and required approvals |
| `PATCH /api/browser` | persist `{enabled: boolean}`; disabling cancels active captures |
| `POST /api/tasks/{id}/browser` | `{url, approve_execution: true, approve_browser: true}`; return capture status |
| `POST /api/tasks/{id}/browser/stop` | request task-scoped capture cancellation |
| `GET /api/tasks/{id}/browser` | most recent result and capability metadata |
| `GET /api/tasks/{id}/browser/screenshots/{snapshot}` | authenticated, task-scoped PNG |
| `GET /api/tools/`, `GET /api/tools/catalog` | original registered tool names and availability; browser consent remains mandatory |
| `GET /server_info` | authenticated runtime/tool discovery consistent with the catalog |

Task requests accept the optional literal Boolean `approve_browser`. It requires
agent mode and literal `approve_execution: true`. It is never inferred from
provider disclosure approval or enabled tools.

## Verification

Unit/HTTP contracts exercise both runtimes with stand-ins for the browser itself,
real SQLite, real loopback HTTP, URL/DNS/credential policy, cancellation, restart
and deletion (`tests/test_browser.py`, `tests/test_host_browser.py`).
