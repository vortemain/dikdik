# Dikdik desktop

## Interactive browsing and distribution work

Entering an address opens the page inside the workspace Browser panel. A
persistent Chromium session renders the page, and the panel forwards clicks,
keyboard input, paste, scrolling, Back, Forward and Reload. No separate browser
window opens. The agent and panel use the same task-scoped page and see its
updated text, screenshots and visible control selectors. External page HTML
never becomes part of the privileged workspace DOM.

With browser consent enabled, the agent can call `web_fetch`, `browse_url`,
`browser_click`, `browser_fill`, `browser_press`, `browser_select`,
`browser_scroll` and `browser_screenshot`. Fetch uses a bounded public GET;
interactive actions retain page state. Clicks and form input follow the agent's
action approval policy. Pages are untrusted data, and cannot grant permissions.
Sessions start with empty cookies and are separate from your normal browser.

The release workflow builds installers on Windows, Apple Silicon and Intel macOS,
and Linux, using `scripts/build_desktop.py` to bundle Python and dependencies.
It launches each frozen app, completes an authenticated task and opens a public
page with bundled Chromium before creating installer artifacts. The same builds
can be dispatched manually through `Desktop installers`.
The installers are unsigned developer previews; clean-machine installation and
native interaction checks remain separate from these automated launch checks.
Windows needs WebView2; Linux needs its listed system libraries. No Docker installation is required for the native
interactive browser. Builds bundle the matching Chromium runtime and native
Dikdik window/application icons. Source installs can install Chromium with
`python -m playwright install chromium`; Edge/Chrome is a fallback.

Dikdik can open in its own native desktop window using the operating system's
web renderer. The desktop and browser versions share projects, conversations,
settings, and the authenticated local API. Desktop support is an optional
installation; the normal `dikdik` command continues to open a browser.

## Install and launch

Use Python 3.13 or newer. From a source checkout:

```powershell
python -m pip install -e ".[desktop]"
dikdik-desktop C:\Projects\my-app
```

On Windows you can then double-click **Dikdik.pyw** in the source checkout. If
there is no usable current or previous project, a native folder picker asks
which project to open. Cancelling the picker closes the launcher.

For a workspace-only dependency installation without changing global Python:

```powershell
python -m pip install --target build/desktop-deps "pywebview>=6,<7" "playwright==1.64.1" "desktop-notifier==6.2.0"
```

`Dikdik.pyw` automatically uses that folder when it exists.

For an installed release, install the extra from the downloaded wheel:

```powershell
python -m pip install "./dikdik-0.2.0-py3-none-any.whl[desktop]"
dikdik-desktop C:\Projects\my-app
```

You can also use `dikdik --desktop FOLDER` or
`python -m dikdik.desktop FOLDER`. On Windows the installed `dikdik-desktop.exe`
is a GUI launcher and does not keep a console window open. A shortcut can target
that launcher with the project path as its argument. With no folder argument,
Dikdik opens the current folder when usable, otherwise its last project.

The desktop starts on loopback port 8787, keeping the same origin for saved
theme and drafts. Set `--port PORT` to choose another (or `--port 0` for an
automatic port), and `--data FOLDER` for a separate private data directory.
Changing the port creates separate browser storage. Only one process may use
the same data directory at a time, including browser and desktop processes.
Close the window to stop its HTTP server and release the project.

## Renderers

Windows uses Microsoft Edge WebView2. Install the
[WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
if it is unavailable. The launcher requires this modern renderer and will not
fall back to Internet Explorer.

Linux needs a graphical desktop and a pywebview renderer. For example:

```bash
python -m pip install -e '.[desktop]' 'pywebview[qt]>=6,<7'
dikdik-desktop /path/to/my-app
```

GTK is also supported when its system libraries are installed; consult the
[pywebview installation guide](https://pywebview.flowrl.com/guide/installation.html)
for platform packages. macOS uses its system WebKit through pywebview.

## Local app behavior

The desktop host binds to `127.0.0.1` and uses the same HttpOnly session-cookie
exchange as browser mode. Its initial token is passed in a URL fragment. It does
not expose a custom Python API to the page. External links open in the system
browser, and the host disables pywebview's file-URL access flag. App downloads, including
conversation exports, remain available. Theme and drafts persist in a dedicated
`desktop` folder inside the private data directory.

Source launch requires Python and the optional dependencies. Platform bundles
include Python and Chromium; their installers still require native OS testing
and signing before publication. If desktop support is unavailable,
`dikdik FOLDER` runs the browser interface.

## Native notifications

General settings offers background task notifications, all task notifications,
or off, plus a test button. Background mode suppresses alerts for the task you
are currently viewing in the focused window. Completion, failure, pauses and
requests for approval or input open the relevant task when clicked. Approval
and secret entry stay inside Dikdik. Previews are off by default; enabling them
adds the task title and project name. Notification sounds follow the sound setting.

Windows uses the same `Dikdik.Desktop` identity for the window, native toast and
Start menu shortcut. The installer supplies the shortcut; source and portable
launches create it if missing. This registration is required for visible banners.
The shortcut is retained across launches. Operating system notification settings
and Do Not Disturb still control delivery. A successful test submission does not
prove a banner was displayed.

macOS requires a signed application bundle and notification permission. Linux
requires a desktop notification service. These platforms need native release
validation on their respective operating systems before publication.

The Ubuntu 24.04+ Debian package installs a dedicated AppArmor compatibility
profile for its root-owned Chromium executable. This permits the browser's
namespace sandbox without disabling system-wide namespace restrictions or
Chromium sandboxing. The package loads the profile on installation and removes
it on uninstall.
