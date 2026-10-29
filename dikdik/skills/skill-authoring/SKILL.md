---
name: skill-authoring
description: Create focused reusable agent skills and resources without duplicating existing workflows.
category: agentic
version: "1.0"
triggers: "create skill, skill authoring, SKILL.md, reusable workflow"
---

# Skill Authoring

1. Inspect the existing skill catalog and identify the missing decision or workflow. Extend an existing skill when the new material serves the same purpose.

2. Use a lowercase hyphenated folder with SKILL.md, name and a precise description of when it applies. Put short routing keywords in triggers for Dikdik's selector.

3. Keep essential workflow in the body and conditional detail in references, scripts or assets only when they serve a concrete use. Scripts require ordinary execution permission and are never run on installation.

4. Validate discovery, explicit invocation, resource paths and context-budget behavior. Confirm that disabled skills stay unavailable and that skill content cannot modify security policy.
