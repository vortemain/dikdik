import { expect, test } from "bun:test";
import { conversationLabel, eventsToChat, eventToMessages } from "../../dikdik/web/contracts";

test("eventsToChat maps the objective to a user message and terminal events to system messages", () => {
  const objective = "Fix the add function.";
  const events = [
    { seq: 1, task_id: "t1", time: 1000, kind: "queued", data: {} },
    { seq: 2, task_id: "t1", time: 1001, kind: "plan", data: { steps: ["inspect", "edit", "test"] } },
    { seq: 3, task_id: "t1", time: 1002, kind: "agent_message", data: { content: "I'll inspect the file first." } },
    { seq: 4, task_id: "t1", time: 1003, kind: "agent_tool_call", data: { name: "read_file", arguments: { path: "calculator.py" } } },
    { seq: 5, task_id: "t1", time: 1004, kind: "agent_tool_result", data: { tool: "read_file", result: "def add(a, b):\n    return a - b\n" } },
    { seq: 6, task_id: "t1", time: 1005, kind: "syntax", data: { kind: "python", status: "passed" } },
    { seq: 7, task_id: "t1", time: 1006, kind: "completed", data: { diff: "diff --git a/app.py b/app.py\n" } },
  ];
  const messages = eventsToChat(events, objective);
  expect(messages.map(m => m.role)).toEqual(["user", "assistant", "assistant", "tool", "tool", "tool", "system"]);
  expect(messages[0]?.body).toBe(objective);
  expect(messages[1]?.title).toBe("Plan");
  expect(messages[2]?.body).toBe("I'll inspect the file first.");
  expect(messages[3]?.title).toContain("read_file");
  expect(messages[4]?.body).toContain("return a - b");
  expect(messages[5]?.title).toContain("Syntax check");
  expect(messages[6]?.state).toBe("completed");
});

test("eventToMessages ignores unknown event kinds rather than executing or inventing content", () => {
  const msgs = eventToMessages({ seq: 1, task_id: "t1", time: 0, kind: "unknown_event", data: { evil: "<script>" } }, "obj");
  expect(msgs).toEqual([]);
});

test("eventToMessages sanitizes control characters in agent message content", () => {
  const withControl = eventToMessages({ seq: 1, task_id: "t1", time: 0, kind: "agent_message", data: { content: "ok\ngood" } }, "obj");
  expect(withControl[0]?.body).toBe("ok\ngood"); // newlines are fine
  const withBad = eventToMessages({ seq: 2, task_id: "t1", time: 0, kind: "agent_message", data: { content: "bad\x00data" } }, "obj");
  expect(withBad).toEqual([]); // NUL content is dropped, not rendered
});

test("conversationLabel truncates long objectives for the sidebar", () => {
  expect(conversationLabel({ id: "abc1234567890", state: "completed", objective: "Short obj" })).toBe("Short obj");
  const long = "x".repeat(80);
  const label = conversationLabel({ id: "abc", state: "running", objective: long });
  expect(label.length).toBeLessThan(long.length); // truncated
  expect(label.startsWith("x".repeat(60))).toBe(true);
});

test("tool result strings are truncated to a safe bound", () => {
  const big = "x".repeat(20000);
  const msgs = eventToMessages({ seq: 1, task_id: "t1", time: 0, kind: "agent_tool_result", data: { tool: "read_file", result: big } }, "obj");
  expect(msgs[0]?.body?.length ?? 0).toBeLessThanOrEqual(2100);
});

test("cancellation and recovery events surface as system messages, not tool calls", () => {
  const cancel = eventToMessages({ seq: 1, task_id: "t1", time: 0, kind: "cancellation_requested", data: {} }, "obj");
  expect(cancel[0]?.role).toBe("system");
  const recovery = eventToMessages({ seq: 2, task_id: "t1", time: 0, kind: "recovery_cleanup", data: { warning: "container removed" } }, "obj");
  expect(recovery[0]?.role).toBe("system");
  expect(recovery[0]?.title).toContain("Recovered");
  const interrupt = eventToMessages({ seq: 3, task_id: "t1", time: 0, kind: "interrupt_requested", data: {} }, "obj");
  expect(interrupt[0]).toMatchObject({ role: "system", title: "Stopping the current action" });
});
