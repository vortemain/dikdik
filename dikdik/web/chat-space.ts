// The chat space: derived views over the task event stream for thinking, tool
// activity, cost and steering. Pure functions, no DOM; newer backend fields are
// optional and every view degrades to "nothing to show" when they are absent.
import type { TaskEvent } from "./contracts";

const num = (value: unknown): number | undefined => typeof value === "number" && Number.isFinite(value) ? value : undefined;
const str = (value: unknown): string => typeof value === "string" ? value : "";
const TERMINAL = new Set(["completed", "failed", "blocked", "cancelled", "interrupted", "paused"]);

/** "Thinking…", "Thought for 12s", "Thought for 2m 5s". */
export function thinkingLabel(seconds: number | null | undefined, active = false): string {
  if (active) return seconds && seconds >= 1 ? `Thinking… ${duration(seconds)}` : "Thinking…";
  if (seconds === null || seconds === undefined || !(seconds >= 0)) return "Thought";
  return `Thought for ${duration(Math.max(1, seconds))}`;
}

export function duration(seconds: number): string {
  if (!(seconds >= 0)) return "";
  if (seconds < 1) return `${Math.max(0.1, Math.round(seconds * 10) / 10)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60), rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** One plain line from a block of thinking: its first sentence, without Markdown. */
export function thinkingSummary(text: string, max = 110): string {
  const plain = text.replace(/```[\s\S]*?(```|$)/g, " ").replace(/[`*_#>]+/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
  const line = plain.split(/\n+/).map(part => part.trim()).find(Boolean) ?? "";
  const sentence = (line.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? line).trim();
  if (sentence.length <= max) return sentence;
  const cut = sentence.slice(0, max - 1), space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** Seconds each `agent_thinking` event took: from the model call that produced it to the thought. */
export function thinkingDurations(events: TaskEvent[]): Map<number, number> {
  const out = new Map<number, number>();
  let started: number | null = null, ended: number | null = null;
  for (const event of events) {
    if (event.kind === "agent_model") {
      if (event.data?.phase === "start") { started = event.time; ended = null; }
      else if (event.data?.phase === "end") ended = event.time;
    } else if (event.kind === "agent_thinking") {
      const reported = num(event.data?.seconds) ?? num(event.data?.duration_seconds);
      if (reported !== undefined) out.set(event.seq, reported);
      else if (started !== null) out.set(event.seq, Math.max(0, (ended ?? event.time) - started));
      started = null;
    }
  }
  return out;
}

/** The model call in flight now, if the run is waiting on one: when it started and how full the context was. */
export function pendingModelCall(events: TaskEvent[]): { since: number; seq: number } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]!;
    if (TERMINAL.has(event.kind)) return null;
    if (event.kind === "agent_model") return event.data?.phase === "start" ? { since: event.time, seq: event.seq } : null;
    if (event.kind === "agent_tool" || event.kind === "agent_message" || event.kind === "agent_thinking") return null;
  }
  return null;
}

// --- tool activity -------------------------------------------------------------

export type ToolStatus = "running" | "done" | "failed";
export interface ToolTiming { status: ToolStatus; seconds: number | null }

/** Status and duration of each tool call, keyed by its start event's seq; paired by call id. */
export function toolTimings(events: TaskEvent[]): Map<number, ToolTiming> {
  const out = new Map<number, ToolTiming>(), open = new Map<string, { seq: number; time: number }>();
  let stopped = false;
  for (const event of events) {
    if (TERMINAL.has(event.kind)) { stopped = true; continue; }
    if (event.kind === "running") stopped = false;
    if (event.kind !== "agent_tool") continue;
    const id = str(event.data?.id) || `${str(event.data?.name)}#`;
    if (event.data?.phase === "start") {
      open.set(id, { seq: event.seq, time: event.time });
      out.set(event.seq, { status: "running", seconds: null });
    } else if (event.data?.phase === "end") {
      const start = open.get(id);
      if (!start) continue;
      open.delete(id);
      out.set(start.seq, { status: event.data?.failed === true ? "failed" : "done", seconds: Math.max(0, event.time - start.time) });
    }
  }
  if (stopped) for (const [seq, timing] of out) if (timing.status === "running") out.set(seq, { status: "failed", seconds: null });
  return out;
}

const READ_VERBS = new Set(["Read", "Searched", "Listed files", "Fetched page", "Researched with Tavily", "Looked up"]);
/** Reads, searches and lookups fold together; edits, commands and checks stay one row each. */
export function groupable(verb: string): boolean { return READ_VERBS.has(verb); }

/** "Read 6 sections of proposal.docx", "Read 4 files", "Searched 3 times", or a mix. */
export function groupTitle(steps: { verb: string; target: string }[]): string {
  if (!steps.length) return "";
  const byVerb = new Map<string, string[]>();
  for (const step of steps) byVerb.set(step.verb, [...byVerb.get(step.verb) ?? [], step.target]);
  const parts = [...byVerb].map(([verb, targets]) => phrase(verb, targets));
  if (parts.length > 3) return `${steps.length} steps`;
  const text = parts.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function phrase(verb: string, targets: string[]): string {
  const count = targets.length, unique = new Set(targets.filter(Boolean));
  const name = (target: string) => target.split(/[\\/]/).pop() || target;
  const times = (n: number) => n === 1 ? "once" : n === 2 ? "twice" : `${n} times`;
  if (verb === "Read") {
    if (count === 1) return `read ${name(targets[0] ?? "") || "1 file"}`;
    if (unique.size === 1) return `read ${count} sections of ${name([...unique][0]!)}`;
    return `read ${count} ${unique.size === count ? "files" : "sections"}`;
  }
  if (verb === "Searched") return `searched ${times(count)}`;
  if (verb === "Listed files") return count === 1 ? "listed files" : `listed files ${times(count)}`;
  if (verb === "Fetched page") return count === 1 ? "fetched 1 page" : `fetched ${count} pages`;
  if (verb === "Researched with Tavily") return `searched the web ${times(count)}`;
  return `${verb.toLowerCase()} ${times(count)}`;
}

// --- cost meter ------------------------------------------------------------------

export interface TurnCost {
  calls: number;
  input: number; output: number; cached: number; cacheWrite: number; fresh: number; total: number;
  cost?: number;
  compactions: number;
  hasCache: boolean;
}

/** Tokens the provider reported for the turn that ends at `endSeq` (or the current turn). */
export function turnCost(events: TaskEvent[], endSeq?: number): TurnCost | null {
  let end = events.length;
  if (endSeq !== undefined) { end = events.findIndex(event => event.seq === endSeq); if (end < 0) return null; }
  let start = 0;
  for (let i = end - 1; i >= 0; i--) if (events[i]!.kind === "running") { start = i; break; }
  const cost: TurnCost = { calls: 0, input: 0, output: 0, cached: 0, cacheWrite: 0, fresh: 0, total: 0, compactions: 0, hasCache: false };
  let reportedCost = 0, anyCost = false, running = 0;
  for (const event of events.slice(start, end)) {
    const data = event.data ?? {};
    if (event.kind === "agent_context_compacted" || event.kind === "context_compacted") cost.compactions++;
    if (event.kind !== "agent_model" || data.phase !== "end") continue;
    cost.calls++;
    const usage = (typeof data.usage === "object" && data.usage !== null ? data.usage : {}) as Record<string, unknown>;
    const input = num(usage.input_tokens) ?? num(usage.prompt_tokens) ?? 0, output = num(usage.output_tokens) ?? num(usage.completion_tokens) ?? 0;
    const cached = num(usage.cache_read_tokens) ?? num(usage.cached_tokens) ?? num(data.cached_tokens);
    const written = num(usage.cache_write_tokens) ?? 0;
    if (cached !== undefined) cost.hasCache = true;
    cost.input += input; cost.output += output; cost.cached += cached ?? 0; cost.cacheWrite += written;
    let total = num(usage.total_tokens);
    const runningTotal = num(data.usage_tokens);
    if (total === undefined && runningTotal !== undefined && runningTotal > running) total = runningTotal - running;
    if (runningTotal !== undefined) running = Math.max(running, runningTotal);
    cost.total += total ?? input + output;
    const price = num(usage.reported_cost);
    if (price !== undefined) { reportedCost += price; anyCost = true; }
  }
  if (!cost.calls && !cost.compactions) return null;
  // Providers either count cached tokens inside input (OpenAI) or beside it (Anthropic); fresh is never negative.
  cost.fresh = Math.max(0, cost.input - cost.cached);
  if (cost.input && cost.cached > cost.input) cost.fresh = cost.input;
  if (anyCost) cost.cost = reportedCost;
  return cost;
}

export function compactTokens(value: number): string {
  if (value < 1000) return String(Math.round(value));
  if (value < 100000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (value < 1e6) return `${Math.round(value / 1000)}k`;
  return `${(value / 1e6).toFixed(2).replace(/0$/, "")}M`;
}

/** One quiet line under a turn: "12.3k tokens · 9.8k cached · 2.5k fresh · 3 model calls · compacted once". */
export function turnCostText(cost: TurnCost | null): string {
  if (!cost) return "";
  const parts: string[] = [];
  if (cost.total) parts.push(`${compactTokens(cost.total)} tokens`);
  if (cost.hasCache && cost.input) parts.push(`${compactTokens(cost.cached)} cached`, `${compactTokens(cost.fresh)} fresh`);
  if (cost.calls) parts.push(`${cost.calls} model call${cost.calls === 1 ? "" : "s"}`);
  if (cost.compactions) parts.push(cost.compactions === 1 ? "compacted once" : `compacted ${cost.compactions} times`);
  if (cost.cost !== undefined && cost.cost > 0) parts.push(`$${cost.cost < 1 ? cost.cost.toFixed(4) : cost.cost.toFixed(2)} reported`);
  return parts.join(" · ");
}

// --- steering --------------------------------------------------------------------

export type SteerStatus = "pending" | "applied" | "follow-up";
/** What became of each message typed during a run, keyed by its user_message seq. */
export function steeringStatus(events: TaskEvent[]): Map<number, SteerStatus> {
  const out = new Map<number, SteerStatus>(), waiting: { seq: number; text: string }[] = [];
  for (const event of events) {
    if (event.kind === "user_message" && event.data?.queued === true) {
      out.set(event.seq, "pending"); waiting.push({ seq: event.seq, text: str(event.data?.text) });
    } else if (event.kind === "user_steered") {
      const index = waiting.findIndex(item => item.text === str(event.data?.text));
      if (index >= 0) { out.set(waiting[index]!.seq, "applied"); waiting.splice(index, 1); }
    } else if (event.kind === "running" && waiting.length) {
      out.set(waiting.shift()!.seq, "follow-up");
    }
  }
  return out;
}

// --- citations -------------------------------------------------------------------

/** `[n]` markers in prose (not links, not footnote definitions), with their source number. */
export function citationMarkers(text: string): { index: number; start: number; end: number }[] {
  const out: { index: number; start: number; end: number }[] = [];
  for (const match of text.matchAll(/\[(\d{1,2})\](?![(:])/g)) {
    const index = Number(match[1]);
    if (index >= 1) out.push({ index, start: match.index!, end: match.index! + match[0].length });
  }
  return out;
}

export function sourceLabel(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}
