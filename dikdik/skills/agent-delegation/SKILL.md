---
name: agent-delegation
description: Delegate bounded repository investigations to available read-only helper agents.
category: agentic
version: "1.0"
triggers: "delegate, subagent, parallel investigation, helper agent"
---

# Agent Delegation

1. Use delegate only when the helper can answer a concrete independent question. Give the available agent name, exact question, relevant scope and expected evidence.

2. Dikdik helpers are read-only: assign inspection, dependency mapping or review findings. Keep implementation and tool authorization with the parent task.

3. Request file paths and observed facts. Check important findings against current files before basing a write or completion claim on them.

4. Merge findings into the parent plan, remove duplicate investigations and stop when further delegation would cost more context than the remaining work.
