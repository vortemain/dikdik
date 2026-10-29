---
name: structured-output
description: Design and validate bounded machine-readable model, tool and plugin outputs.
category: agentic
version: "1.0"
triggers: "structured output, json schema, typed output, validation contract"
---

# Structured Output

1. Define a small explicit schema with required fields, allowed values and size/depth limits. Keep display text separate from fields that drive execution.

2. Validate runtime values even when a provider advertises JSON/schema support. Reject unknown control fields, non-finite numbers and booleans masquerading as numeric authorization.

3. Treat missing, malformed and oversized output as a defined failure. Never silently convert arbitrary text into an allow decision or successful verification.

4. Test invalid enums, wrong types, extra fields, truncation and nested payloads. Preserve a safe diagnostic and correlation id without logging sensitive raw input.
