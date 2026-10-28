import { describe, expect, test } from "bun:test";
import { eventToMessages, terminalTranscript, type TaskEvent } from "../../dikdik/web/contracts";

const event = (seq: number, kind: string, data: Record<string, unknown>): TaskEvent =>
  ({ seq, task_id: "task", time: seq, kind, data });

describe("terminal transcript", () => {
  test("ANSI colors and title escapes cannot hide output or become markup", () => {
    const view = terminalTranscript([
      event(1, "terminal_command", { command_id: "color", command: "echo colored" }),
      event(2, "terminal_output", { command_id: "color", output: "\x1b]0;title\x07\x1b[31mred <script>\x1b[0m\n", final: true, status: "failed", error: "Could not start shell" }),
    ]);
    expect(view.commands[0]?.output).toBe("red <script>\nCould not start shell\n");
  });
  test("rebuilds ordered command output and final status after reload", () => {
    const events = [
      event(1, "terminal_command", { command_id: "one", command: "printf hi" }),
      event(2, "terminal_output", { command_id: "one", order: 1, output: "hi", final: false }),
      event(3, "terminal_output", { command_id: "one", order: 2, output: "", final: true, exit_code: 0, status: "passed" }),
      event(4, "terminal_command", { command_id: "two", command: "false" }),
      event(5, "terminal_output", { command_id: "two", order: 1, output: "nope\n", final: true, exit_code: 2, status: "failed" }),
    ];
    const view = terminalTranscript(events);
    expect(view.commands).toHaveLength(2);
    expect(view.commands[0]).toMatchObject({ command: "printf hi", output: "hi", running: false, exitCode: 0 });
    expect(view.text).toContain("$ false\nnope\n\n[exit 2]");
  });

  test("keeps running commands visible and rejects malformed untrusted fields", () => {
    const view = terminalTranscript([
      event(1, "terminal_output", { command_id: "missing", output: "ignored" }),
      event(2, "terminal_command", { command_id: "ok", command: "echo safe" }),
      event(3, "terminal_output", { command_id: "ok", output: "bad\u0000data" }),
    ]);
    expect(view.commands[0]).toMatchObject({ command: "echo safe", output: "", running: true });
    expect(view.text).toEndWith("[running]");
    expect(eventToMessages(event(4, "terminal_output", { command_id: "ok", output: "secret" }), "objective")).toEqual([]);
  });

  test("preserves backend chunks larger than 64 KiB within the display cap", () => {
    const output = "x".repeat(70 * 1024);
    const view = terminalTranscript([
      event(1, "terminal_command", { command_id: "large", command: "large-output" }),
      event(2, "terminal_output", { command_id: "large", output, final: false }),
      event(3, "terminal_output", { command_id: "large", output: "", final: true, exit_code: 0, status: "passed" }),
    ]);
    expect(view.commands[0]?.output).toBe(output);
  });
});
