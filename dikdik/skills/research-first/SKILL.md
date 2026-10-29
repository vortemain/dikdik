---
name: research-first
description: Read and search the codebase before proposing changes. Adapted from affaan-m/ECC.
category: methodology
version: "1.0"
license: MIT
triggers: "unfamiliar codebase, integration, before estimating"
---

# Research first

Before proposing a change in a codebase you have not read, investigate. A
confident-sounding plan built on assumptions is worse than an honest "I need to
read this first."

## Minimum investigation

- **What does the request touch?** Use `list_files` with a query for the
  feature name, then `read_file` the files that match. Read the actual code,
  not just the file list.
- **What calls what?** For a change to function X, find its callers with
  `search_text` on the function name. A change that ignores callers breaks
  them.
- **What conventions are already here?** Look at a neighbor file before
  adding a new one. If the project uses `pydantic` models, do not add a
  hand-rolled validator. If it uses `pytest`, do not add `unittest`.
- **What tests exist?** Read the test that covers the area you are changing.
  It tells you the intended behavior and what the user considers a contract.

## When to stop researching

You have read enough when you can state, in one or two sentences, what the
change will touch and why it will not break a caller you have not seen. If you
cannot state that, read more.

## Anti-patterns

- **Reading everything.** Investigating the whole project for a one-file
  change is procrastination. Scope the research to the request.
- **Reading filenames only.** A file named `utils.py` tells you nothing about
  its contents. Open it.
- **Planning before reading.** A plan that lists files to "discover" later is
  a plan to guess later. Read first, then plan.

## Output

The investigation is not the deliverable. State what you found that affects
the change, then proceed. A research log that the user has to read is not the
goal; a correct change is.
