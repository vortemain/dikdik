---
name: planning
description: Form a request-specific plan after understanding the request and inspecting context. Adapted from obra/superpowers and addyosmani/agent-skills.
category: methodology
version: "1.0"
license: MIT
triggers: "multi-step work, feature implementation, refactor"
---

# Planning

A plan is a short, ordered list of concrete steps for *this* request, formed
after you understand the request and have looked at the relevant code. It is
not a generic pipeline ("discover, edit, test") shown to every task.

## When to plan

Plan when the work has more than one logical step that depends on the previous
one. A single edit, a question, or a "change this string" does not need a plan.

## How to plan

1. **Read first.** Use `list_files` and `read_file` on the files the request
   names or implies before planning. A plan built on assumptions about code you
   have not read is a guess.
2. **Write steps that are checkable.** Each step should be verifiable on its
   own: "Add input validation to `parse_config` returning `ValueError` for
   negative ports" is checkable; "Improve config handling" is not.
3. **Order by dependency.** If step B cannot be verified until step A is done,
   put A first. If they are independent, the order does not matter — say so.
4. **Name the verification for each step.** A syntax check, a specific test,
   or a manual check. "Run tests" is not verification unless you say *which*
   tests should pass.
5. **Keep it short.** Three to seven steps for most work. If you have fifteen,
   the request is probably more than one task — say so and do the first one.

## What a plan is not

- Not a ceremony before every message.
- Not a list of your execution engine's internal stages.
- Not a place to hide uncertainty — if a step is uncertain, mark it and plan to
  resolve the uncertainty first.

## Use the task tracker

For multi-step work, use the `task_tracker` tool to share your checklist with
the user. Update each item's status as you go. This is the visible plan, not a
separate document.
