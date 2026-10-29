---
name: brainstorming
description: Explore the problem space before writing code. Adapted from obra/superpowers.
category: methodology
version: "1.0"
license: MIT
triggers: "new feature, ambiguous request, design decision"
---

# Brainstorming

Before writing code for a non-trivial request, think through the problem. This
is not ceremony: it prevents the most common agent failure — solving the wrong
problem or the right problem the wrong way.

## When to brainstorm

- The request is ambiguous or could mean several things.
- The request implies a design decision (data model, API shape, UX flow).
- The request is a new feature, not a small fix.
- You are about to create more than one file.

Skip brainstorming for a typo, a one-line fix, a direct question, or when the
user has already specified the exact change.

## How to brainstorm

1. **Restate the goal in one sentence.** If you cannot, you do not understand it
   yet — ask the user a clarifying question instead of guessing.
2. **List two to four plausible approaches.** Each is one or two sentences with a
   trade-off, not a full design. Name what each approach optimizes for
   (simplicity, performance, compatibility, etc.).
3. **Pick one and say why.** Prefer the simplest approach that satisfies the
   request. A simpler change is easier to review, revert, and build on.
4. **Note the riskiest assumption.** State the one assumption that, if wrong,
   invalidates the plan. The first verification step should check it.

## What brainstorming is not

- It is not a generic checklist shown to every request.
- It is not a substitute for reading the actual code.
- It does not produce code; it produces a direction.

## Output

Keep the brainstorm brief — a few sentences in the reply, not a separate
document. If the user can read it and say "yes, do that," it was the right
length. If they have to ask "so what are you going to do?", it was too long or
too vague.
