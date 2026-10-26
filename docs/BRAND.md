# Compact identity

**Dikdik** / *Small name. Serious intelligence.*

## Mark

`dikdik/web/icon.svg` is an original, front-facing dikdik head: oversized
alert ears, two short straight horns, a narrow tapering snout and large eyes,
in tan (`#E8C496`) on a deep forest rounded square (`#1F3D31`). Shapes are
filled, not stroked, so the silhouette still reads at 16px. The lowercase
`dikdik` wordmark sits beside it in Inter 650 with slight negative tracking.
The mark is used as the favicon, the assistant avatar and the empty-state
emblem; it is never animated to imply work.

## Type

- **Inter** (variable, bundled) for all interface text. Base 14px; chat prose
  15px / 1.68; titles 30px (empty state) and 14px (top bar); metadata 12–13px.
  Nothing is set below 11px.
- **JetBrains Mono** (variable, bundled) for code, diffs, paths and tool
  arguments at 12.5–13px / 1.6.

Both ship as local WOFF2 files under the SIL OFL; no font CDN or tracking.

## Color and theme

Neutral warm greys carry the layout, black (light) or near-white (dark)
primary buttons carry actions, and forest green marks focus, approval and
success. Diff lines use paired green/red/blue tints with matching ink so they
stay readable in both themes. Light, dark and system themes are defined as CSS
custom properties on `:root`; the choice is stored per browser.

## Layout

Sidebar (new task, recent tasks with live state dots, workspace, settings) →
centered 768px conversation → optional right-hand **Changes** canvas with the
per-file diff. Agent tool calls render as compact, expandable steps paired with
their output; every code block has a language label and a Copy button. Settings
open as a dialog with sections for General, Models, API keys, Skills and
Automations. Below 1100px the canvas becomes an overlay; below 760px the
sidebar becomes a drawer.

Status is always stated in words (Completed, Not test-verified), never by
color alone. The working indicator appears only while the backend reports a
queued or running task.

This is a developer-preview identity, not a trademark clearance or a claim of
completed accessibility certification.
