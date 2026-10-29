import { describe, expect, test } from "bun:test";
import { commandActivity, eventToMessages, terminalTranscript, type TaskEvent } from "../../dikdik/web/contracts";
import { shellTokens, terminalRuns } from "../../dikdik/web/terminal-format";
import { TerminalRenderer, terminalStatus } from "../../dikdik/web/terminal-render";

const event = (seq: number, kind: string, data: Record<string, unknown>): TaskEvent =>
  ({ seq, task_id: "task", time: seq, kind, data });

describe("terminal transcript", () => {
  test('silent commands explain elapsed time, timeout and Stop without claiming progress', () => {
    const events = [event(1, 'terminal_command', { command_id: 'quiet', command: 'find . -name x', timeout: 60 })];
    expect(commandActivity(events, 2)).toBe('Command running · 1s · Timeout 60s');
    expect(commandActivity(events, 20)).toBe('Command running · 19s · No output for 19s · Timeout 60s · Use Stop to interrupt.');
    events.push(event(19, 'terminal_output', { command_id: 'quiet', output: 'found a file', final: false }));
    expect(commandActivity(events, 20)).not.toContain('No output');
    events.push(event(21, 'terminal_output', { command_id: 'quiet', final: true, status: 'timed_out' }));
    expect(commandActivity(events, 22)).toBeNull();
  });

  test('a stopped turn cannot leave an agent command marked running forever', () => {
    const events = [event(1, 'terminal_command', { command_id: 'lost', command: 'slow command' }), event(3, 'paused', {})];
    const view = terminalTranscript(events);
    expect(view.commands[0]?.running).toBe(false);
    expect(view.text).toContain('without a recorded command result');
    expect(view.text).not.toContain('[running]');
    expect(commandActivity(events, 50)).toBeNull();
  });

  test('manual terminal commands can outlive the conversation turn', () => {
    const events = [event(1, 'terminal_command', { command_id: 'manual', command: 'watch files', kind: 'command' }), event(3, 'completed', {})];
    expect(terminalTranscript(events).commands[0]?.running).toBe(true);
    expect(commandActivity(events, 20)).toContain('Stop is available in Terminal.');
  });
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

  test("ANSI styling survives split records and plain copy contains no escapes", () => {
    const view = terminalTranscript([
      event(1, "terminal_command", { command_id: "split", command: "echo '<script>'" }),
      event(2, "terminal_output", { command_id: "split", output: "\x1b[3" }),
      event(3, "terminal_output", { command_id: "split", output: "1;1mred\x1b[0m plain\x1b]8;;https://untrusted.test" }),
      event(4, "terminal_output", { command_id: "split", output: "\x1b\\ link\x1b]8;;\x1b\\", final: true, exit_code: 0, status: "passed" }),
    ]);
    const command = view.commands[0]!;
    expect(command.output).toBe("red plain link");
    expect(command.outputRuns[0]).toEqual({ text: "red", bold: true, foreground: "var(--terminal-color-1)" });
    expect(view.text).toBe("$ echo '<script>'\nred plain link");
    expect(view.text).not.toContain("\x1b");
  });

  test("supports palette and RGB color with resets; conceal and cursor escapes cannot hide diagnostics", () => {
    const runs = terminalRuns("\x1b[38;5;196mred\x1b[48;2;10;20;30;4munderlined\x1b[0mnormal\x1b[8mvisible\x1b[2Jtail");
    expect(runs[0]).toMatchObject({ text: "red", foreground: "rgb(255, 0, 0)" });
    expect(runs[1]).toMatchObject({ text: "underlined", foreground: "rgb(255, 0, 0)", background: "rgb(10, 20, 30)", underline: true });
    expect(runs.map(run => run.text).join("")).toBe("redunderlinednormalvisibletail");
    expect(runs[2]?.foreground).toBeUndefined();
    expect(terminalRuns("\x1b[38;2;999;0;0minvalid")[0]?.foreground).toBeUndefined();
    expect(terminalRuns("\x1b[38:2::10:20:30mcolor")[0]?.foreground).toBe("rgb(10, 20, 30)");
    expect(terminalRuns("safe\x1b]0;unfinished").map(run => run.text).join("")).toBe("safe");
  });

  test("caps output styling work while preserving every visible character", () => {
    const runs = terminalRuns("\x1b[31mx\x1b[32my".repeat(8000));
    expect(runs.length).toBeLessThanOrEqual(4096);
    expect(runs.map(run => run.text).join("")).toBe("xy".repeat(8000));
  });

  test("command highlighting preserves commands literally across pipes and quoted arguments", () => {
    const source = 'git status --short | findstr "<script>"; $env:HOME';
    const tokens = shellTokens(source);
    expect(tokens.map(token => token.text).join("")).toBe(source);
    expect(tokens.find(token => token.text === "git")?.kind).toBe("command");
    expect(tokens.find(token => token.text === "findstr")?.kind).toBe("command");
    expect(tokens.find(token => token.text === "--short")?.kind).toBe("option");
    expect(tokens.find(token => token.text === '"<script>"')?.kind).toBe("string");
  });

  test("command rows use text nodes and retain completed nodes when another command streams", () => {
    const document = { createElement: (_tag: string) => new TestElement() };
    class TestElement {
      ownerDocument = document; className = ""; textContent = ""; hidden = false;
      style: Record<string, string> = {}; children: TestElement[] = []; parent: TestElement | null = null;
      classList = { add: (_name: string) => {} };
      setAttribute(_name: string, _value: string): void {}
      append(...nodes: TestElement[]): void { for (const node of nodes) { node.parent = this; this.children.push(node); } }
      replaceWith(node: TestElement): void { if (this.parent) { node.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this), 1, node); } }
      remove(): void { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    }
    const root = new TestElement(), renderer = new TerminalRenderer(root as unknown as HTMLElement);
    const view = terminalTranscript([
      event(1, "terminal_command", { command_id: "done", command: "echo '<script>'" }),
      event(2, "terminal_output", { command_id: "done", output: "\x1b[31m<img onerror=alert(1)>\x1b[0m", final: true, exit_code: 0, status: "passed" }),
      event(3, "terminal_command", { command_id: "live", command: "watch" }),
    ]);
    renderer.render(view.commands);
    const completed = root.children[0];
    renderer.render(view.commands);
    expect(root.children[0]).toBe(completed);
    renderer.render([view.commands[0]!, { ...view.commands[1]!, output: "streaming", outputRuns: [{ text: "streaming" }] }]);
    expect(root.children[0]).toBe(completed);
    expect(root.children[1]?.children[1]?.children[0]?.textContent).toBe("streaming");
    const output = completed?.children[1]?.children[0];
    expect(output?.textContent).toBe("<img onerror=alert(1)>");
    expect(output?.style.color).toBe("var(--terminal-color-1)");
    expect(terminalStatus(view.commands[0]!)).toEqual({ label: "Completed", kind: "passed" });
    expect(terminalStatus(view.commands[1]!)).toEqual({ label: "Running", kind: "running" });
    renderer.render([view.commands[1]!]);
    expect(root.children).toHaveLength(1);
  });
});
