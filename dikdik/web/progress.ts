/** Live progress dashboard for long agent tasks. Renders into #progress-panel. */
import type { ProgressState, ContextHealth } from "./contracts";

function make<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function icon(name: string): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "i"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function healthClass(health: ContextHealth | null, ratio: number | undefined): string {
  if (health === "critical" || (ratio !== undefined && ratio >= 0.75)) return "progress-critical";
  if (health === "high" || (ratio !== undefined && ratio >= 0.60)) return "progress-high";
  return "progress-ok";
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtRatio(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** Render the progress panel from a ProgressState. Called on every SSE poll. */
export function renderProgress(panel: HTMLElement, state: ProgressState, elapsedSeconds: number): void {
  if (!state.isRunning) {
    panel.hidden = true;
    panel.replaceChildren();
    return;
  }
  panel.hidden = false;

  const header = make("div", "progress-header");
  header.append(icon("activity"), make("span", "progress-title", "Task progress"));

  // Stats row: elapsed, model calls, tool calls
  const stats = make("div", "progress-stats");
  const elapsedMin = Math.floor(elapsedSeconds / 60);
  const elapsedSec = elapsedSeconds % 60;
  stats.append(
    make("span", "progress-stat", `⏱ ${elapsedMin}:${String(elapsedSec).padStart(2, "0")}`),
    make("span", "progress-stat", `🧠 ${state.modelCalls} model call${state.modelCalls === 1 ? "" : "s"}`),
    make("span", "progress-stat", `🔧 ${state.toolCalls} tool call${state.toolCalls === 1 ? "" : "s"}`),
  );

  // Current step
  const stepRow = make("div", "progress-step-row");
  stepRow.append(icon("loader"), make("span", "progress-step", state.currentStep || "Working…"));

  // Context health meter
  let contextBar: HTMLElement = make("div", "progress-context-none", "Context: —");
  if (state.context) {
    const cls = healthClass(state.contextHealth, state.context.ratio);
    const bar = make("div", "progress-bar");
    const fill = make("div", `progress-bar-fill ${cls}`);
    fill.style.width = `${Math.min(100, state.context.ratio * 100)}%`;
    bar.append(fill);
    contextBar = make("div", "progress-context");
    contextBar.append(
      make("span", "progress-context-label", `Context: ${fmtBytes(state.context.bytes)} / ${fmtBytes(state.context.limit)} (${fmtRatio(state.context.ratio)})`),
      bar,
    );
  } else if (state.contextHealth) {
    contextBar = make("div", `progress-context ${healthClass(state.contextHealth, undefined)}`);
    contextBar.append(make("span", "progress-context-label", `Context: ${state.contextHealth}`));
  }

  // Checkpoint indicator
  let checkpoint: HTMLElement | null = null;
  if (state.lastCheckpointSeconds !== null) {
    checkpoint = make("div", "progress-checkpoint");
    checkpoint.append(icon("save"), make("span", "", `Checkpoint ${state.lastCheckpointSeconds}s ago`));
  }

  // Recent recoveries (collapsed)
  let recoveries: HTMLElement | null = null;
  if (state.recentRecoveries.length > 0) {
    recoveries = make("div", "progress-recoveries");
    for (const reason of state.recentRecoveries) {
      recoveries.append(make("div", "progress-recovery", reason));
    }
  }

  panel.replaceChildren(header, stats, stepRow, contextBar);
  if (checkpoint) panel.append(checkpoint);
  if (recoveries) panel.append(recoveries);
}
