---
name: agent-evaluation
description: Evaluate agent behavior with reproducible tasks, outcome criteria and adversarial cases.
category: agentic
version: "1.0"
triggers: "agent evaluation, eval suite, benchmark, regression dataset"
---

# Agent Evaluation

1. Define the target behavior and measurable success criteria before choosing examples. Include ordinary tasks, incomplete context, malformed tool outputs, denied actions and recovery cases.

2. Separate deterministic runtime properties from model quality. Test policy enforcement with scripted models; evaluate reasoning and task quality with versioned fixtures.

3. Record model/configuration, task inputs, tool budget, completion state, current verification evidence, latency and failures. Never include secrets in fixtures or traces.

4. Compare changes on the same dataset and inspect regressions, including false completion and unsafe success. Report sample size and limitations instead of extrapolating perfection.
