import { expect, test } from "bun:test";
import { deriveProgress, type TaskEvent } from "../../dikdik/web/contracts";

function ev(seq: number, kind: string, data: Record<string, unknown> = {}, time = 1000): TaskEvent {
  return { seq, task_id: "t", time, kind: kind as TaskEvent["kind"], data };
}

test("deriveProgress: empty events → zeroed, not running", () => {
  const state = deriveProgress([], 1000);
  expect(state.modelCalls).toBe(0);
  expect(state.toolCalls).toBe(0);
  expect(state.isRunning).toBe(false);
  expect(state.currentStep).toBe("");
  expect(state.context).toBeNull();
  expect(state.contextHealth).toBeNull();
  expect(state.recentRecoveries.length).toBe(0);
});

test("deriveProgress: model start+end → counts, not running after end", () => {
  const events = [
    ev(1, "agent_model", { phase: "start", model_calls: 1, context_bytes: 10000, context_limit: 131072 }),
    ev(2, "agent_model", { phase: "end", model_calls: 1, usage_tokens: 0 }),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.modelCalls).toBe(1);
  expect(state.isRunning).toBe(false);
  expect(state.context).not.toBeNull();
  expect(state.context!.bytes).toBe(10000);
  expect(state.context!.ratio).toBeLessThan(1);
});

test("deriveProgress: model start only → running, currentStep is Thinking", () => {
  const events = [ev(1, "agent_model", { phase: "start", model_calls: 3, context_bytes: 50000, context_limit: 131072 })];
  const state = deriveProgress(events, 1000);
  expect(state.isRunning).toBe(true);
  expect(state.modelCalls).toBe(3);
  expect(state.currentStep).toBe("Thinking…");
});

test("deriveProgress: tool start → running, currentStep reflects tool name", () => {
  const events = [
    ev(1, "agent_model", { phase: "start", model_calls: 1 }),
    ev(2, "agent_model", { phase: "end", model_calls: 1 }),
    ev(3, "agent_tool", { phase: "start", name: "run_tests", tool_calls: 1 }),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.isRunning).toBe(true);
  expect(state.currentStep).toBe("Running tests…");
});

test("deriveProgress: tool end → not running, toolCalls counted", () => {
  const events = [
    ev(1, "agent_tool", { phase: "start", name: "edit_file", tool_calls: 5 }),
    ev(2, "agent_tool", { phase: "end", tool_calls: 5 }),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.isRunning).toBe(false);
  expect(state.toolCalls).toBe(5);
});

test("deriveProgress: context_health → health level set", () => {
  const events = [ev(1, "context_health", { level: "high", ratio: 0.65, bytes: 85000, limit: 131072 })];
  const state = deriveProgress(events, 1000);
  expect(state.contextHealth).toBe("high");
});

test("deriveProgress: context_compacted → context cleared", () => {
  const events = [
    ev(1, "agent_model", { phase: "start", model_calls: 1, context_bytes: 100000, context_limit: 131072 }),
    ev(2, "context_compacted", {}),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.context).toBeNull();
});

test("deriveProgress: checkpoint_heartbeat → lastCheckpointSeconds computed", () => {
  const events = [ev(1, "checkpoint_heartbeat", { model_calls: 5 })];
  const state = deriveProgress(events, 1065);
  expect(state.lastCheckpointSeconds).toBe(65);
});

test("deriveProgress: recovery events → recentRecoveries captured (max 2)", () => {
  const events = [
    ev(1, "recovery", { reason: "Failed to edit file" }),
    ev(2, "recovery", { reason: "Search returned empty" }),
    ev(3, "recovery", { reason: "Third error" }),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.recentRecoveries.length).toBe(2);
  expect(state.recentRecoveries[0]).toBe("Search returned empty");
  expect(state.recentRecoveries[1]).toBe("Third error");
});

test("deriveProgress: terminal event → not running", () => {
  const events = [
    ev(1, "agent_model", { phase: "start", model_calls: 1 }),
    ev(2, "paused", {}),
  ];
  const state = deriveProgress(events, 1000);
  expect(state.isRunning).toBe(false);
});
