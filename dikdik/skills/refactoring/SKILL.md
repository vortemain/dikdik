---
name: refactoring
description: Change internal structure while preserving observed behavior and compatibility.
category: engineering
version: "1.0"
triggers: "refactor, extract module, simplify code, remove duplication"
---

# Refactoring

1. State the behavior and public interfaces that must remain stable. Inspect callers and existing tests before moving responsibilities.

2. Choose a narrow structural goal such as removing duplicate validation or isolating a side effect. Avoid combining broad refactoring with unrelated behavior changes.

3. Move in small steps with compatibility shims only where real callers require them. Keep error behavior, ordering and persistence semantics explicit.

4. Run the affected behavioral checks and review the diff for accidental permission or data changes. Remove obsolete paths only after confirming their callers are gone.
