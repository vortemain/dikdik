---
name: data-engineering
description: Change schemas, migrations and storage workflows with consistency and recovery checks.
category: engineering
version: "1.0"
triggers: "database, migration, schema change, transaction, sqlite"
---

# Data Engineering

1. Inspect the existing schema, constraints and transaction ownership. Identify which reads and writes must observe a consistent state.

2. Design a migration that handles both fresh and existing installations. Preserve user data and preferences; make repeated startup safe and avoid destructive default migrations.

3. Use parameterized statements, explicit constraints and bounded queries. Keep slow external operations outside write transactions whenever consistency permits.

4. Test migration from the previous schema, repeated migration, rollback or failure recovery, and concurrent access relevant to the application.
