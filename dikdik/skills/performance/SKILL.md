---
name: performance
description: Investigate latency, throughput and memory with representative measurements.
category: engineering
version: "1.0"
triggers: "performance, slow query, latency, memory usage, optimize"
---

# Performance

1. Define the workload and user-visible target. Measure a baseline with representative input sizes before changing algorithms, caches or concurrency.

2. Find the actual bottleneck using timing, query plans or profiling. Separate model/network time from local CPU, filesystem and database work.

3. Choose the smallest change that addresses the measured cost. Bound caches, queues, retries and worker pools; include invalidation and backpressure behavior.

4. Compare the same workload after the change and check correctness under load. Report distributions or worst cases where averages hide a regression.
