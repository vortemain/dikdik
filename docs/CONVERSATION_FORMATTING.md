# Conversation output

Replies, streamed text, Markdown file previews and new-version previews share the
same renderer. Soft line breaks wrap naturally; blank spacer lines do not create
empty paragraphs. Code fences preserve their original whitespace.

Supported formatting includes headings, nested ordered/bulleted lists, read-only
task checkboxes, bold, italic, strikethrough, `==highlights==`, inline code, safe
HTTP/HTTPS links, blockquotes and GitHub-style note/tip/warning callouts. Known
project paths in inline code open Files.

## Tables

Click a column heading to sort ascending/descending. Numeric columns support
negative values and decimals. **Copy table** copies tab-separated data in the
current sort order; sorting never changes the original reply or project files.
Large tables display their first 500 sorted rows; Copy table includes every row
in the rendered message's data.

## Charts

A `chart` fenced block accepts declarative JSON, not executable code:

````markdown
```chart
{"type":"bar","title":"Measured load time (ms)","labels":["Before","After"],"values":[120,65]}
```
````

Types are `bar` and `line`. Multiple series use `series` instead of `values`:

```json
{"type":"line","title":"Measured requests","labels":["Mon","Tue"],"series":[{"name":"Successful","values":[12,18]},{"name":"Failed","values":[2,1]}]}
```

Only supplied finite numbers are plotted: at most 60 labels and four series.
Hover or focus a point for its value; expand **View and copy data** for an
accessible table. Unsupported or invalid chart data stays visible as source,
with an explanation. The agent must not invent measurements to fill a chart.

## HTML/CSS design previews

HTML code fences offer **Preview design** after browser sign-in. Previews are
explicitly opened, sanitized, authenticated, project-scoped and short-lived.
The source code stays available through **View code** and Copy.

Previews have an opaque sandbox origin and a restrictive Content Security Policy:
no scripts, access to Dikdik's session/API, form submissions, automatic navigation,
or outside images/styles/fonts. Inline CSS and native `<details>` interactions
work. This is a static design preview, not a full application runtime. Real app
behavior still needs the project's normal development and testing workflow.
