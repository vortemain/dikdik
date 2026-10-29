import type { TerminalCommandView } from "./contracts";
import { shellTokens } from "./terminal-format";

export function terminalStatus(command: TerminalCommandView): { label: string; kind: string } {
  if (command.running) return { label: "Running", kind: "running" };
  if (command.exitCode !== undefined && command.exitCode !== null && command.exitCode !== 0) return { label: `Exit ${command.exitCode}`, kind: "failed" };
  if (command.status === "passed" || command.exitCode === 0 && (!command.status || command.status === "passed")) return { label: "Completed", kind: "passed" };
  const label = command.status ? command.status.replace(/_/g, " ") : "Finished";
  return { label: label[0]!.toUpperCase() + label.slice(1), kind: command.status === "failed" ? "failed" : "stopped" };
}

function unchanged(a: TerminalCommandView, b: TerminalCommandView): boolean {
  return a.command === b.command && a.output === b.output && a.running === b.running
    && a.exitCode === b.exitCode && a.status === b.status && a.outputRuns.length === b.outputRuns.length
    && a.outputRuns.every((run, index) => {
      const other = b.outputRuns[index]!;
      return run.text === other.text && run.foreground === other.foreground && run.background === other.background
        && run.bold === other.bold && run.dim === other.dim && run.italic === other.italic && run.underline === other.underline && run.strike === other.strike;
    });
}

/** Update only changed command blocks, preserving completed output and text selection. */
export class TerminalRenderer {
  private rows = new Map<string, { node: HTMLElement; command: TerminalCommandView }>();
  constructor(private root: HTMLElement) {}
  render(commands: TerminalCommandView[]): void {
    const present = new Set(commands.map(command => command.id));
    for (const [id, row] of this.rows) if (!present.has(id)) { row.node.remove(); this.rows.delete(id); }
    for (const command of commands) {
      const previous = this.rows.get(command.id);
      if (previous && unchanged(previous.command, command)) continue;
      const document = this.root.ownerDocument;
      const row = document.createElement("section");
      row.className = "terminal-entry";
      const header = document.createElement("div"); header.className = "terminal-entry-head";
      const prompt = document.createElement("span"); prompt.className = "terminal-entry-prompt"; prompt.textContent = "$"; prompt.setAttribute("aria-hidden", "true");
      const source = document.createElement("code"); source.className = "terminal-entry-command";
      for (const token of shellTokens(command.command)) {
        const span = document.createElement("span"); span.textContent = token.text;
        if (token.kind) span.className = `shell-${token.kind}`;
        source.append(span);
      }
      const state = terminalStatus(command), badge = document.createElement("span");
      badge.className = `terminal-command-state is-${state.kind}`; badge.textContent = state.label;
      header.append(prompt, source, badge);
      const output = document.createElement("pre"); output.className = "terminal-entry-output";
      for (const run of command.outputRuns) {
        const span = document.createElement("span"); span.textContent = run.text;
        if (run.foreground) span.style.color = run.foreground;
        if (run.background) span.style.backgroundColor = run.background;
        if (run.bold) span.style.fontWeight = "700";
        if (run.dim) span.classList.add("terminal-dim");
        if (run.italic) span.style.fontStyle = "italic";
        if (run.underline || run.strike) span.style.textDecoration = [run.underline ? "underline" : "", run.strike ? "line-through" : ""].filter(Boolean).join(" ");
        output.append(span);
      }
      output.hidden = !command.output;
      row.append(header, output);
      if (previous) previous.node.replaceWith(row);
      else this.root.append(row);
      this.rows.set(command.id, { node: row, command });
    }
  }
}
