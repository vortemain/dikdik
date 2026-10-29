---
name: automation-design
description: Design reliable recurring jobs with idempotency, cancellation and meaningful notifications.
category: agentic
version: "1.0"
triggers: "automation, scheduler, recurring task, background job, webhook"
---

# Automation Design

1. Define the trigger, timezone, execution identity and success condition. Inspect existing jobs before creating another schedule for the same purpose.

2. Make repeated execution safe: persist checkpoints or idempotency keys, prevent overlapping writes and bound retries/backoff. Treat webhook delivery as at-least-once unless proven otherwise.

3. Keep secrets out of job payloads and verify signed inbound events when supported. A schedule does not widen the underlying task's tool permissions.

4. Test duplicate delivery, cancellation, timeouts and restart recovery. Notify on actionable changes or failures according to user preferences, and distinguish scheduled from successfully executed.
