import { expect, test } from "bun:test";
import { codeLanguage, eventsToChat, isCodeTool, observationText } from "../../dikdik/web/contracts";
import { stepLabel } from "../../dikdik/web/chat-widgets";

const event = (seq: number, kind: string, data: Record<string, unknown>) => ({ seq, task_id: "t", time: 1000 + seq, kind, data });

test("run_code names its language, and run_python from earlier conversations is Python", () => {
  expect(isCodeTool("run_code")).toBe(true);
  expect(isCodeTool("run_python")).toBe(true);
  expect(isCodeTool("run_command")).toBe(false);
  expect(codeLanguage("run_code", { language: "JS" })).toEqual({ id: "javascript", label: "JavaScript" });
  expect(codeLanguage("run_code", { language: "c#" })).toEqual({ id: "csharp", label: "C#" });
  expect(codeLanguage("run_python", { code: "print(1)" })).toEqual({ id: "python", label: "Python" });
  expect(codeLanguage("run_code", { language: "zig" })).toEqual({ id: "", label: "zig" });
  expect(codeLanguage("run_code", { language: "<img src=x>" })).toEqual({ id: "", label: "code" });
});

test("a run_code step and its result read like a command's", () => {
  const messages = eventsToChat([
    event(1, "agent_tool", { phase: "start", id: "a", name: "run_code", arguments: { language: "go", code: "package main\n\nfunc main() {}\n" } }),
    event(2, "agent_tool", { phase: "end", id: "a", name: "run_code", failed: true,
                             observation: { status: "failed", exit_code: 1, language: "go", phase: "build", output: "undefined: x", changed_files: [], rejected_changes: [] } }),
  ], "Work");
  const call = messages.find(m => m.kind === "agent_tool_call")!;
  expect(stepLabel(call)).toEqual({ icon: "terminal", title: "Ran Go", arg: "2 lines" });
  expect(observationText("run_code", { status: "passed", exit_code: 0, language: "javascript", tool_calls: 2, output: "42", changed_files: [], rejected_changes: [] }))
    .toBe("exit 0 · passed · JavaScript · 2 tool calls\n\n42");
  expect(observationText("run_code", { status: "failed", exit_code: 1, language: "go", phase: "build", output: "undefined: x" }))
    .toBe("exit 1 · failed · Go · build failed\n\nundefined: x");
  expect(observationText("run_python", { status: "passed", exit_code: 0, output: "ok" })).toBe("exit 0 · passed · Python\n\nok");
  expect(observationText("run_code", { error: "Ruby is not installed on this computer" })).toBe("Error: Ruby is not installed on this computer");
});
