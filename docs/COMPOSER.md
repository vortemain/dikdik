# Composer drafts

Unsent text is saved after a short pause in the private server data directory.
Home and each conversation have separate drafts. Switching conversations flushes
pending edits, and returning or reloading restores that conversation's text.
Suggestions also update the saved draft. Empty or whitespace-only text disables
Send; Shift+Enter inserts a newline and Enter submits.

Drafts contain text only, up to 4,000 characters. They do not save permissions,
run tools, call a model or change the workspace. Task deletion removes its draft;
Home is separate. The database is private but draft text is not encrypted.

Successful submission clears only the text acknowledged by the server. Rejected
submissions preserve it. A failed history refresh after an acknowledged message
does not turn that message into a failed send.

If another tab saved a different version, both texts remain available. Expand
**Saved draft from another tab**, then choose **Use saved draft** or **Keep my
draft**. No version is silently overwritten, including an empty saved version.
If a save fails, keep the tab open and use **Retry save**. Unsaved edits remain in
memory; the browser warns before leaving. Closing anyway or a browser crash can
lose those unsaved edits.

The authenticated API is `GET /api/drafts/{scope}` and
`PATCH /api/drafts/{scope}`. Scope is `home` or an existing conversation ID.
PATCH requires exactly `content` and the last `revision`. A stale revision returns
409 with `current`; clears retain a revision so stale tabs cannot recreate text.
The web client uses this API rather than directly accessing storage.

The full slash menu, attachments, side questions, goals and context meter remain
separate Phase A work.
