---
name: error-recovery
description: Recover from tool failures, transient errors and stalled agent loops without repeating unsafe actions.
category: agentic
version: "1.0"
triggers: "retry, tool failure, recovery, stuck loop, timeout"
---

# Error Recovery

1. Classify the failure as invalid input, stale state, denied permission, unavailable capability or transient infrastructure. Read the structured error before choosing a retry.

2. For invalid edits, reread the current text and form a new exact match. For missing paths, list the containing folder; do not broaden into unrelated filesystem roots.

3. Retry a transient operation only within a bounded budget and after checking whether its previous attempt produced a side effect. Use idempotency support when available.

4. Treat denied actions as a decision boundary. If no authorized alternative remains, preserve progress and explain the specific blocker instead of changing tools to evade it.
