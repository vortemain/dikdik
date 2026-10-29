---
name: release-management
description: Prepare reviewable releases with compatibility, rollout and rollback evidence.
category: engineering
version: "1.0"
triggers: "release, deployment, rollout, publish, ship to production"
---

# Release Management

1. Identify the release artifact, target environment, acceptance checks and compatibility impact. Separate local preparation from publishing or deployment.

2. Build from the intended revision and inspect generated/package contents. Verify configuration, migrations and secrets handling using the project's existing process.

3. Prepare a concrete change summary and rollback or recovery procedure proportionate to the change. Check observability for detecting a failed rollout.

4. Perform external publication only within explicit user authorization and tool policy. Record the actual deployed version and verification outcome; prepared is not deployed.
