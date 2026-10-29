import { expect, test } from "bun:test";
import { usageMetrics, type TaskEvent } from "../../dikdik/web/contracts";

let seq = 0;
const event = (kind: string, data: Record<string, unknown> = {}): TaskEvent => ({ seq: ++seq, task_id: "t", time: seq, kind, data });

test("usage groups provider reports by turn and never invents cost", () => {
  const metrics = usageMetrics([
    event("running"),
    event("agent_started", { model: { profile: "Strong" } }),
    event("agent_model", { phase: "start", context_bytes: 1024, context_limit: 4096 }),
    event("agent_model", { phase: "end", usage_tokens: 70, usage: { input_tokens: 50, output_tokens: 20, total_tokens: 70, cache_read_tokens: 10 } }),
    event("agent_model", { phase: "end", usage_tokens: 140, usage: { input_tokens: 60, output_tokens: 10, total_tokens: 70 } }),
    event("completed"),
    event("running"),
    event("agent_started", { model: { profile: "Fast" } }),
    event("agent_model", { phase: "end", usage_tokens: 30, usage: { total_tokens: 30, reported_cost: 0.002, secret: "ignored" } }),
  ]);
  expect(metrics.turns.map(t => [t.turn, t.profile, t.calls, t.totalTokens])).toEqual([[1, "Strong", 2, 140], [2, "Fast", 1, 30]]);
  expect(metrics.task).toEqual({ inputTokens: 110, outputTokens: 30, cacheReadTokens: 10, totalTokens: 170, reportedCost: 0.002 });
  expect(metrics.turns[0]!.reportedCost).toBeUndefined();
  expect(metrics.context?.ratio).toBe(0.25);
});

test("older events with only a running total still count; nothing reported means no turns", () => {
  const legacy = usageMetrics([event("running"), event("agent_model", { phase: "end", usage_tokens: 70 }), event("agent_model", { phase: "end", usage_tokens: 100 })]);
  expect(legacy.turns[0]?.totalTokens).toBe(100);
  expect(usageMetrics([event("running"), event("agent_model", { phase: "end", usage_tokens: 0 })]).turns).toEqual([]);
  expect(usageMetrics([event("agent_model", { phase: "end", usage: { input_tokens: -5, total_tokens: "9" } })]).turns).toEqual([]);
});

test("truncated and failed request usage is included without double counting retries", () => {
  const metrics = usageMetrics([event("running"),
    event("agent_model", { phase: "retry", usage_tokens: 100, usage: { total_tokens: 100, reported_cost: .001 } }),
    event("agent_model", { phase: "end", usage_tokens: 160, usage: { total_tokens: 60, reported_cost: .002 } }),
    event("agent_model", { phase: "error", usage_tokens: 180, usage: { total_tokens: 20 } }),
  ]);
  expect(metrics.task.totalTokens).toBe(180);
  expect(metrics.task.reportedCost).toBeCloseTo(.003);
  expect(metrics.turns[0]?.calls).toBe(3);
});
