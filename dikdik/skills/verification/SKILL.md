---
name: verification
description: Match completion claims to current evidence and distinguish syntax, tests and objective acceptance.
category: agentic
version: "1.0"
triggers: "verify, verification, acceptance criteria, before completion"
---

# Verification

1. Translate the requested behavior into a check observable from outside the implementation. Prefer an existing relevant test or a focused regression check over tests that repeat internal steps.

2. Verify the current candidate after the final relevant edit. A passed check from an earlier candidate is stale; distinguish test execution from syntax-only validation.

3. Inspect unsuccessful and inconclusive statuses explicitly. A command that exits successfully without discovering tests does not prove the requested behavior.

4. Report what was checked, its result and any remaining acceptance gap. Use run_tests and the runtime completion gate; a skill or hook cannot manufacture verification evidence.
