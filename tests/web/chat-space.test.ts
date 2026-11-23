import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  citationMarkers, compactTokens, duration, groupTitle, groupable, pendingModelCall, sourceLabel, steeringStatus,
  thinkingDurations, thinkingLabel, thinkingSummary, toolTimings, turnCost, turnCostText,
} from "../../dikdik/web/chat-space";
import { eventToMessages, type TaskEvent } from "../../dikdik/web/contracts";

let seq = 0;
const ev = (kind: string, time: number, data: Record<string, unknown> = {}): TaskEvent => ({ seq: ++seq, task_id: "t", time, kind, data });

test("thinking collapses to a duration and a one-line summary", () => {
  expect(thinkingLabel(12.4)).toBe("Thought for 12s");
  expect(thinkingLabel(125)).toBe("Thought for 2m 5s");
  expect(thinkingLabel(0.2)).toBe("Thought for 1s");
  expect(thinkingLabel(null)).toBe("Thought");
  expect(thinkingLabel(3, true)).toBe("Thinking… 3s");
  expect(thinkingLabel(0, true)).toBe("Thinking…");
  expect(thinkingSummary("**Plan.** I should read the methods chapter first. Then the results.")).toBe("Plan.");
  expect(thinkingSummary("\n\nThe user wants a summary of chapter 3, so I will read it first and then check the numbers")).toBe(
    "The user wants a summary of chapter 3, so I will read it first and then check the numbers");
  const long = thinkingSummary("word ".repeat(60), 40);
  expect(long.length).toBeLessThanOrEqual(40);
  expect(long.endsWith("…")).toBe(true);
});

test("thinking time runs from the model call that produced the thought", () => {
  const events = [ev("agent_model", 100, { phase: "start" }), ev("agent_model", 109, { phase: "end" }), ev("agent_thinking", 109.5, { content: "x" }),
    ev("agent_model", 120, { phase: "start" }), ev("agent_thinking", 124, { content: "y", seconds: 2 })];
  const durations = thinkingDurations(events);
  expect([...durations.values()]).toEqual([9, 2]);
});

test("a model call in flight is reported until the next event from the agent", () => {
  const start = ev("agent_model", 50, { phase: "start" });
  expect(pendingModelCall([ev("running", 49), start])).toEqual({ since: 50, seq: start.seq });
  expect(pendingModelCall([start, ev("agent_model", 52, { phase: "end" })])).toBeNull();
  expect(pendingModelCall([start, ev("cancelled", 53)])).toBeNull();
  expect(pendingModelCall([])).toBeNull();
});

test("tool calls pair with their results by id and carry status and duration", () => {
  const a = ev("agent_tool", 10, { phase: "start", id: "a", name: "read_file" });
  const b = ev("agent_tool", 10.1, { phase: "start", id: "b", name: "read_file" });
  const events = [a, b, ev("agent_tool", 10.5, { phase: "end", id: "b", name: "read_file", failed: true }), ev("agent_tool", 12, { phase: "end", id: "a", name: "read_file" })];
  const timings = toolTimings(events);
  expect(timings.get(a.seq)).toEqual({ status: "done", seconds: 2 });
  expect(timings.get(b.seq)?.status).toBe("failed");
  const open = ev("agent_tool", 20, { phase: "start", id: "c", name: "run_command" });
  expect(toolTimings([open]).get(open.seq)).toEqual({ status: "running", seconds: null });
  expect(toolTimings([open, ev("cancelled", 21)]).get(open.seq)?.status).toBe("failed");
  expect(duration(0.04)).toBe("0.1s");
  expect(duration(1.26)).toBe("1s");
});

test("consecutive reads fold into one readable line", () => {
  const read = (target: string) => ({ verb: "Read", target });
  expect(groupTitle(Array.from({ length: 6 }, () => read("docs/proposal.docx")))).toBe("Read 6 sections of proposal.docx");
  expect(groupTitle([read("a.py"), read("b.py"), read("c.py"), read("d.py")])).toBe("Read 4 files");
  expect(groupTitle([read("a.py"), { verb: "Searched", target: "add" }, { verb: "Searched", target: "sub" }])).toBe("Read a.py, searched twice");
  expect(groupTitle([{ verb: "Fetched page", target: "https://a" }, { verb: "Fetched page", target: "https://b" }])).toBe("Fetched 2 pages");
  expect(groupable("Read") && groupable("Searched")).toBe(true);
  expect(groupable("Edited") || groupable("Ran")).toBe(false);
});

test("per-turn cost separates cached from fresh tokens and counts compactions", () => {
  const events = [
    ev("running", 1),
    ev("agent_model", 2, { phase: "end", usage: { input_tokens: 9000, output_tokens: 500, cache_read_tokens: 6000 } }),
    ev("agent_context_compacted", 3, { before_bytes: 200000, context_bytes: 40000 }),
    ev("agent_model", 4, { phase: "end", usage: { input_tokens: 3000, output_tokens: 200, cache_read_tokens: 2500, reported_cost: 0.0123 } }),
    ev("completed", 5),
    ev("running", 6),
    ev("agent_model", 7, { phase: "end", usage_tokens: 800 }),
  ];
  const first = turnCost(events, events[4]!.seq)!;
  expect(first).toMatchObject({ calls: 2, input: 12000, output: 700, cached: 8500, fresh: 3500, total: 12700, compactions: 1, hasCache: true });
  expect(turnCostText(first)).toBe("12.7k tokens · 8.5k cached · 3.5k fresh · 2 model calls · compacted once · $0.0123 reported");
  const current = turnCost(events)!;
  expect(current).toMatchObject({ calls: 1, total: 800, hasCache: false });
  expect(turnCostText(current)).toBe("800 tokens · 1 model call");
  expect(turnCost([ev("running", 1)])).toBeNull();
  expect(compactTokens(1_250_000)).toBe("1.25M");
});

test("messages typed during a run show whether they were read mid-run or sent afterwards", () => {
  const first = ev("user_message", 1, { text: "Shorter please", queued: true });
  const second = ev("user_message", 2, { text: "And in French", queued: true, attachments: true });
  const third = ev("user_message", 3, { text: "One more", queued: true });
  const events = [ev("running", 0), first, second, ev("user_steered", 4, { text: "Shorter please" }), ev("completed", 5), ev("running", 6), third];
  const status = steeringStatus(events);
  expect(status.get(first.seq)).toBe("applied");
  expect(status.get(second.seq)).toBe("follow-up");
  expect(status.get(third.seq)).toBe("pending");
  expect(eventToMessages(ev("user_steered", 7, { text: "x" }), "")).toEqual([]);
});

test("citation markers are found in prose but not in links or footnote definitions", () => {
  expect(citationMarkers("Growth was 4% [1], later 6% [2][3].").map(marker => marker.index)).toEqual([1, 2, 3]);
  expect(citationMarkers("See [1](https://a) and [2]: https://b")).toEqual([]);
  expect(citationMarkers("Array [0] is not a citation")).toEqual([]);
  expect(sourceLabel("https://www.who.int/news/item/1")).toBe("who.int");
  expect(sourceLabel("not a url")).toBe("not a url");
});

test("tool messages carry their call id; recovery is a notice, not a tool step", () => {
  const call = eventToMessages(ev("agent_tool", 1, { phase: "start", id: "c1", name: "read_document", arguments: { path: "a.docx" } }), "")[0];
  const result = eventToMessages(ev("agent_tool", 2, { phase: "end", id: "c1", name: "read_document", observation: "text" }), "")[0];
  expect(call?.callId).toBe("c1");
  expect(result?.callId).toBe("c1");
  expect(eventToMessages(ev("agent_recovery", 3, { reason: "Re-reading parts of a document already read" }), "")[0]).toMatchObject({ role: "system", kind: "agent_recovery" });
});

test("the chat wires the new views into the conversation", () => {
  const app = readFileSync("dikdik/web/app.ts", "utf8"), css = readFileSync("dikdik/web/style.css", "utf8"), html = readFileSync("dikdik/web/index.html", "utf8");
  expect(html).toContain('id="run-plan"');
  for (const name of ["function renderLiveThinking(", "function renderRunPlan(", "function addCitations(", "function noticeRow(", "function steerPill(", 'addEventListener("thinking"']) expect(app).toContain(name);
  for (const selector of [".run-plan {", ".cite-chip", ".step-status", ".msg-notice", ".think-live", ".steer-applied"]) expect(css).toContain(selector);
  expect(css).toMatch(/@media \(max-width: 760px\) \{\n  \.think-block, \.msg-live-thinking/);
});
