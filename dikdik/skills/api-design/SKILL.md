---
name: api-design
description: Design and evolve API contracts with validation, compatibility and clear error semantics.
category: engineering
version: "1.0"
triggers: "api design, endpoint, request schema, response schema, http contract"
---

# Api Design

1. Inspect existing callers, routes and response conventions. Define input types, limits, authentication boundaries and success/error responses before changing handlers.

2. Validate at the boundary and reject unknown security-sensitive fields. Distinguish absent values, explicit nulls and false; truthiness is not boolean authorization.

3. Make retries and pagination explicit. Use stable identifiers and idempotency protections for operations whose repeated execution can duplicate effects.

4. Verify valid requests, malformed input, denied requests and existing callers. Preserve compatibility deliberately or document the exact migration.
