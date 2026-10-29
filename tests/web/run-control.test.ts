import { describe, expect, test } from "bun:test";
import {
  attachmentList, buildTaskRequest, contextUsage, eventToMessages, goalState, isResumable, isTerminal,
  openConfirmations, parseGoal, parseSlash, resultStatus, slashMatches, type TaskEvent,
} from "../../dikdik/web/contracts";

const event = (seq: number, kind: string, data: Record<string, unknown> = {}): TaskEvent => ({ seq, task_id: "t", time: seq, kind, data });
const agent = { objective: "Ship it", mode: "agent" as const, verification: "syntax" as const, approveExecution: false, approveExternal: true, edits: "[]" };
const ID = "0123456789abcdef0123456789abcdef";

describe("slash commands", () => {
  test("parses names and arguments, not paths", () => {
    expect(parseSlash("/btw why is this slow?")).toEqual({ name: "btw", arg: "why is this slow?" });
    expect(parseSlash("  /Condense  ")).toEqual({ name: "condense", arg: "" });
    expect(parseSlash("/usr/bin/env python")).toBeNull();
    expect(parseSlash("fix /plan later")).toBeNull();
  });
  test("goal turns default to five and are bounded", () => {
    expect(parseGoal("--max 3 make tests pass")).toEqual({ turns: 3, goal: "make tests pass" });
    expect(parseGoal("make tests pass")).toEqual({ turns: 5, goal: "make tests pass" });
    expect(() => parseGoal("--max 30 x")).toThrow("1–20");
    expect(() => parseGoal("--max 3")).toThrow("goal");
  });
  test("menu matches commands and skills by prefix only while typing the name", () => {
    expect(slashMatches("/c").map(c => c.name)).toEqual(["code", "condense"]);
    expect(slashMatches("/", ["review"]).map(c => c.name)).toContain("review");
    expect(slashMatches("/plan now")).toEqual([]);
    expect(slashMatches("plan")).toEqual([]);
  });
});

describe("requests", () => {
  test("carry a model choice, attachments and a goal", () => {
    expect(buildTaskRequest({ ...agent, modelProfile: "p1", attachments: [ID], goalMaxTurns: 4 }))
      .toMatchObject({ model_profile: "p1", attachments: [ID], goal_max_turns: 4 });
    expect(buildTaskRequest(agent)).not.toHaveProperty("goal_max_turns");
  });
  test("goals need agent mode", () => {
    expect(() => buildTaskRequest({ ...agent, mode: "model", goalMaxTurns: 3 })).toThrow("Agent mode");
  });
});

describe("run state from events", () => {
  test("paused is terminal and resumable; completed is not resumable", () => {
    expect(isTerminal("paused")).toBe(true);
    expect(isResumable("paused")).toBe(true);
    expect(isResumable("failed")).toBe(true);
    expect(isResumable("completed")).toBe(false);
    expect(resultStatus("paused", {})).toContain("Resume");
    expect(resultStatus("paused", { pause_reason: "Run limit reached. Progress is saved; resume to continue." }))
      .toBe("Run limit reached. Progress is saved; resume to continue.");
  });
  test("context usage reads the latest model call and resets after condensing", () => {
    const events = [event(1, "agent_model", { phase: "start", context_bytes: 1024, context_limit: 4096 }), event(2, "agent_model", { phase: "start", context_bytes: 3072, context_limit: 4096 })];
    expect(contextUsage(events)).toEqual({ bytes: 3072, limit: 4096, ratio: 0.75 });
    expect(contextUsage([...events, event(3, "condensed", { summary: "s", upto_seq: 2 })])).toBeNull();
    expect(contextUsage([event(1, "agent_model", { phase: "error" })])).toBeNull();
  });
  test("goal state is the latest goal event", () => {
    const events = [event(1, "goal_status", { status: "active", turns: 0, max_turns: 3, objective: "green" }), event(5, "goal_status", { status: "achieved", turns: 2, max_turns: 3, objective: "green", reason: "tests pass" })];
    expect(goalState(events)).toEqual({ status: "achieved", turns: 2, maxTurns: 3, objective: "green", reason: "tests pass" });
    expect(goalState([])).toBeNull();
  });
  test("open approvals close when decided or when the turn ends", () => {
    const asked = [event(1, "confirmation_requested", { id: "a" }), event(2, "confirmation_requested", { id: "b" }), event(3, "confirmation_resolved", { id: "a", accepted: true })];
    expect(openConfirmations(asked)).toEqual(["b"]);
    expect(openConfirmations([...asked, event(4, "paused")])).toEqual([]);
  });
});

describe("new event rows", () => {
  test("attachments keep only well-formed entries", () => {
    expect(attachmentList([{ id: ID, name: "a.png", type: "image/png", size: 3 }, { id: "../x", name: "b" }, "junk"]))
      .toEqual([{ id: ID, name: "a.png", type: "image/png", size: 3 }]);
    expect(eventToMessages(event(2, "attachments", { files: [{ id: ID, name: "a.png", type: "image/png", size: 3 }] }), "")[0]).toMatchObject({ role: "user", kind: "attachments" });
  });
  test("approvals, hooks, subagents, goals and forks become rows", () => {
    expect(eventToMessages(event(1, "confirmation_requested", { id: "c1", tool: "run_command", risk: "high", reason: "network", arguments: { command: "curl x" } }), "")[0])
      .toMatchObject({ kind: "confirmation_requested", details: { id: "c1", tool: "run_command", risk: "high" } });
    expect(eventToMessages(event(2, "hook_blocked", { tool: "run_command", message: "no pushes" }), "")[0]).toMatchObject({ title: "Blocked by a hook", body: "no pushes" });
    expect(eventToMessages(event(3, "subagent", { phase: "end", agent: "explorer", findings: "found it" }), "")[0]).toMatchObject({ title: "explorer reported back", body: "found it" });
    expect(eventToMessages(event(4, "goal_status", { status: "capped", turns: 3, max_turns: 3 }), "")[0]?.title).toBe("Goal stopped at its turn limit · 3/3");
    expect(eventToMessages(event(5, "user_message", { text: "Keep working toward the goal: x", goal: true }), "")[0]).toMatchObject({ role: "system", kind: "goal_turn" });
    expect(eventToMessages(event(6, "paused", { paused: true }), "")[0]).toMatchObject({ state: "paused", id: "terminal-6" });
    expect(eventToMessages(event(7, "forked", { from_task: "a", from_seq: 4 }), "")[0]?.title).toBe("Forked from another conversation");
  });
  test("hook executions retain decision and correlation evidence", () => {
    const row = eventToMessages(event(8, "hook_execution", { hook_id: "project:freeze", event: "tool.before", decision: "block",
      reason: "maintenance", source: "project", priority: -1, status: "completed", duration_ms: 3,
      run_id: "run", dispatch_id: "dispatch", call_id: "call", payload_sha256: "digest", data: { policy: "freeze" } }), "")[0];
    expect(row).toMatchObject({ role: "system", title: "Hook: project:freeze", body: "tool.before · block · maintenance",
      details: { source: "project", priority: -1, status: "completed", run_id: "run", dispatch_id: "dispatch", call_id: "call", data: { policy: "freeze" } } });
    expect(eventToMessages(event(9, "hook_event", { decision: "allow", hook_count: 0 }), "")).toEqual([]);
  });
});
