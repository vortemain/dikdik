---
name: browser-verification
description: Verify rendered web behavior using the browser capabilities actually enabled for the task.
category: engineering
version: "1.0"
triggers: "browser verification, visual check, end-to-end, rendered page"
---

# Browser Verification

1. Identify the specific user interaction and expected visible state. Use deterministic fixtures or a controlled local page where possible.

2. Check which browser tools are available: a URL/text visit cannot establish visual layout, focus behavior or interactive correctness by itself.

3. When interactive/visual tools exist, exercise the flow at relevant viewport sizes and inspect errors, loading, empty and failure states. Capture only evidence needed for the finding.

4. Keep authentication and external writes inside the task's permissions. Report whether the result came from code inspection, a page fetch or an actual interactive browser check.
