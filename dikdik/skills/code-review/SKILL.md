---
name: code-review
description: Review changes before declaring done. Adapted from addyosmani/agent-skills.
category: methodology
version: "1.0"
license: MIT
triggers: "before finish, before apply, multi-file change"
---

# Code review

Before calling a task done, review your own diff the way a careful reviewer
would. The agent that wrote the code is the worst reviewer of that code, so be
deliberate.

## What to check, in order

1. **The diff answers the request.** Read the objective again, then the diff.
   If the diff does something the request did not ask for, or misses something
   it did, that is the most important finding.
2. **No unrelated changes.** Every line in the diff should be traceable to the
   request. Reformats, "while I was here" edits, and silent dependency bumps
   belong in separate work.
3. **Tests are not weakened.** Scan the diff for changed assertions, removed
   cases, or relaxed limits. If a test was weakened to pass, stop and say so.
4. **Error paths exist.** New code that reads input should reject bad input
   with a clear message, not crash or silently accept it.
5. **Naming and structure.** A function name that lies about what it does is
   worse than a long name that tells the truth. Flag any name you would not
   want to read a month from now.
6. **No secrets or credentials in the diff.** Keys, tokens, and passwords do
   not belong in source. If the change adds one, stop.

## How to surface the review

Do not paste a generic checklist into every reply. If the review found
nothing notable, a single line ("Reviewed the diff; no concerns beyond the
change itself") is enough. If it found issues, fix them before finishing — do
not list concerns and finish anyway.

## When the change is small

For a one-line fix, a full review is a single question: does this line do what
the request asked, and does it break a neighbor? Spending a paragraph on it
is theater.
