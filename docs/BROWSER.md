# Workspace browser and agent web tools

Desktop installs include a persistent Chromium runtime. Browsing stays inside
the workspace Browser panel: the page renders in a separate browser process,
and the panel forwards clicks, typing, paste, scrolling and navigation. The
agent uses that same task-scoped session. There is no popup browser window and
no requirement to run Docker for this path.

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

## Legacy capture fallback

Without the optional persistent browser runtime, the existing one-shot system
browser or Docker screenshot capture remains available. The following describes
the Docker capture worker's narrower capabilities and historical validation.

The agent's `browse_url` tool opens a public HTTP/HTTPS URL, returns bounded page
text as untrusted tool data, and stores a 1440×900 screenshot. The screenshot
appears in chat and in the Browser tab. The tab keeps the last successful image
when a later capture fails. Its external link opens with `noopener noreferrer`.

Select **Browse public web** and **Run tools in Docker** in the composer to allow
the tool for an agent turn. Browser execution can also be approved with syntax-only
verification; that approval still does not mean tests were run. Agent profiles
can disable browsing, and Settings → General can disable the feature globally
for this workspace. A profile or schedule cannot grant browsing consent.

For a manual capture, open the workspace panel → Browser, enter the public URL,
and approve **Allow Docker and public web access for this capture**. The checkbox
resets after a capture or conversation change. **Stop capture** cancels that task's
capture. A task cannot be deleted while its browser is running.

## Install the optional runtime

The Python backend and web client gain no runtime dependencies. Playwright and
Chromium live only in a separate, operator-built container image. Review its
contents and licenses before building; the command below downloads a base image
and an exact Playwright package version and uses network access during the build.
No build, pull, package installation, or Docker startup is automatic.

```powershell
docker build -t dikdik-browser:1 dikdik/browser_runtime
```

The Dockerfile pins Playwright and its matching browser image to **1.64.1**.
An immutable base-image digest and distribution/security review are still release
gates. The operator must rebuild the image after updating the worker or its
dependencies. The local image protocol label is checked for tool discovery;
finding that label is not proof that Chromium launched successfully.

## Network and storage boundary

Chromium runs as UID/GID 65534, with no network, a read-only root, no capabilities,
no-new-privileges, bounded memory/CPU/PIDs, an in-container timeout, and no workspace,
credential, Docker-socket or host-root mount. Chromium's own sandbox is disabled
under this restricted profile; the Docker boundary remains essential and is not
proof against browser/kernel/daemon exploits.

The trusted backend relays public **GET** resources over stdin/stdout. It forwards
no browser or host cookies, authorization, proxy configuration, custom headers,
or ambient credentials. Every URL and redirect uses HTTP/HTTPS on its standard
port. DNS answers must all be public, and each TCP connection is pinned to one
validated address while HTTPS verifies the original hostname. Private, loopback,
link-local, multicast, mapped IPv6 and local-name targets are refused. Requests
that bypass interception still cannot leave the network-disabled container.

Captures allow at most 64 resources, 2 MiB per resource and 8 MiB total, with a
60-second host deadline, bounded DNS/fetch operations and container cleanup.
Only GET is relayed; forms, uploads, login sessions, WebSockets and downloads are
unsupported. Some interactive or large sites will fail or render incompletely.
JavaScript and page text are untrusted and cannot change approvals or tool policy.

Screenshots are authenticated PNG responses under the task's API scope. SQLite
stores at most 10 screenshots per task and 100 across the data directory (each
at most 2 MiB); an older chat image may become unavailable after retention.
Deleting a task atomically deletes its screenshot rows and ordinary event history.
URLs, titles, page text and screenshots can contain sensitive content, so keep
the private data directory protected. There is no general content-secret detector.
Interrupted captures are marked interrupted on restart and get a bounded attempt
to remove their recorded container.

## API

All routes use the existing session/bearer authentication and Host/Origin checks.

| Route | Behavior |
|---|---|
| `GET /api/browser` | enabled flag, local image availability, runtime and required approvals |
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

Unit/HTTP contracts exercise the runner protocol with stand-ins, real SQLite,
real loopback HTTP, URL/DNS/credential policy, cancellation, restart and deletion.
The real Chrome UI checks include a clearly labeled screenshot stand-in. The
actual Docker attempt in this environment returned `blocked`; no successful
Chromium-in-Docker capture or isolation pass is recorded.

To explicitly authorize the live test, after reviewing and building the image:

```powershell
$env:DIKDIK_RUN_BROWSER = "1"
python -m unittest tests.test_browser.LiveBrowserTests -v
Remove-Item Env:DIKDIK_RUN_BROWSER
```

This makes a real public request to `https://example.com/` through the relay.
