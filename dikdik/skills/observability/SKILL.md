---
name: observability
description: Instrument agent and service execution with correlated, bounded and privacy-aware telemetry.
category: agentic
version: "1.0"
triggers: "observability, tracing, telemetry, audit log, execution trace"
---

# Observability

1. Define the question each event must answer: what ran, why, under which task/run/call identity, for how long and with what outcome.

2. Use structured fields and stable correlation ids across parent tasks, delegated work and tool calls. Distinguish policy denial, timeout, callback error and tool failure.

3. Prefer metadata and input digests over raw prompts, credentials or command output. Bound payload sizes and make audit-write failure behavior explicit.

4. Test event ordering and correlation under success, cancellation and failure. Verify that the trace supports the reported completion state rather than merely showing that execution started.
