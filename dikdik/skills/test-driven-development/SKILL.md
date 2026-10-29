---
name: test-driven-development
description: Test-driven development for changes that should be verified. Adapted from obra/superpowers.
category: methodology
version: "1.0"
license: MIT
triggers: "bug, fix, logic, function, regression, test"
---

# Test-driven development

For a change that has a clear expected behavior, write the test that fails
first, then make it pass with the smallest change. This is not dogma — it is
the cheapest way to know the change does what was asked.

## When to use TDD

- A bug fix: the test reproduces the bug, the fix makes it pass.
- A new function with a specified contract (inputs → outputs).
- A regression-prone change (parsing, validation, financial logic).

Skip TDD when the behavior cannot be pinned by a test: exploratory refactors,
visual layout, prose, or "make it feel better." For those, verify by reading
and by showing the user the result.

## The loop

1. **Write one failing test.** It should fail for the right reason (the bug or
   the missing feature), not a syntax error or a missing import.
2. **Run it.** Confirm it fails. If it passes already, you either already fixed
   it or the test does not exercise the change — find out which.
3. **Make the smallest change that passes the test.** Do not add the next
   feature in the same edit. Resist generalizing beyond the test.
4. **Run the full relevant suite.** Not just the new test — the change may have
   broken a neighbor. A passing full suite is the evidence, not the new test
   alone.
5. **Refactor only when green.** Both tests must pass before and after.

## Rules that keep it honest

- **Never weaken an existing test to make a change pass.** If a test blocks a
  legitimate change, the change or the test is wrong — discuss it, do not
  silently relax the assertion.
- **A passing syntax check is not a passing test.** Say "syntax checked only"
  when that is all you have; never imply tests passed if they did not run.
- **One test per behavior.** Do not bundle unrelated assertions into one test
  to make the count look higher; a failure should point at one behavior.

## When the user did not ask for TDD

For a request that says "fix the bug," TDD is the right tool because the test
proves the fix. For "explore why this is slow," it is not — the output is an
explanation, not a passing test. Match the method to the request.
