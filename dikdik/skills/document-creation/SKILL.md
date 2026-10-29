---
name: document-creation
description: Create clear documents (READMEs, ADRs, runbooks, API docs) for a real reader. Adapted from anthropics/skills.
category: writing
version: "1.0"
license: MIT
triggers: "README, documentation, ADR, runbook, API doc"
---

# Document creation

A document is for a reader who needs to do something, not for the writer to
show they understood the system. Write to the reader's task.

## Match the document type

- **README:** what the project is, how to run it, how to contribute — in that
  order, and only what a new person needs. A README that starts with
  architecture is for the author, not the reader.
- **ADR (architecture decision record):** context, decision, status,
  consequences. The decision is one sentence; the consequences include what gets
  harder. An ADR that does not list a downside is advocacy, not a record.
- **Runbook:** the symptom, the check, the fix — for an on-call reader at 3am.
  Numbered steps, exact commands, no prose that requires context.
- **API doc:** the endpoint, parameters, a request, a response, the errors.
  An example request and response are worth more than a paragraph of
  description.

## Universal rules

- **Lead with the task.** The first sentence tells the reader what they will
  be able to do after reading. "This document explains…" is a sign the writer
  did not decide who it is for.
- **One audience.** A document for new contributors and a document for the
  on-call engineer are different documents. Pick one; link to the other.
- **Examples over adjectives.** "Fast" means nothing; "responds in under 50ms
  at p99" means something. "Easy to use" means nothing; "one command to start"
  means something.
- **Keep it current or delete it.** A stale document is worse than no
  document — it misleads with confidence. If you are documenting something
  that changes, note what version it describes and when.

## What a document is not

It is not a place to show thoroughness by listing every file. It is not a
transcript of your investigation. If the reader does not need it to do their
task, it does not belong.
