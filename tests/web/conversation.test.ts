import { expect, test } from "bun:test";
import { conversationLabel, eventsToChat, observationText, transcriptMarkdown } from "../../dikdik/web/contracts";

const event = (seq: number, kind: string, data: Record<string, unknown>) => ({ seq, task_id: "t", time: 1000 + seq, kind, data });

test("real agent events become tool calls paired with readable results", () => {
  const messages = eventsToChat([
    event(1, "queued", {}),
    event(2, "agent_message", { content: "Reading the file." }),
    event(3, "agent_model", { phase: "start", model_calls: 1 }),
    event(4, "agent_tool", { phase: "start", id: "a", name: "read_file", arguments: { path: "calc.py" } }),
    event(5, "agent_tool", { phase: "end", id: "a", name: "read_file", failed: false, observation: { path: "calc.py", lines: ["def add(a, b):", "    return a - b"] } }),
    event(6, "agent_tool", { phase: "end", id: "b", name: "edit_file", failed: true, observation: { error: "Replacement must match exactly once" } }),
    event(7, "agent_check", { kind: "unittest", status: "passed" }),
    event(8, "agent_model", { phase: "error", error: "Model request failed" }),
  ], "Fix it");
  expect(messages.map(m => [m.role, m.kind])).toEqual([
    ["user", "queued"], ["assistant", "agent_message"], ["tool", "agent_tool_call"], ["tool", "agent_tool_result"],
    ["tool", "agent_tool_result"], ["tool", "syntax"], ["system", "agent_model"],
  ]);
  expect(messages[2]?.title).toBe("Tool call: read_file");
  expect(messages[2]?.details).toEqual({ path: "calc.py" });
  expect(messages[3]?.body).toBe("def add(a, b):\n    return a - b");
  expect(messages[4]?.state).toBe("tool_failed");
  expect(messages[4]?.body).toBe("Error: Replacement must match exactly once");
  expect(messages[5]?.title).toBe("Check: unittest");
  expect(messages[6]?.title).toBe("Model request failed");
});

test("follow-up messages render as user turns, marked when queued", () => {
  const messages = eventsToChat([event(1, "queued", {}), event(9, "user_message", { text: "Now add tests", queued: true }), event(10, "user_message", { text: "" })], "First");
  expect(messages.map(m => m.body)).toEqual(["First", "Now add tests"]);
  expect(messages[1]?.title).toBe("Queued");
});

test("observationText formats each tool's output", () => {
  expect(observationText("list_files", { files: ["a.py", "b.py"] })).toBe("a.py\nb.py");
  expect(observationText("list_files", { files: [] })).toBe("(no files)");
  expect(observationText("search_text", { matches: [{ path: "a.py", line: 3, text: "x = 1" }], truncated: true })).toBe("a.py:3: x = 1\n…(more matches)");
  expect(observationText("search_text", { matches: [] })).toBe("(no matches)");
  expect(observationText("create_file", { created: "new.py", verification_invalidated: true })).toBe("Created new.py");
  expect(observationText("run_tests", { checks: [{ kind: "python_syntax", status: "passed" }, { kind: "unittest", status: "failed", output: "FAIL: test_add" }] }))
    .toBe("python_syntax: passed\n\nunittest: failed\nFAIL: test_add");
  expect(observationText("finish", { completed: true })).toBe("Finished");
  expect(observationText("run_command", { status: "failed", exit_code: 2, output: "boom", changed_files: ["a.py"], rejected_changes: ["tests/test_a.py"] }))
    .toBe("exit 2 · failed\nKept changes: a.py\nRejected changes: tests/test_a.py\n\nboom");
  expect(observationText("run_command", { status: "blocked", exit_code: null, error: "Docker CLI unavailable", changed_files: [], rejected_changes: [] }))
    .toBe("exit – · blocked\nDocker CLI unavailable");
  expect(observationText("read_file", { truncated: true, preview: "{\"lines\"" })).toBe("{\"lines\"\n…(truncated)");
  expect(observationText("unknown", { a: 1 })).toBe("{\n  \"a\": 1\n}");
});

test("a renamed task shows its title; otherwise the objective", () => {
  expect(conversationLabel({ id: "abcdef123456", state: "completed", objective: "Fix addition", title: "Addition fix" })).toBe("Addition fix");
  expect(conversationLabel({ id: "abcdef123456", state: "completed", objective: "Fix addition", title: "" })).toBe("Fix addition");
  expect(conversationLabel({ id: "abcdef123456", state: "completed" })).toBe("abcdef12");
});

test("transcript export includes turns, tool output and the diff", () => {
  const messages = eventsToChat([
    event(1, "queued", {}),
    event(2, "agent_message", { content: "Done." }),
    event(3, "agent_tool", { phase: "end", id: "a", name: "list_files", observation: { files: ["a.py"] } }),
    event(4, "completed", { tests_verified: false }),
  ], "Fix it");
  const text = transcriptMarkdown("Fix it", messages, "diff --git a/a.py b/a.py\n+x\n");
  expect(text).toContain("# Fix it");
  expect(text).toContain("## You · ");
  expect(text).toContain("## Dikdik · ");
  expect(text).toContain("Done.");
  expect(text).toContain("- **Tool result: list_files**");
  expect(text).toContain("### Task completed");
  expect(text).toContain("```diff\ndiff --git a/a.py b/a.py\n+x\n```");
});

test("the agent profile travels only with agent tasks", async () => {
  const { buildTaskRequest } = await import("../../dikdik/web/contracts");
  const base = { objective: "Go", verification: "syntax" as const, approveExecution: false, approveExternal: true, edits: "[]" };
  expect(buildTaskRequest({ ...base, mode: "agent", agentProfile: "p1" }).agent_profile).toBe("p1");
  expect(buildTaskRequest({ ...base, mode: "agent", agentProfile: "" }).agent_profile).toBeUndefined();
  expect(buildTaskRequest({ ...base, mode: "model", agentProfile: "p1" }).agent_profile).toBeUndefined();
});
