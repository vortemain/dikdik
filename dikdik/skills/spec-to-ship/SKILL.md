---
name: spec-to-ship
description: A disciplined pipeline for non-trivial work: spec, plan, build, test, review, ship. Adapted from addyosmani/agent-skills.
category: methodology
version: "1.0"
license: MIT
triggers: "feature, multi-step change, production work"
---

# Spec → plan → build → test → review → ship

For non-trivial work, a disciplined pipeline produces a better result than
jumping to code. Each stage has a gate; do not skip a stage because the next
one feels more productive.

## The stages

1. **Spec.** One paragraph: what the change is, what it is not, the single
   acceptance criterion that proves it is done. If you cannot write the
   acceptance criterion, the request is not specific enough — ask.
2. **Plan.** Three to seven ordered steps, each checkable. (See the `planning`
   skill.) The plan is shared with the user via `task_tracker` so they can see
   and correct direction before you build.
3. **Build.** Implement the plan one step at a time. After each step, the
   code should still pass the existing tests; do not save a broken state to
   fix "at the end."
4. **Test.** Run the specific tests the plan said would pass. If a planned
   test does not pass, that is a finding, not a reason to keep going.
5. **Review.** Review the diff against the spec and the security checklist.
   (See `code-review` and `security-review`.) Fix findings before shipping.
6. **Ship.** The change is ready when the spec's acceptance criterion is met
   and the review found nothing unfixed. State the evidence; do not say
   "done" without it.

## When to use the full pipeline

Use it when the work is a feature or a multi-file change. For a one-line fix
or a question, the pipeline is overhead — just do the work and state the
evidence. The pipeline serves the work, not the other way around.

## The gate that matters most

The acceptance criterion from the spec is the gate. If you reach "ship" and
cannot demonstrate the criterion, the work is not done — the pipeline failed
to catch a missed requirement, and the honest move is to say so, not to
declare done and move on.
