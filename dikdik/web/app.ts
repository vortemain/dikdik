import { browserView, buildTaskRequest, conversationLabel, eventsToChat, fileStatusLabel, isTerminal, modePolicy, parseMode, parsePlan, parseVerification, resultStatus, reviewDiff, taskChecklist, terminalTranscript, transcriptMarkdown } from "./contracts";
import { attachmentList, contextUsage, goalState, isResumable, openConfirmations, parseGoal, parseSlash, slashMatches } from "./contracts";
import { buildAutomation, describeSchedule, parseAutomations, parseRunSummaries, profilesPanel, secretsPanel, skillsPanel } from "./contracts";
import type { ActiveModel, AgentProfile, AttachmentMeta, Automation, ChatMessage, SlashCommand, TaskRequest, DiffReview, M4Factory, M4Node, PlanView, Project, Task, TaskEvent, TaskResult, TaskSummary } from "./contracts";
import { ComposerDrafts } from './drafts';
import { keysPane, modelsPane } from "./models-pane";
import { alertKind, fileTree, organiseTasks, parentFolders, parseTable, parseTags, rankCommands, workspacePath } from "./organise";
import type { TaskSort } from "./organise";
import { ComposerSize, composerMaximum } from './composer-size';

function element<T extends HTMLElement>(id: string, kind: { new(): T }): T {
  const found = document.getElementById(id);
  if (!(found instanceof kind)) throw new Error(`Missing or invalid UI element: ${id}`);
  return found;
}
const el = (id: string) => document.getElementById(id)!;
const input = (id: string) => element(id, HTMLInputElement);
const button = (id: string) => element(id, HTMLButtonElement);
const select = (id: string) => element(id, HTMLSelectElement);
const textarea = (id: string) => element(id, HTMLTextAreaElement);

// Bearer token only for the paste-a-token fallback; normally the server's HttpOnly cookie signs us in.
let token = "", selected: string | null = null, polling = false, watching = false, connected = false;
let review: DiffReview = reviewDiff(null), reviewTask: string | null = null, reviewKey = "", selectedFile: string | null = null, renderedDiff = "";
let projectName = "", canvasAutoOpened: string | null = null, selectedState = "", showArchived = false;
let lastMessages: ChatMessage[] = [];
let agentProfiles: AgentProfile[] = [];  // read by syncMode at load time, so declared up here
let currentPlan: PlanView | null = null, parentTask: string | null = null, observedTaskMode = '';
let viewEpoch = 0, observeQueued = false;
let submitting = false;
// Run control: files attached to the next message, a per-tab model choice, and the open task's request.
let attached: (AttachmentMeta & { preview: string })[] = [];
let modelOverride: { id: string; name: string } | null = null;
let modelProfiles: { id: string; name: string; model: string }[] = [], skillNames: string[] = [];
let slashItems: SlashCommand[] = [], slashIndex = 0, slashModels = false;
let taskRequest: TaskRequest | null = null;
const confirmCards = new Map<string, HTMLElement>();
// Organisation, onboarding and files: read by functions that run at load time.
let modelReady = true, activeTag = "", listLimit = 40, folderOptions: string[] = [];
let appSettings: { confirmation: string; condenser: boolean; git_name: string; git_email: string; sounds: boolean } | null = null;
let stepGroup: { li: HTMLLIElement; list: HTMLElement; details: HTMLDetailsElement; summary: HTMLElement; count: number } | null = null;
let showAllMessages = false, projectFileSet = new Set<string>();
const expandedDirs = new Set<string>();
let openTabs: string[] = [], fileText = "", filePreview = false;
let exposedSecrets = 0;  // keys the user exposed to the sandbox; the composer offers them only then
const composerSize = new ComposerSize();

// --- small DOM helpers ----------------------------------------------------
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
function setClass(node: HTMLElement, name: string, on: boolean): void {
  const names = new Set((node.className || "").split(/\s+/).filter(Boolean));
  if (on) names.add(name); else names.delete(name);
  node.className = [...names].join(" ");
}
function storageGet(key: string): string | null {
  try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; }
}
function storageSet(key: string, value: string): void {
  try { globalThis.localStorage?.setItem(key, value); } catch { /* private mode: preference just isn't remembered */ }
}
function wide(query: string): boolean {
  return typeof globalThis.matchMedia === "function" && globalThis.matchMedia(query).matches;
}
function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- copy to clipboard ----------------------------------------------------
async function copyText(text: string, control: HTMLButtonElement): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = make("textarea"); area.value = text; area.className = "sr-only";
    document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
  }
  const label = control.querySelector("span");
  control.replaceChildren(icon("check"), make("span", "", "Copied"));
  setClass(control, "copied", true);
  setTimeout(() => { control.replaceChildren(icon("copy"), label ?? make("span", "", "Copy")); setClass(control, "copied", false); }, 1600);
}
function copyButton(getText: () => string, label = "Copy"): HTMLButtonElement {
  const control = make("button", "copy");
  control.type = "button";
  control.setAttribute("aria-label", "Copy to clipboard");
  control.append(icon("copy"), make("span", "", label));
  control.onclick = () => { void copyText(getText(), control); };
  return control;
}

// --- code blocks and diffs --------------------------------------------------
const LANGUAGES: Record<string, string> = { py: "python", ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx", json: "json", md: "markdown", rs: "rust", toml: "toml", yml: "yaml", yaml: "yaml", sh: "bash", css: "css", html: "html", go: "go", java: "java", c: "c", cpp: "cpp", rb: "ruby" };
function languageFor(path: unknown): string {
  if (typeof path !== "string") return "";
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return LANGUAGES[ext] ?? "";
}
function looksLikeDiff(text: string): boolean {
  return /^(diff --git |--- |@@ )/m.test(text) && /^[+-]/m.test(text);
}
/** Diff lines as spans; joined textContent equals the input exactly. */
function diffLines(text: string): HTMLElement[] {
  const lines = text.split("\n");
  return lines.map((line, index) => {
    const kind = line.startsWith("@@") ? "hunk"
      : /^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(line) ? "meta"
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
    return make("span", `dl dl-${kind}`, line + (index < lines.length - 1 ? "\n" : ""));
  });
}
function codeBlock(text: string, lang = "", options: { wrap?: boolean } = {}): HTMLElement {
  const block = make("div", options.wrap ? "code-block wrap" : "code-block");
  const head = make("div", "code-head");
  head.append(make("span", "code-lang", lang || "text"), copyButton(() => text));
  const pre = make("pre"), code = make("code");
  if (lang === "diff" || (!lang && looksLikeDiff(text))) code.append(...diffLines(text));
  else code.textContent = text;
  pre.append(code);
  block.append(head, pre);
  return block;
}

// --- rich text: a deliberately small, DOM-only Markdown subset -------------
// Untrusted model output never reaches innerHTML; every node is built explicitly.
function inline(text: string): Node[] {
  const nodes: Node[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) nodes.push(document.createTextNode(text.slice(last, index)));
    const token = match[0];
    if (token.startsWith('[')) {
      const parts = /^\[([^\]]+)\]\((.+)\)$/.exec(token);
      try {
        const url = new URL(parts?.[2] ?? 'https://invalid.invalid');
        if (!url.username && !url.password) { const link = make('a', '', parts?.[1] ?? 'Link'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; nodes.push(link); }
        else nodes.push(document.createTextNode(token));
      } catch { nodes.push(document.createTextNode(token)); }
    } else if (token.startsWith("`")) {
      const target = workspacePath(token.slice(1, -1), projectFileSet);
      if (target) {
        const link = make("button", "path-link"); link.type = "button"; link.title = `Open ${target.path} in Files`;
        link.append(make("code", "", token.slice(1, -1)));
        link.onclick = () => { void openFileFromChat(target.path, target.line); };
        nodes.push(link);
      } else nodes.push(make("code", "", token.slice(1, -1)));
    } else nodes.push(make("strong", "", token.slice(2, -2)));
    last = index + token.length;
  }
  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}
function richText(text: string): HTMLElement {
  const root = make("div", "prose");
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let paragraph: string[] = [];
  let list: HTMLElement | null = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const p = make("p"); p.append(...inline(paragraph.join("\n"))); root.append(p); paragraph = [];
  };
  const flushList = () => { list = null; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const fence = /^\s*```\s*([\w.+-]*)\s*$/.exec(line);
    if (fence) {
      flushParagraph(); flushList();
      const body: string[] = [];
      for (i++; i < lines.length && !/^\s*```\s*$/.test(lines[i] ?? ""); i++) body.push(lines[i] ?? "");
      root.append(codeBlock(body.join("\n"), (fence[1] ?? "").toLowerCase()));
      continue;
    }
    const table = parseTable(lines, i);
    if (table) {
      flushParagraph(); flushList();
      root.append(markdownTable(table));
      i = table.end - 1;
      continue;
    }
    const alert = alertKind(line);
    if (alert || /^\s*>/.test(line)) {
      flushParagraph(); flushList();
      const body: string[] = [];
      for (i += alert ? 1 : 0; i < lines.length && /^\s*>/.test(lines[i] ?? ""); i++) body.push((lines[i] ?? "").replace(/^\s*>\s?/, ""));
      i--;
      if (alert) {
        const box = make("div", `md-alert md-alert-${alert}`);
        box.append(make("p", "md-alert-title", alert[0]!.toUpperCase() + alert.slice(1)), richText(body.join("\n")));
        root.append(box);
      } else { const quote = make("blockquote"); quote.append(...inline(body.join("\n"))); root.append(quote); }
      continue;
    }
    const heading = /^#{1,4}\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      const h = make("h4"); h.append(...inline(heading[1] ?? "")); root.append(h);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "UL" : "OL";
      if (!list || list.tagName !== tag) { list = make(bullet ? "ul" : "ol"); root.append(list); }
      const li = make("li"); li.append(...inline((bullet ?? numbered)?.[1] ?? "")); list.append(li);
    } else if (!line.trim()) {
      flushParagraph(); flushList();
    } else {
      flushList(); paragraph.push(line);
    }
  }
  flushParagraph();
  return root;
}

function markdownTable(table: NonNullable<ReturnType<typeof parseTable>>): HTMLElement {
  const scroller = make("div", "table-scroll");
  const grid = make("table"), head = make("thead"), body = make("tbody"), headRow = make("tr");
  table.header.forEach((cell, index) => { const th = make("th"); th.append(...inline(cell)); if (table.align[index]) th.style.textAlign = table.align[index]!; headRow.append(th); });
  head.append(headRow);
  for (const row of table.rows) {
    const tr = make("tr");
    row.forEach((cell, index) => { const td = make("td"); td.append(...inline(cell)); if (table.align[index]) td.style.textAlign = table.align[index]!; tr.append(td); });
    body.append(tr);
  }
  grid.append(head, body);
  scroller.append(grid);
  return scroller;
}

// --- chat rendering -------------------------------------------------------
interface StepLabel { icon: string; title: string; arg: string }
const TOOL_LABELS: Record<string, [string, string, string]> = {
  read_file: ["file", "Read", "path"],
  list_files: ["list", "Listed files", "query"],
  search_text: ["search", "Searched", "text"],
  edit_file: ["pencil", "Edited", "path"],
  create_file: ["file-plus", "Created", "path"],
  run_tests: ["terminal", "Ran checks", ""],
  run_command: ["terminal", "Ran", "command"],
  finish: ["flag", "Finished", ""],
  task_tracker: ["list", "Updated the task list", ""],
  write_plan: ["pencil", "Wrote the plan", ""],
  read_plan: ["book", "Read the plan", ""],
  browse_url: ["globe", "Opened", "url"],
};
function detailField(details: unknown, key: string): string {
  if (typeof details !== "object" || details === null || !(key in details)) return "";
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}
function toolName(msg: ChatMessage): string {
  return (msg.title ?? "").replace(/^Tool (call|result): /, "");
}
function stepLabel(msg: ChatMessage): StepLabel {
  const details = msg.details;
  switch (msg.kind) {
    case "agent_tool_call": {
      const name = toolName(msg);
      if (name.startsWith("mcp__")) {
        const [, server = "", tool = ""] = name.split("__");
        return { icon: "plug", title: `${server} · ${tool}`, arg: "" };
      }
      const known = TOOL_LABELS[name];
      if (!known) return { icon: "terminal", title: name || "Tool", arg: "" };
      const arg = known[2] ? detailField(details, known[2]) || detailField(details, "query") || detailField(details, "pattern") : "";
      return { icon: known[0], title: known[1], arg };
    }
    case "agent_tool_result": return { icon: "terminal", title: `Output · ${toolName(msg)}`, arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "inspected": return { icon: "folder", title: "Inspected repository", arg: detailField(details, "file_count") ? `${detailField(details, "file_count")} files` : "" };
    case "execution": return { icon: "terminal", title: "Running tests in Docker", arg: detailField(details, "command") };
    case "syntax": return { icon: detailField(details, "status") === "passed" ? "check" : "alert", title: msg.title ?? "Syntax check", arg: detailField(details, "status") };
    case "agent_repair": return { icon: "refresh", title: "Repair attempt", arg: detailField(details, "reason") };
    case "agent_started": return { icon: "sparkle", title: "Started agent", arg: "" };
    case "agent_finished": return { icon: "flag", title: "Agent finished", arg: detailField(details, "state") };
    case "proposal": return { icon: "pencil", title: "Proposed edits", arg: "" };
    case "applied": return { icon: "check", title: msg.title ?? "Applied", arg: (msg.body ?? "").split("\n").join(", ") };
    case "skills": return { icon: "book", title: "Using skills", arg: (msg.body ?? "").split("\n").join(", ") };
    case "mcp": return { icon: "plug", title: "Connected MCP servers", arg: `${(msg.body ?? "").split("\n").join(", ")} · ${detailField(details, "tools") || 0} tools` };
    case "cancellation_requested": return { icon: "stop", title: "Cancellation requested", arg: "" };
    case "recovery_cleanup": return { icon: "alert", title: "Recovered after restart", arg: detailField(details, "warning") };
    case "hook_blocked": return { icon: "shield", title: "Blocked by a project hook", arg: msg.body ?? "" };
    case "agent_thinking": return { icon: "sparkle", title: "Thinking", arg: "" };
    case "model_routed": return { icon: "cpu", title: msg.title ?? "Routed", arg: msg.body ?? "" };
    case "critic": return { icon: msg.title === "Critic: looks done" ? "check" : "alert", title: msg.title ?? "Critic", arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "sandbox_secrets": return { icon: "key", title: "Sandbox secrets available", arg: msg.body ?? "" };
    case "confirmation_resolved": return { icon: msg.title === "Approved" ? "check" : "x", title: `${msg.title} · ${detailField(details, "id") ? "action" : ""}`.replace(/ · $/, ""), arg: detailField(details, "reason") };
    case "subagent": return { icon: "sparkle", title: msg.title ?? "Subagent", arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "condensed": return { icon: "list", title: msg.title ?? "Condensed", arg: "" };
    case "resumed": return { icon: "play", title: "Resumed", arg: detailField(details, "from_state") ? `from ${detailField(details, "from_state")}` : "" };
    case "pause_requested": return { icon: "pause", title: "Pausing after this step", arg: "" };
    case "forked": return { icon: "branch", title: "Forked from another conversation", arg: "" };
    case "goal_status": return { icon: "target", title: msg.title ?? "Goal", arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "goal_turn": return { icon: "target", title: "Next goal turn", arg: (msg.body ?? "").split("\n")[0] ?? "" };
    default: return { icon: "terminal", title: msg.title ?? "Step", arg: "" };
  }
}
function evidenceBlocks(msg: ChatMessage, path = ""): HTMLElement[] {
  const blocks: HTMLElement[] = [];
  const details = msg.details;
  if (msg.kind === "agent_tool_call" && toolName(msg) === "edit_file" && detailField(details, "before")) {
    const minus = detailField(details, "before").split("\n").map(line => "-" + line);
    const plus = detailField(details, "after").split("\n").map(line => "+" + line);
    blocks.push(codeBlock([`--- ${path}`, `+++ ${path}`, ...minus, ...plus].join("\n"), "diff"));
    return blocks;
  }
  if (msg.kind === "agent_tool_call" && toolName(msg) === "run_command" && detailField(details, "command")) {
    blocks.push(codeBlock(detailField(details, "command"), "bash", { wrap: true }));
    return blocks;
  }
  if (msg.kind === "agent_thinking") {
    blocks.push(make("div", "thinking-text", msg.body ?? ""));
    return blocks;
  }
  if (msg.kind === "agent_tool_call" && toolName(msg) === "create_file" && detailField(details, "content")) {
    blocks.push(codeBlock(detailField(details, "content"), languageFor(path)));
    return blocks;
  }
  if (msg.body) blocks.push(codeBlock(msg.body, msg.kind === "agent_tool_result" ? languageFor(path) : "", { wrap: msg.kind !== "agent_tool_result" }));
  // Arguments already shown in the row's label (a path or query) would only repeat themselves.
  const shown = new Set(["path", "query", "text", "pattern"]);
  if (msg.kind === "agent_tool_call" && typeof details === "object" && details !== null && Object.keys(details).every(key => shown.has(key))) return blocks;
  if (details !== undefined && details !== null && !(typeof details === "object" && Object.keys(details).length === 0)) {
    blocks.push(codeBlock(typeof details === "string" ? details : JSON.stringify(details, null, 2), typeof details === "string" ? "" : "json", { wrap: true }));
  }
  return blocks;
}
function stepRow(msg: ChatMessage): { li: HTMLLIElement; body: HTMLElement } {
  const li = make("li", `msg msg-${msg.role === "system" ? "system" : "tool"} msg-${msg.kind}`);
  li.id = msg.id;
  li.dataset.step = "1";
  const label = stepLabel(msg);
  const details = make("details", "step");
  const summary = make("summary");
  const badge = make("span", "step-icon"); badge.append(icon(label.icon));
  summary.append(badge, make("span", "step-title", label.title));
  if (label.arg) summary.append(make("span", "step-arg", label.arg));
  const chevron = icon("chevron"); chevron.setAttribute("class", "i step-chevron");
  summary.append(chevron);
  const body = make("div", "step-body");
  const blocks = evidenceBlocks(msg, label.arg);
  body.append(...blocks);
  details.append(summary, body);
  // Rows without evidence don't expand; a tool call regains its chevron when its output arrives.
  if (!blocks.length) setClass(details, "step-static", true);
  summary.addEventListener("click", event => { if (details.className.includes("step-static")) event.preventDefault(); });
  li.append(details);
  return { li, body };
}
function resultCard(msg: ChatMessage): HTMLLIElement {
  const li = make("li", `msg msg-system msg-result`);
  li.id = msg.id;
  const state = msg.state ?? "completed";
  const result = msg.details as TaskResult | undefined;
  if (state === "completed" && (result as { turn_changed?: unknown } | undefined)?.turn_changed === false) {
    // An answer-only turn: the reply says it all, so just note that nothing changed.
    li.className = "msg msg-system msg-turn-note";
    const note = make("p", "turn-note", `No files changed in this turn.${usageText(msg.details) ? " " + usageText(msg.details) : ""}`);
    const seq = Number(msg.id.replace("terminal-", ""));
    if (selected && Number.isInteger(seq)) {
      const branch = make("button", "ghost small turn-branch");
      branch.type = "button";
      branch.append(icon("branch"), make("span", "", "Branch"));
      branch.title = "Start a new conversation with the history up to this point";
      branch.onclick = () => { if (selected) void forkTask(selected, isLatestTurn(msg) ? undefined : seq).catch(error => message(error instanceof Error ? error.message : "Fork failed", true)); };
      note.append(branch);
    }
    li.append(note);
    return li;
  }
  const card = make("div", `result-card result-${state}`);
  const head = make("div", "result-head");
  const badge = make("span", "result-icon");
  badge.append(icon(state === "completed" ? "check" : state === "cancelled" ? "stop" : state === "paused" ? "pause" : "alert"));
  const titles: Record<string, string> = { completed: "Candidate ready for review", failed: "Task failed", blocked: "Task blocked", cancelled: "Task cancelled", interrupted: "Task interrupted", paused: "Paused" };
  head.append(badge, make("span", "result-title", titles[state] ?? `Task ${state}`));
  if (state === "completed") {
    const verified = result?.tests_verified === true;
    head.append(make("span", verified ? "verify-badge verified" : "verify-badge", verified ? "Tests passed" : "Not test-verified"));
  }
  card.append(head, make("p", "result-text", resultStatus(state, result)));
  if (usageText(msg.details)) card.append(make("p", "result-usage", usageText(msg.details)));
  const actions = make("div", "result-actions");
  if (result && typeof result.diff === "string" && result.diff) {
    const open = make("button", "small");
    open.type = "button";
    open.append(icon("diff"), make("span", "", "Review & apply"));
    open.onclick = () => openCanvas("changes");
    actions.append(open, copyButton(() => review.diff, "Copy diff"));
  }
  const latest = isLatestTurn(msg);
  if (state !== "completed" && latest) {
    const exact = taskRequest?.mode === "agent" && isResumable(state);
    const resume = make("button", "small primary");
    resume.type = "button";
    resume.append(icon(exact ? "play" : "refresh"), make("span", "", exact ? "Resume" : "Continue"));
    resume.title = exact ? "Continue from the exact step where it stopped, with a fresh step budget" : "Send a follow-up asking Dikdik to finish";
    resume.onclick = () => { void (exact ? resumeTask() : sendMessage("Continue where you left off. Finish the original request.")); };
    actions.append(resume);
  }
  const seq = Number(msg.id.replace("terminal-", ""));
  if (selected && Number.isInteger(seq)) {
    const branch = make("button", "small ghost");
    branch.type = "button";
    branch.append(icon("branch"), make("span", "", "Branch from here"));
    branch.title = "Start a new conversation with the history and changes up to this point";
    branch.onclick = () => { if (selected) void forkTask(selected, latest ? undefined : seq).catch(error => message(error instanceof Error ? error.message : "Fork failed", true)); };
    actions.append(branch);
  }
  const evidence = make("details", "step");
  const summary = make("summary");
  const chevron = icon("chevron"); chevron.setAttribute("class", "i step-chevron");
  summary.append(make("span", "step-title", "Evidence"), chevron);
  evidence.append(summary, codeBlock(JSON.stringify(msg.details ?? {}, null, 2), "json", { wrap: true }));
  card.append(actions, evidence);
  li.append(card);
  return li;
}
function assistantMessage(msg: ChatMessage, showHeader: boolean): HTMLLIElement {
  const li = make("li", `msg msg-assistant msg-${msg.kind}`);
  li.id = msg.id;
  if (showHeader) {
    const avatar = make("img", "avatar"); avatar.src = "/icon.svg"; avatar.alt = "";
    li.append(avatar);
  } else li.append(make("span", "avatar-gap"));
  const content = make("div");
  if (showHeader) {
    const label = make("div", "msg-label", "Dikdik");
    const time = make("time", "", clock(msg.time)); time.dateTime = new Date(msg.time * 1000).toISOString();
    label.append(time);
    content.append(label);
  }
  if (msg.kind === "plan") {
    const steps = Array.isArray(msg.details) ? msg.details : null;
    if (steps && steps.every(step => typeof step === "string")) {
      const prose = make("div", "prose");
      prose.append(make("p", "", "Here’s the plan:"));
      const list = make("ol", "plan");
      for (const step of steps) list.append(make("li", "", step));
      prose.append(list);
      content.append(prose);
    } else content.append(...evidenceBlocks(msg));
  } else if (msg.body) {
    content.append(richText(msg.body));
    const tools = make("div", "msg-actions");
    tools.append(copyButton(() => msg.body ?? "", "Copy"));
    content.append(tools);
  }
  li.append(content);
  return li;
}
/** Work counts the provider and agent reported for a turn; never an invented cost. */
function usageText(details: unknown): string {
  const agent = typeof details === "object" && details !== null ? (details as { agent?: Record<string, unknown> }).agent : undefined;
  if (!agent) return "";
  const count = (key: string) => typeof agent[key] === "number" ? agent[key] as number : 0;
  const parts = [`${count("model_calls")} model call${count("model_calls") === 1 ? "" : "s"}`, `${count("tool_calls")} tool call${count("tool_calls") === 1 ? "" : "s"}`];
  if (count("reported_usage_tokens")) parts.push(`${count("reported_usage_tokens").toLocaleString()} tokens reported`);
  const model = (details as { model?: { profile?: unknown; model?: unknown } }).model;
  const name = typeof model?.profile === "string" && model.profile ? model.profile : typeof model?.model === "string" ? model.model : "";
  if (name) parts.push(name);
  return parts.join(" · ");
}
function isLatestTurn(msg: ChatMessage): boolean {
  const last = [...liveEvents].reverse().find(event => isTerminal(event.kind));
  return !last || `terminal-${last.seq}` === msg.id;
}
function attachmentCard(msg: ChatMessage): HTMLLIElement {
  const li = make("li", "msg msg-user msg-attachments"); li.id = msg.id;
  const grid = make("div", "attachment-grid");
  for (const file of attachmentList(msg.details)) {
    if (file.type.startsWith("image/")) {
      const image = make("img", "attachment-image"); image.src = `/api/attachments/${file.id}`; image.alt = file.name; image.loading = "lazy"; image.title = file.name;
      grid.append(image);
    } else {
      const chip = make("span", "attachment-chip"); chip.append(icon("file"), make("span", "", file.name));
      grid.append(chip);
    }
  }
  li.append(grid);
  return li;
}
function confirmationCard(msg: ChatMessage): HTMLLIElement {
  const d = (msg.details ?? {}) as { id?: string; tool?: string; risk?: string; reason?: string; arguments?: unknown };
  const li = make("li", "msg msg-system msg-confirm"); li.id = msg.id;
  const card = make("div", `confirm-card risk-${d.risk || "low"}`);
  const head = make("div", "result-head");
  const badge = make("span", "result-icon"); badge.append(icon("shield"));
  head.append(badge, make("span", "result-title", `Allow ${d.tool || "this action"}?`), make("span", "verify-badge", d.risk === "high" ? "High risk" : d.risk === "medium" ? "Needs review" : "Low risk"));
  card.append(head);
  if (d.reason) card.append(make("p", "result-text", d.reason));
  const args = typeof d.arguments === "object" && d.arguments !== null ? d.arguments as Record<string, unknown> : {};
  const shown = typeof args.command === "string" ? codeBlock(args.command, "bash", { wrap: true }) : codeBlock(JSON.stringify(args, null, 2), "json", { wrap: true });
  card.append(shown);
  const actions = make("div", "result-actions");
  const allow = make("button", "small primary", "Allow"); allow.type = "button"; allow.title = "Allow (Y or Enter)";
  const reject = make("button", "small", "Reject"); reject.type = "button"; reject.title = "Reject (N or Esc)";
  const status = make("span", "confirm-status");
  actions.append(allow, reject, status);
  card.append(actions);
  li.append(card);
  const id = d.id ?? "";
  const decide = async (accept: boolean) => {
    if (!selected || allow.disabled) return;
    allow.disabled = reject.disabled = true;
    try { await api(`/api/tasks/${encodeURIComponent(selected)}/confirm`, { id, accept }); status.textContent = accept ? "Allowed" : "Rejected"; }
    catch (error) { allow.disabled = reject.disabled = false; message(error instanceof Error ? error.message : "Could not send the decision", true); }
  };
  allow.onclick = () => { void decide(true); };
  reject.onclick = () => { void decide(false); };
  card.addEventListener("keydown", (event: KeyboardEvent) => {
    const key = event.key.toLowerCase();
    if (key === "y" || key === "enter") { event.preventDefault(); void decide(true); }
    else if (key === "n" || key === "escape") { event.preventDefault(); void decide(false); }
  });
  card.tabIndex = -1;
  if (openConfirmations(liveEvents).includes(id)) {
    confirmCards.set(id, li);
    if (!textarea("objective").value.trim()) setTimeout(() => allow.focus?.(), 0);
  } else {
    allow.disabled = reject.disabled = true;
    status.textContent = "Decided";
  }
  return li;
}

let rendered = new Set<string>();
let pendingCalls: { name: string; body: HTMLElement }[] = [];
let lastRole = "";
function renderMessage(msg: ChatMessage): HTMLLIElement | null {
  if (msg.kind === "attachments") { lastRole = "user"; return attachmentCard(msg); }
  if (msg.kind === "confirmation_requested") { lastRole = "system"; return confirmationCard(msg); }
  if (msg.kind === "confirmation_resolved") {
    const id = detailField(msg.details, "id"), card = confirmCards.get(id);
    if (card) {
      for (const control of card.querySelectorAll<HTMLButtonElement>("button")) control.disabled = true;
      const status = card.querySelector(".confirm-status");
      if (status) status.textContent = msg.title === "Approved" ? "Allowed" : "Rejected";
      confirmCards.delete(id);
    }
  }
  if (msg.kind === 'plan_updated') {
    const li = make('li', 'msg msg-plan-preview'); li.id = msg.id;
    li.append(make('h4', '', 'PLAN.md'), richText((msg.body ?? '').slice(0, 800)));
    const actions = make('div', 'row');
    for (const label of ['Read more', 'View', 'Build']) {
      const control = make('button', 'small', label); control.type = 'button';
      control.onclick = () => { if (label === 'Build') { const plan = msg.details as { revision?: number; planner_id?: string } | undefined; void buildReviewedPlan(plan?.revision, plan?.planner_id); } else openCanvas('planner'); };
      actions.append(control);
    }
    li.append(actions); return li;
  }
  if (msg.kind === "browser_result") {
    const view = browserView([{ seq: 1, task_id: selected ?? "", time: msg.time, kind: msg.kind, data: (msg.details ?? {}) as Record<string, unknown> }]);
    const li = make("li", "msg msg-browser"); li.id = msg.id;
    li.append(make("p", "msg-label", view.screenshot ? view.title || "Browser capture" : `Browser: ${view.status || "unavailable"}`));
    if (view.screenshot) {
      const image = make("img", "browser-image"); image.src = view.screenshot; image.alt = `Screenshot of ${view.url}`; image.loading = "lazy";
      li.append(image, make("p", "browser-caption", view.url));
      const open = make("button", "small", "Open Browser tab"); open.type = "button"; open.onclick = () => openCanvas("browser"); li.append(open);
    } else li.append(make("p", "browser-status", view.error || "No screenshot was captured."));
    return li;
  }
  if (msg.role === "user") {
    const li = make("li", `msg msg-user msg-${msg.kind}`);
    li.id = msg.id;
    const bubble = make("div", "bubble", msg.body ?? "");
    const tools = make("div", "msg-actions user-actions");
    tools.append(copyButton(() => msg.body ?? "", "Copy"));
    li.append(bubble, tools);
    lastRole = "user";
    return li;
  }
  if (msg.role === "system" && msg.state) { lastRole = "system"; return resultCard(msg); }
  if (msg.role === "assistant" && (msg.body || msg.kind === "plan")) {
    const li = assistantMessage(msg, lastRole !== "assistant");
    lastRole = "assistant";
    return li;
  }
  // Pair a tool result with its call so each step reads as one action plus its output.
  if (msg.kind === "agent_tool_result") {
    const name = toolName(msg);
    const index = pendingCalls.findIndex(call => call.name === name);
    const call = index >= 0 ? pendingCalls.splice(index, 1)[0] : undefined;
    if (call && msg.body) {
      const label = call.body.parentElement?.querySelector(".step-arg")?.textContent ?? "";
      call.body.append(codeBlock(msg.body, name === "read_file" ? languageFor(label) : ""));
      if (call.body.parentElement) {
        setClass(call.body.parentElement, "step-static", false);
        if (msg.state === "tool_failed") setClass(call.body.parentElement, "step-failed", true);
      }
      return null;
    }
  }
  const { li, body } = stepRow(msg);
  if (msg.kind === "agent_tool_call") pendingCalls.push({ name: toolName(msg), body });
  lastRole = "tool";
  return li;
}

function nearBottom(thread: HTMLElement): boolean {
  return !(thread.scrollHeight > 0) || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
}
const EARLIER_LIMIT = 150;
function renderMessageStream(messages: ChatMessage[]): void {
  const stream = el("message-stream"), thread = el("thread");
  const stick = nearBottom(thread);
  if (!rendered.size && !showAllMessages && messages.length > EARLIER_LIMIT) {
    // Long conversations open on their newest messages; older ones load on request.
    const hidden = messages.slice(0, messages.length - EARLIER_LIMIT);
    for (const msg of hidden) rendered.add(msg.id);
    const li = make("li", "msg show-earlier");
    const more = make("button", "ghost small", `Show ${hidden.length} earlier messages`); more.type = "button";
    more.onclick = () => { showAllMessages = true; rerenderConversation(); };
    li.append(more);
    stream.append(li);
  }
  for (const msg of messages) {
    if (rendered.has(msg.id)) continue;
    rendered.add(msg.id);
    const node = renderMessage(msg);
    if (node) addToStream(stream, node);
  }
  if (stick) thread.scrollTop = thread.scrollHeight;
}
/** Consecutive steps share one block; three or more fold into "N steps" once the agent moves on. */
function addToStream(stream: HTMLElement, node: HTMLLIElement): void {
  if (node.dataset.step !== "1") { closeStepGroup(); stream.append(node); return; }
  if (!stepGroup || stream.lastElementChild !== stepGroup.li) {
    closeStepGroup();
    const li = make("li", "msg step-group few"), details = make("details"), summary = make("summary"), list = make("ol", "step-list");
    details.open = true;
    summary.addEventListener("click", event => { if (li.className.includes("few")) event.preventDefault(); });
    details.append(summary, list);
    li.append(details);
    stream.append(li);
    stepGroup = { li, list, details, summary, count: 0 };
  }
  stepGroup.list.append(node);
  stepGroup.count++;
  stepGroup.summary.replaceChildren(icon("chevron"), make("span", "", `${stepGroup.count} steps`));
  setClass(stepGroup.li, "few", stepGroup.count < 3);
}
function closeStepGroup(): void {
  if (stepGroup && stepGroup.count >= 3) stepGroup.details.open = false;
  stepGroup = null;
}
function rerenderConversation(): void {
  rendered = new Set(); pendingCalls = []; lastRole = ""; stepGroup = null; confirmCards.clear();
  el("message-stream").replaceChildren();
  renderMessageStream(eventsToChat(liveEvents, liveObjective));
}

// --- diff review canvas ---------------------------------------------------
function showReviewSelection(): void {
  const file = review.files.find(item => item.path === selectedFile);
  const label = file ? `${fileStatusLabel(file.status)} · ${file.path}` : review.files.length ? "All changes" : "Combined diff";
  const sel = el("selected-file");
  sel.textContent = label;
  const content = file ? file.diff : review.diff;
  const diff = el("diff");
  if (renderedDiff !== content || diff.textContent !== content) {
    const code = diffLines(content);
    diff.replaceChildren(...(content ? code : []));
    renderedDiff = content;
  }
  diff.hidden = !content;
  sel.hidden = !content;
  el("copy-diff").hidden = !content;
  for (const control of el("review-controls").querySelectorAll("button")) {
    const current = control.dataset.path === selectedFile || (selectedFile === null && control.dataset.all === "true");
    control.setAttribute("aria-pressed", String(current));
  }
}

function renderReview(taskId: string, state: string, result: unknown): void {
  review = reviewDiff(result);
  const key = JSON.stringify([taskId, review.files.map(file => [file.path, file.status])]);
  if (key !== reviewKey) {
    if (reviewTask !== taskId || !review.files.some(file => file.path === selectedFile)) selectedFile = null;
    reviewTask = taskId; reviewKey = key;
    const controls: HTMLButtonElement[] = [];
    const addControl = (label: string, path: string | null, status?: string): void => {
      const control = document.createElement("button");
      control.type = "button"; control.className = "secondary"; control.textContent = label;
      control.setAttribute("aria-controls", "diff");
      control.title = label;
      if (path === null) control.dataset.all = "true";
      else control.dataset.path = path;
      if (status) control.dataset.status = status;
      control.onclick = () => { selectedFile = path; showReviewSelection(); };
      controls.push(control);
    };
    if (review.files.length) {
      addControl("All changes", null);
      for (const file of review.files) addControl(`${fileStatusLabel(file.status)} · ${file.path}`, file.path, file.status);
    }
    el("review-controls").replaceChildren(...controls);
  }
  (el("review-controls") as HTMLElement).hidden = !review.files.length;
  const note = el("review-note");
  note.textContent = !review.diff
    ? result ? "No candidate diff in this result." : isTerminal(state) ? "No candidate diff for this stopped task." : "No candidate diff yet."
    : review.metadata === "invalid" ? "Per-file details could not be validated. Showing the combined diff." : review.metadata === "legacy" ? "Per-file details unavailable. Showing the combined diff." : "";
  note.hidden = !note.textContent;
  showReviewSelection();
  const count = review.files.length || (review.diff ? 1 : 0);
  el("changes-count").textContent = count ? String(count) : "";
  el("canvas-count").textContent = review.files.length ? `${review.files.length} file${review.files.length === 1 ? "" : "s"}` : "";
  el("toggle-canvas").hidden = !review.diff;
  if (review.diff && canvasAutoOpened !== taskId && isTerminal(state)) {
    canvasAutoOpened = taskId;
    if (wide("(min-width: 1101px)")) openCanvas("changes");
  }
}

function setCanvas(open: boolean): void {
  el("canvas").hidden = !open;
  el("toggle-canvas").setAttribute("aria-pressed", String(open && canvasTab === "changes"));
}

// --- canvas tabs: changes, files, git -------------------------------------
let canvasTab = "changes", projectFiles: string[] = [], openFile: string | null = null, fileVersion = "workspace";
let applyStep: "idle" | "confirm" | "busy" | "done" = "idle", lastApplied: string[] = [];
function openCanvas(tab: string): void {
  canvasTab = tab;
  const canvas = el("canvas");
  if (typeof canvas.querySelectorAll === "function") {
    for (const node of canvas.querySelectorAll<HTMLElement>(".canvas-tab")) node.setAttribute("aria-selected", String(node.dataset.tab === tab));
    for (const pane of canvas.querySelectorAll<HTMLElement>(".canvas-body")) pane.hidden = pane.dataset.pane !== tab;
  }
  setCanvas(true);
  if (tab === "files") renderFileList();
  if (tab === "git") void loadGit();
  if (tab === "terminal") renderTerminal();
  if (tab === "browser") renderBrowser();
  if (tab === 'planner') { renderPlanner(); if (selected) void loadPlan(selected); }
  if (tab === 'tasklist') renderChecklist();
}
function changedPaths(): Set<string> { return new Set(review.files.map(file => file.path)); }

function renderTerminal(): void {
  const view = terminalTranscript(liveEvents);
  const terminal = el("terminal-view");
  const empty = el("terminal-empty");
  terminal.textContent = view.text;
  terminal.hidden = !view.commands.length;
  empty.hidden = !!view.commands.length;
  empty.textContent = selectedState && (!isTerminal(selectedState) || selectedState === "failed")
    ? "Waiting for runtime to start..."
    : "No terminal output yet. Commands run by the agent will appear here.";
  if (view.commands.some(command => command.running)) terminal.scrollTop = terminal.scrollHeight;
}

function renderBrowser(): void {
  const view = browserView(liveEvents);
  el("browser-current-url").textContent = view.url || "No URL loaded";
  const link = el("browser-external") as HTMLAnchorElement;
  link.hidden = !view.url; link.href = view.url || "#";
  button("browser-external-empty").hidden = !!view.url;
  el("browser-empty").hidden = !!view.screenshot;
  const mount = el("browser-snapshot"); mount.hidden = !view.screenshot;
  if (mount.dataset.screenshot !== view.screenshot) {
    mount.dataset.screenshot = view.screenshot;
    mount.replaceChildren();
    if (view.screenshot) {
      const image = make("img", "browser-image"); image.src = view.screenshot; image.alt = view.title || `Screenshot of ${view.url}`;
      image.addEventListener("error", () => { el("browser-status").textContent = "This screenshot is no longer available. Open the URL again to capture it."; });
      mount.append(image);
    }
  }
  el("browser-status").textContent = view.status === "running" ? "Opening page…" : view.error || (view.status === "passed" ? "Page captured. Web content is untrusted." : "");
  button("browser-open").disabled = !selected || view.status === "running";
  button("browser-stop").disabled = view.status !== "running";
}

async function loadPlan(task: string): Promise<void> {
  const epoch = viewEpoch;
  const plan = parsePlan(await api(`/api/tasks/${encodeURIComponent(task)}/planner`));
  if (task !== selected || epoch !== viewEpoch) return;
  currentPlan = plan; renderPlanner();
}
function renderPlanner(): void {
  const content = currentPlan?.content ?? '';
  el('planner-empty').hidden = !!content;
  const mount = el('planner-content'); mount.hidden = !content;
  if (mount.dataset.content !== content) { mount.dataset.content = content; mount.replaceChildren(...(content ? [richText(content)] : [])); }
  button('planner-create').disabled = !selected || !!currentPlan?.planner_id || !isTerminal(selectedState);
  button('planner-build').disabled = !content || !isTerminal(selectedState) || currentPlan?.built_revision === currentPlan?.revision;
  el('planner-note').textContent = content ? `PLAN.md · revision ${currentPlan?.revision}. Review before Build. Files change only through Apply.` : 'Plans are reviewed before building. Workspace files change only through Apply.';
}
function renderChecklist(): void {
  const view = taskChecklist(liveEvents);
  el('tasklist-tab').hidden = !view.used;
  const list = el('task-checklist'); list.replaceChildren();
  const labels = { todo: 'To do', in_progress: 'In progress', done: 'Done' };
  const symbols = { todo: '○', in_progress: '●', done: '✓' };
  for (const task of view.tasks) {
    const row = make('li', 'checklist-step'); row.dataset.active = String(task.status === 'in_progress');
    const marker = make('span', 'checklist-icon', symbols[task.status]); marker.setAttribute('aria-hidden', 'true');
    row.append(marker, make('span', 'checklist-status', labels[task.status]), make('span', '', task.title));
    list.append(row);
  }
  if (view.used && !view.tasks.length) list.append(make('li', '', 'No checklist steps.'));
}
async function enterPlanning(): Promise<void> {
  if (!selected) { select('mode').value = 'plan'; syncMode(); syncComposer(); textarea('objective').focus(); return; }
  if (!isTerminal(selectedState)) throw new Error('Wait for the current turn before planning.');
  const plan = parsePlan(await api(`/api/tasks/${selected}/planner`, {}));
  if (!plan?.planner_id) throw new Error('Planner initialization failed.');
  select('mode').value = 'plan'; syncMode(); selectTask(plan.planner_id);
  await loadHistory(); await observe();
  message('Planning agent initialized');
}
async function enterCode(): Promise<void> {
  const target = parentTask || currentPlan?.task_id;
  select('mode').value = 'agent'; syncMode();
  if (target && target !== selected) { selectTask(target); await loadHistory(); await observe(); }
  syncComposer();
}
async function buildReviewedPlan(expectedRevision?: number, expectedPlanner?: string): Promise<void> {
  if (!selected || !currentPlan?.content) return message('There is no plan to build.', true);
  if (expectedRevision !== undefined && expectedRevision !== currentPlan.revision) return message('The plan changed. Review its latest revision in the Planner tab.', true);
  if (expectedPlanner !== undefined && expectedPlanner !== currentPlan.planner_id) return message('The planner changed. Review the current plan in the Planner tab.', true);
  try {
    const task = selected, revision = currentPlan.revision, planner_id = currentPlan.planner_id;
    select('mode').value = 'agent'; syncMode();
    const request = composerRequest('Execute the plan based on the PLAN.md file.');
    const result = await api<{ id: string }>(`/api/tasks/${task}/planner/build`, { ...request, confirm: true, revision, planner_id });
    selectTask(result.id); await loadHistory(); await observe();
  } catch (error) { message(error instanceof Error ? error.message : 'Could not build this plan', true); }
}
el('planner-create').addEventListener('click', () => { void enterPlanning().catch(error => message(String(error), true)); });
el('planner-build').addEventListener('click', () => { void buildReviewedPlan(); });

el("browser-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!selected) return message("Choose a conversation first.", true);
  if (!input("browser-consent").checked) return message("Approve Docker and public web access for this capture.", true);
  const task = selected;
  button("browser-open").disabled = true; button("browser-stop").disabled = false;
  el("browser-status").textContent = "Opening page…";
  try {
    await api(`/api/tasks/${task}/browser`, { url: input("browser-url").value, approve_browser: true, approve_execution: true });
    if (selected === task) { watching = true; await observe(); }
  } catch (error) { message(error instanceof Error ? error.message : "Browser capture failed", true); }
  finally {
    input("browser-consent").checked = false;
    if (selected === task) { button("browser-open").disabled = false; button("browser-stop").disabled = true; }
  }
});
el("browser-stop").addEventListener("click", () => {
  if (selected) void api(`/api/tasks/${selected}/browser/stop`, {}).catch(error => message(String(error), true));
});

async function loadBrowserSettings(): Promise<void> {
  const info = await api<{ enabled: boolean; image: string; availability: string; launch_verified: boolean }>("/api/browser");
  input("browser-enabled").checked = info.enabled;
  el("browser-availability").textContent = info.availability === "image_ready" ? info.launch_verified ? "Browser image installed. A capture completed in this service session." : "Browser image installed. A successful launch has not been verified." : `Browser runtime unavailable. Build the local ${info.image} image and start Docker.`;
}
el("browser-enabled").addEventListener("change", async () => {
  try { await request("/api/browser", "PATCH", { enabled: input("browser-enabled").checked }); message("Browser setting saved."); }
  catch (error) { message(error instanceof Error ? error.message : "Could not save browser setting", true); await loadBrowserSettings(); }
});

function renderFileList(): void {
  const query = ((el("file-search") as HTMLInputElement).value ?? "").trim().toLowerCase();
  const changed = changedPaths();
  const all = [...new Set([...review.files.filter(f => f.status === "added").map(f => f.path), ...projectFiles])].sort();
  const matches = all.filter(path => !query || path.toLowerCase().includes(query));
  if (!query) {
    el("file-list").replaceChildren(...treeRows(all, changed));
    if (!all.length) el("file-list").replaceChildren(make("p", "more", "No files"));
    return;
  }
  const nodes: HTMLElement[] = matches.slice(0, 400).map(path => {
    const item = make("button", "", path);
    item.type = "button";
    item.title = path;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(path === openFile));
    if (changed.has(path)) item.dataset.changed = "true";
    item.onclick = () => { void showFile(path, changed.has(path) && selected ? "candidate" : "workspace"); };
    return item;
  });
  if (matches.length > 400) nodes.push(make("p", "more", `${matches.length - 400} more — refine the search`));
  if (!matches.length) nodes.push(make("p", "more", query ? "No matching files" : "No files"));
  el("file-list").replaceChildren(...nodes);
}
async function showFile(path: string, version: string): Promise<void> {
  try {
    const query = `/api/files/content?path=${encodeURIComponent(path)}${version === "candidate" && selected ? `&task=${encodeURIComponent(selected)}` : ""}`;
    const data = await api<{ path: string; version: string; text: string }>(query);
    openFile = path; fileVersion = data.version; fileText = data.text;
    openTabs = [...openTabs.filter(tab => tab !== path), path].slice(-8);
    for (const folder of parentFolders(path)) expandedDirs.add(folder);
    el("file-name").textContent = path;
    const markdown = /\.(md|markdown)$/i.test(path);
    el("file-preview").hidden = !markdown;
    if (!markdown) filePreview = false;
    showFilePreview();
    renderFileTabs();
    const lines = data.text.split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    el("file-view").replaceChildren(...lines.map((line, index) => {
      const span = make("span", "ln", line + "\n");
      span.dataset.n = String(index + 1);
      return span;
    }));
    el("copy-file").hidden = false;
    const changed = changedPaths().has(path) && !!selected;
    el("file-version").hidden = !changed;
    for (const node of el("file-version").querySelectorAll<HTMLElement>("button")) node.setAttribute("aria-pressed", String(node.dataset.version === data.version));
    renderFileList();
  } catch (error) { message(error instanceof Error ? error.message : "Could not open the file", true); }
}

interface GitInfo {
  repo: boolean; installed: boolean; branch?: string; ahead?: number | null;
  status?: { path: string; status: string; staged: boolean }[];
  commits?: { short: string; subject: string; author: string; time: number }[];
}
async function loadGit(): Promise<void> {
  const panel = el("git-panel");
  try {
    const info = await api<GitInfo>("/api/git");
    el("git-count").textContent = info.status?.length ? String(info.status.length) : "";
    if (!info.repo) {
      panel.replaceChildren(make("p", "muted", info.installed ? "This workspace is not a Git repository. Run git init in it to track and commit changes." : "Git is not installed on this machine."));
      return;
    }
    const head = make("div", "git-branch");
    head.append(icon("diff"), make("span", "", info.branch ?? ""), make("span", "muted", info.ahead ? `${info.ahead} commit${info.ahead === 1 ? "" : "s"} ahead of upstream` : ""));
    const statusBox = make("section");
    statusBox.append(make("h3", "", info.status?.length ? "Changed files" : "Working tree clean"));
    const list = make("div", "git-status");
    for (const entry of info.status ?? []) {
      const row = make("label");
      const box = make("input");
      box.type = "checkbox"; box.value = entry.path; box.checked = lastApplied.includes(entry.path);
      row.append(box, make("span", "", entry.path), make("span", `tag tag-${entry.status}`, entry.status));
      list.append(row);
    }
    statusBox.append(list);
    const form = make("form", "git-commit");
    const field = make("textarea");
    field.placeholder = "Commit message";
    field.setAttribute("aria-label", "Commit message");
    field.value = lastApplied.length && selected ? conversationLabel(lastTasks.find(t => t.id === selected) ?? { id: selected, state: "" }) : "";
    const commit = make("button", "primary small");
    commit.type = "submit";
    commit.append(icon("check"), make("span", "", "Commit selected"));
    form.append(field, commit);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const files = [...list.querySelectorAll<HTMLInputElement>("input:checked")].map(box => box.value);
      try {
        const made = await request<{ short: string }>("/api/git/commit", "POST", { message: field.value, files });
        lastApplied = [];
        message(`Committed ${made.short}.`);
        await loadGit();
      } catch (error) { message(error instanceof Error ? error.message : "Commit failed", true); }
    });
    const logBox = make("section");
    logBox.append(make("h3", "", "Recent commits"));
    const log = make("ol", "git-log");
    for (const entry of info.commits ?? []) {
      const row = make("li");
      row.append(make("code", "", entry.short), make("span", "", entry.subject), make("small", "", `${entry.author} · ${new Date(entry.time * 1000).toLocaleString()}`));
      log.append(row);
    }
    if (!info.commits?.length) log.append(make("li", "muted", "No commits yet"));
    logBox.append(log);
    panel.replaceChildren(head, ...(info.status?.length ? [statusBox, form] : [statusBox]), logBox);
  } catch (error) {
    panel.replaceChildren(make("p", "error", error instanceof Error ? error.message : "Git status unavailable"));
  }
}

function syncApplyBar(): void {
  const bar = el("apply-bar");
  bar.hidden = !(selected && review.diff && isTerminal(selectedState));
  setClass(bar, "confirming", applyStep === "confirm");
  setClass(bar, "done", applyStep === "done");
  const count = review.files.length || 1;
  el("apply-note").textContent = applyStep === "confirm" ? `Write ${count} file${count === 1 ? "" : "s"} into ${projectName || "your workspace"}? Files you edited since are never overwritten.`
    : applyStep === "done" ? `Applied to ${projectName || "your workspace"}.` : "Changes live in an isolated copy until you apply them.";
  el("apply-label").textContent = applyStep === "confirm" ? "Confirm apply" : applyStep === "busy" ? "Applying…" : "Apply to workspace";
  (el("apply") as HTMLButtonElement).disabled = applyStep === "busy";
  el("apply").hidden = applyStep === "done";
  el("apply-cancel").hidden = applyStep !== "confirm";
  el("apply-commit").hidden = applyStep !== "done";
}
async function applyChanges(): Promise<void> {
  if (!selected) return;
  if (applyStep !== "confirm") { applyStep = "confirm"; syncApplyBar(); return; }
  applyStep = "busy"; syncApplyBar();
  try {
    const done = await request<{ applied: string[]; unchanged: string[] }>(`/api/tasks/${encodeURIComponent(selected)}/apply`, "POST", { confirm: true });
    lastApplied = done.applied;
    storageSet("dikdik.onboarding.applied", "1");
    applyStep = "done";
    message(done.applied.length ? `Applied ${done.applied.length} file${done.applied.length === 1 ? "" : "s"}.` : "Already applied; nothing to write.");
    watching = true;  // show the "applied" note in the conversation
    await Promise.all([project(), observe()]);
    void loadGit();
  } catch (error) {
    applyStep = "idle";
    message(error instanceof Error ? error.message : "Apply failed", true);
  }
  syncApplyBar();
}

// --- task lifecycle / polling ---------------------------------------------
function resetConversation(): void {
  rendered = new Set(); pendingCalls = []; lastRole = ""; renderedDiff = ""; applyStep = "idle"; lastApplied = [];
  confirmCards.clear(); taskRequest = null; stepGroup = null; showAllMessages = false;
  el("goal-banner").hidden = true; el("context-meter").hidden = true; el("pause").hidden = true; el("side-answer").hidden = true;
  el("message-stream").replaceChildren();
  el("review-controls").replaceChildren();
  el("diff").replaceChildren();
  el("review-note").textContent = "";
  liveEvents = [];
  currentPlan = null; parentTask = null; observedTaskMode = '';
  input("browser-consent").checked = false;
  input("browser-approval").checked = false;
  input("browser-url").value = "";
  renderTerminal();
  renderBrowser();
  renderPlanner(); renderChecklist();
  el("working").hidden = true;
  el("toggle-canvas").hidden = true;
  setCanvas(false);
  button("cancel").disabled = true;
}
function setUrl(task: string | null): void {
  const loc = globalThis.location;
  if (!loc || typeof globalThis.history?.replaceState !== "function") return;
  globalThis.history.replaceState(null, "", task ? `${loc.pathname}?task=${encodeURIComponent(task)}` : loc.pathname);
}
function syncComposer(): void {
  textarea("objective").placeholder = select('mode').value === 'plan' ? 'Let’s work on a plan' : selected ? "Reply to Dikdik…" : "Ask Dikdik to build, fix or change something…";
  el("task-actions").hidden = !selected;
  syncSend();
}
function syncSend(): void {
  button('start').disabled = submitting || (!textarea('objective').value.trim() && !attached.length) || !el("model-banner").hidden;
  textarea('objective').required = !attached.length;  // attachments alone are a message
}
function selectTask(id: string): void {
  composerSize.reset();
  stopStream();
  viewEpoch++;
  selected = id; watching = true; selectedState = "";
  resetConversation();
  el("chat-title").textContent = "Loading…";
  setState("");
  markCurrent();
  closeSidebarDrawer();
  setUrl(id);
  syncComposer();
  void composerDrafts.select(id);
}
function newTask(): void {
  composerSize.reset();
  stopStream();
  viewEpoch++;
  selected = null; watching = false; reviewTask = null; reviewKey = ""; selectedState = "";
  resetConversation();
  el("chat-title").textContent = "New task";
  setTitle("");
  setState("");
  markCurrent();
  closeSidebarDrawer();
  setUrl(null);
  syncComposer();
  void composerDrafts.select('home');
  textarea("objective").focus?.();
}
function setTitle(title: string): void {
  if (globalThis.document && "title" in document) document.title = title ? `${title} · Dikdik` : "Dikdik";
}
function setState(state: string): void {
  const pill = el("task-state");
  pill.textContent = state;
  pill.className = state ? `state-pill state-${state}` : "state-pill";
}

let messageTimer: ReturnType<typeof setTimeout> | undefined;
function message(value: string, error = false): void {
  const m = el("message");
  if (messageTimer !== undefined) clearTimeout(messageTimer);
  m.textContent = value;
  m.className = error ? "error" : "";
  m.hidden = false;
  messageTimer = setTimeout(() => { m.hidden = true; }, error ? 7000 : 3500);
}

class RequestError extends Error {
  constructor(message: string, readonly status: number, readonly data?: unknown) { super(message); }
}
async function request<T>(path: string, method: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  const init: RequestInit = { method, headers, credentials: "same-origin" };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fetch(path, init);
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data === "object" && data !== null && "error" in data && typeof data.error === "string" ? data.error : "Request failed";
    throw new RequestError(detail, response.status, data);
  }
  return data as T;
}
const api = <T>(path: string, body?: unknown): Promise<T> => request<T>(path, body === undefined ? "GET" : "POST", body);
const apiM4 = <T>(path: string, method: string, body?: unknown): Promise<T> => request<T>(path, method, body);
const composerDrafts = new ComposerDrafts(request, (content, status) => {
  if (content !== null) textarea('objective').value = content;
  const labels = { loading: 'Loading draft…', saving: 'Saving draft…', saved: 'Draft saved', unsaved: 'Draft not saved. Keep this tab open.', conflict: 'This draft changed in another tab. Choose which version to keep.' };
  el('draft-status').textContent = labels[status.state];
  el('draft-status-row').hidden = status.state === 'saved' && !textarea('objective').value;
  el('draft-use-saved').hidden = status.state !== 'conflict'; el('draft-keep-local').hidden = status.state !== 'conflict';
  el('draft-retry').hidden = status.state !== 'unsaved'; el('draft-conflict').hidden = status.state !== 'conflict';
  el('draft-remote').textContent = status.remote ?? '';
  autosize();
});
el('draft-use-saved').addEventListener('click', () => { void composerDrafts.resolve('server'); });
el('draft-keep-local').addEventListener('click', () => { void composerDrafts.resolve('local'); });
el('draft-retry').addEventListener('click', () => { void composerDrafts.resolve('local'); });
globalThis.addEventListener?.('beforeunload', event => { if (composerDrafts.hasUnsaved()) { event.preventDefault(); (event as BeforeUnloadEvent).returnValue = ''; } });

// The composer names the model the next task will use, or says what's missing.
async function loadModel(): Promise<void> {
  const info = await api<ActiveModel>("/api/model");
  const pill = el("model-pill");
  setClass(pill, "unset", !info.configured);
  modelReady = info.configured;
  el("model-label").textContent = modelOverride ? modelOverride.name : info.configured ? info.model ?? "Model" : "Set up a model";
  setClass(pill, "override", modelOverride !== null);
  pill.title = info.configured
    ? info.source === "profile" ? `${info.model} · ${info.provider_name} (profile “${info.profile}”). Change it in Settings → Models.` : `${info.model} from server environment variables`
    : info.problem ?? "No model is set up";
  syncModelBanner(); renderOnboarding(); syncSend();
}

async function project(): Promise<void> {
  const data = await api<Project>("/api/project");
  projectName = data.name;
  projectFiles = data.files.map(file => file.path);
  projectFileSet = new Set(projectFiles);
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
  const summary = `${plural(data.files.length, "file")} · ${plural(data.python_tests.length, "test")}`;
  el("project-name").textContent = data.name;
  el("project-summary").textContent = summary;
  el("settings-project").textContent = data.name;
  el("settings-indexer").textContent = (data as Project & { indexer?: string }).indexer === "rust" ? "Rust (native, parallel)" : "Python (run scripts/build_native.py for the faster Rust indexer)";
  el("settings-inventory").textContent = `${data.files.length} text files · ${data.python_tests.length} potential Python tests · ${data.manifests.length} manifests`;
  el("welcome-sub").textContent = `Working in ${data.name}. Describe a bug, feature or change — Dikdik works on an isolated copy and shows you every diff.`;
}

let lastTasks: TaskSummary[] = [];
function markCurrent(): void {
  for (const item of el("history").children) {
    const control = item as HTMLElement;
    control.setAttribute("aria-current", String(control.dataset?.id === selected));
  }
}
function taskRow(task: TaskSummary): HTMLElement {
  const row = taskRowBase(task);
  const tags = task.tags ?? [];
  if (tags.length) {
    const chip = make("span", "conv-tag", tags[0] ?? "");
    if (tags.length > 1) chip.textContent += ` +${tags.length - 1}`;
    row.insertBefore(chip, row.querySelector(".conv-more"));
    row.title += ` · ${tags.join(", ")}`;
  }
  return row;
}
function taskRowBase(task: TaskSummary): HTMLElement {
  // A div with button semantics, so the row can hold its own actions button.
  const item = make("div", "conv-item");
  item.setAttribute("role", "button");
  item.tabIndex = 0;
  item.dataset.id = task.id;
  const name = conversationLabel(task);
  const label = make("span", "conv-obj", name), state = make("span", `conv-state state-${task.state}`, task.state);
  item.title = `${name} — ${task.state}`;
  item.append(state, label);
  if (task.pinned) item.append(make("span", "conv-pin", "Pinned"));
  const more = make("button", "ghost icon conv-more");
  more.type = "button";
  more.setAttribute("aria-label", `Actions for ${name}`);
  more.append(icon("more"));
  more.addEventListener("click", event => { event.stopPropagation(); openTaskMenu(task.id, more); });
  item.append(more);
  item.setAttribute("aria-current", String(task.id === selected));
  item.onclick = () => { selectTask(task.id); void observe(); };
  item.addEventListener("keydown", (event: KeyboardEvent) => {
    if (event.target === item && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); item.click(); }
  });
  return item;
}
function collapsedFolders(): Set<string> {
  try { return new Set(JSON.parse(storageGet("dikdik.folders.collapsed") ?? "[]") as string[]); } catch { return new Set(); }
}
function renderHistory(): void {
  const query = ((el("task-search") as HTMLInputElement).value ?? "").trim();
  const view = organiseTasks(lastTasks, {
    query, tag: activeTag, sort: (select("task-sort").value || "recent") as TaskSort, sinceDays: Number(select("task-since").value) || 0,
    showArchived, selected, now: Date.now() / 1000,
  });
  if (activeTag && !view.tags.includes(activeTag)) activeTag = "";
  folderOptions = view.folderNames;
  const closed = collapsedFolders();
  const nodes: HTMLElement[] = [];
  if (view.pinned.length) nodes.push(make("p", "list-head", "Pinned"), ...view.pinned.map(taskRow));
  for (const folder of view.folders) {
    const shut = closed.has(folder.name) && !folder.tasks.some(task => task.id === selected) && !query;
    const head = make("button", "folder-head"); head.type = "button";
    head.setAttribute("aria-expanded", String(!shut));
    head.append(icon("chevron"), icon("folder"), make("span", "folder-name", folder.name), make("span", "folder-count", String(folder.tasks.length)));
    head.onclick = () => {
      const next = collapsedFolders();
      if (next.has(folder.name)) next.delete(folder.name); else next.add(folder.name);
      storageSet("dikdik.folders.collapsed", JSON.stringify([...next]));
      renderHistory();
    };
    nodes.push(head);
    if (!shut) nodes.push(...folder.tasks.map(taskRow));
  }
  const rest = view.recent.slice(0, listLimit);
  if (rest.length && (view.pinned.length || view.folders.length)) nodes.push(make("p", "list-head", showArchived ? "All" : "Recent"));
  nodes.push(...rest.map(taskRow));
  if (view.recent.length > listLimit) {
    const more = make("button", "ghost small show-archived", `Show more (${view.recent.length - listLimit})`); more.type = "button";
    more.onclick = () => { listLimit += 40; renderHistory(); };
    nodes.push(more);
  }
  if (view.older) {
    const older = make("button", "ghost small show-archived", `${view.older} older hidden · show all`); older.type = "button";
    older.onclick = () => { select("task-since").value = "0"; renderHistory(); };
    nodes.push(older);
  }
  if (!nodes.length) nodes.push(make("div", "conv-empty", query || activeTag ? "No matching tasks" : "No tasks yet"));
  el("history").replaceChildren(...nodes);
  el("recent-head").hidden = view.pinned.length > 0 || view.folders.length > 0;
  const chips = el("tag-chips");
  chips.hidden = !view.tags.length;
  chips.replaceChildren(...view.tags.map(tag => {
    const chip = make("button", "tag-chip", tag); chip.type = "button";
    chip.setAttribute("aria-pressed", String(tag === activeTag));
    chip.onclick = () => { activeTag = activeTag === tag ? "" : tag; renderHistory(); };
    return chip;
  }));
  el("show-archived").hidden = view.archived === 0;
  el("show-archived").textContent = showArchived ? "Hide archived" : `Show archived (${view.archived})`;
  el("delete-archived").hidden = !showArchived || view.archived === 0;
  if (!deletingArchived) el("delete-archived").textContent = `Delete all archived (${view.archived})`;
}
let deletingArchived = false;
async function loadHistory(): Promise<void> {
  lastTasks = await api<TaskSummary[]>("/api/tasks");
  renderHistory();
  renderOnboarding();
}

// --- task actions menu ----------------------------------------------------
let menuTask: string | null = null, confirmingDelete = false;
function closeTaskMenu(): void { el("task-menu").hidden = true; menuTask = null; confirmingDelete = false; }
function openTaskMenu(task: string, anchor: HTMLElement): void {
  const info = lastTasks.find(item => item.id === task);
  const menu = el("task-menu");
  menuTask = task; confirmingDelete = false;
  for (const node of menu.querySelectorAll<HTMLButtonElement>("button")) {
    const action = node.dataset.action;
    const label = node.querySelector("span");
    if (label && action === "pin") label.textContent = info?.pinned ? "Unpin" : "Pin";
    if (label && action === "archive") label.textContent = info?.archived ? "Unarchive" : "Archive";
    if (label && action === "delete") label.textContent = "Delete";
  }
  const box = anchor.getBoundingClientRect();
  menu.hidden = false;
  const width = menu.offsetWidth, height = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(box.right - width, innerWidth - width - 8))}px`;
  menu.style.top = `${box.bottom + height + 8 > innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4}px`;
  menu.querySelector<HTMLButtonElement>("button")?.focus();
}
async function patchTask(task: string, changes: Record<string, unknown>): Promise<void> {
  await request(`/api/tasks/${encodeURIComponent(task)}`, "PATCH", changes);
  await loadHistory();
  if (task === selected && "title" in changes) el("chat-title").textContent = conversationLabel(lastTasks.find(item => item.id === task) ?? { id: task, state: "" });
}
function startRename(task: string): void {
  const row = [...el("history").querySelectorAll<HTMLElement>(".conv-item")].find(node => node.dataset.id === task);
  const info = lastTasks.find(item => item.id === task);
  if (!row || !info) return;
  const field = make("input", "conv-rename");
  field.value = conversationLabel(info);
  field.setAttribute("aria-label", "Task name");
  row.replaceChildren(field);
  field.focus(); field.select();
  let done = false;
  const finish = async (save: boolean): Promise<void> => {
    if (done) return;
    done = true;
    try { if (save && field.value.trim() !== conversationLabel(info)) await patchTask(task, { title: field.value }); else renderHistory(); }
    catch (error) { message(error instanceof Error ? error.message : "Rename failed", true); renderHistory(); }
  };
  field.addEventListener("click", event => event.stopPropagation());
  field.addEventListener("keydown", (event: KeyboardEvent) => {
    event.stopPropagation();
    if (event.key === "Enter") void finish(true);
    if (event.key === "Escape") void finish(false);
  });
  field.addEventListener("blur", () => { void finish(true); });
}
function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = make("a");
  link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function exportTask(task: string): Promise<void> {
  const data = await api<Task>("/api/tasks/" + encodeURIComponent(task));
  const title = conversationLabel({ ...data, objective: data.request.objective, title: lastTasks.find(item => item.id === task)?.title ?? "" });
  const diff = typeof data.result?.diff === "string" ? data.result.diff : "";
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || "task";
  download(`dikdik-${slug}.md`, transcriptMarkdown(title, eventsToChat(data.events as TaskEvent[], data.request.objective), diff), "text/markdown");
}
async function runMenuAction(action: string): Promise<void> {
  const task = menuTask;
  if (!task) return;
  const info = lastTasks.find(item => item.id === task);
  try {
    if (action === "rename") { closeTaskMenu(); startRename(task); }
    else if (action === "pin") { closeTaskMenu(); await patchTask(task, { pinned: !info?.pinned }); }
    else if (action === "archive") { closeTaskMenu(); await patchTask(task, { archived: !info?.archived }); message(info?.archived ? "Task restored." : "Task archived."); }
    else if (action === "export") { closeTaskMenu(); await exportTask(task); }
    else if (action === "fork") { closeTaskMenu(); await forkTask(task); }
    else if (action === "organise") { closeTaskMenu(); openMeta(task); }
    else if (action === "download") { closeTaskMenu(); await downloadTask(task); }
    else if (action === "info") { closeTaskMenu(); await showAgentInfo(); }
    else if (action === "delete") {
      if (!confirmingDelete) {
        confirmingDelete = true;
        const label = el("task-menu").querySelector<HTMLElement>("[data-action=delete] span");
        if (label) label.textContent = "Click again to delete";
        return;
      }
      closeTaskMenu();
      await request(`/api/tasks/${encodeURIComponent(task)}`, "DELETE");
      if (task === selected) newTask();
      await loadHistory();
      message("Task deleted.");
    }
  } catch (error) { message(error instanceof Error ? error.message : "Action failed", true); }
}

// --- live stream: events as they are stored, the reply as it is written ------
let live: EventSource | null = null, liveTask: string | null = null, liveEvents: TaskEvent[] = [], liveObjective = "", liveSeq = 0;
function stopStream(): void {
  live?.close();
  live = null; liveTask = null;
  showLiveReply("");
}
function showLiveReply(text: string): void {
  const existing = document.getElementById?.("live-reply");
  if (!text) {
    existing?.remove?.();
    if (existing) el("working-label").textContent = "Working";
    return;
  }
  const li = existing ?? make("li", "msg msg-assistant msg-live");
  if (!existing) {
    li.id = "live-reply";
    const avatar = make("img", "avatar"); avatar.src = "/icon.svg"; avatar.alt = "";
    li.append(avatar, make("div"));
  }
  const body = li.lastElementChild as HTMLElement;
  body.replaceChildren(make("div", "msg-label", "Dikdik"), richText(text));
  el("message-stream").append(li);  // keep it last
  el("working-label").textContent = "Writing";
  const thread = el("thread");
  if (nearBottom(thread)) thread.scrollTop = thread.scrollHeight;
}
function startStream(id: string): void {
  // The stream rides on the session cookie; a pasted token keeps 1-second polling instead.
  if (typeof globalThis.EventSource !== "function" || token || liveTask === id) return;
  stopStream();
  liveTask = id;
  const source = new EventSource(`/api/tasks/${encodeURIComponent(id)}/stream?after=${liveSeq}`);
  live = source;
  source.addEventListener("task", event => {
    if (id !== selected) return stopStream();
    const record = JSON.parse((event as MessageEvent).data) as TaskEvent;
    if (record.seq <= liveSeq) return;  // a reconnect replays from the start
    liveSeq = record.seq;
    liveEvents.push(record);
    if (record.kind === "agent_message" || record.kind === "agent_tool") showLiveReply("");
    renderMessageStream(eventsToChat(liveEvents, liveObjective));
    if (record.kind === "terminal_command" || record.kind === "terminal_output") renderTerminal();
    if (record.kind === "browser_started" || record.kind === "browser_result") renderBrowser();
    if (record.kind === 'plan_updated') void loadPlan(id);
    if (record.kind === 'task_tracker') renderChecklist();
    if (record.kind === "running" || isTerminal(record.kind)) {
      setState(record.kind);
      selectedState = record.kind;
      renderPlanner();
      void loadHistory();
      if (isTerminal(record.kind)) { chime(); void refreshFiles(); }
    }
    if (record.kind === "running") { el("working").hidden = false; el("working-label").textContent = "Working"; button("cancel").disabled = false; }
    else if (isTerminal(record.kind)) { el("working").hidden = true; button("cancel").disabled = true; }
    renderRunState();
  });
  source.addEventListener("delta", event => {
    if (id !== selected) return stopStream();
    showLiveReply((JSON.parse((event as MessageEvent).data) as { text: string }).text);
  });
  source.addEventListener("end", () => {
    stopStream();
    watching = true;
    void observe();  // pick up the final result, diff and history
  });
  source.onerror = () => { if (source.readyState === EventSource.CLOSED || liveTask !== id) { stopStream(); watching = true; } };
}

async function observe(force = false): Promise<void> {
  if (force) { viewEpoch++; watching = true; }
  if (polling) { observeQueued = true; return; }
  if (!selected || !watching || liveTask === selected) return;
  polling = true;
  const id = selected, epoch = viewEpoch;
  try {
    const task = await api<Task>("/api/tasks/" + id);
    if (id !== selected || epoch !== viewEpoch) return;
    liveEvents = [...task.events] as TaskEvent[];
    liveObjective = task.request.objective;
    liveSeq = liveEvents.reduce((max, event) => Math.max(max, event.seq ?? 0), 0);
    el("chat-title").textContent = conversationLabel({ ...task, objective: task.request.objective, title: lastTasks.find(item => item.id === id)?.title ?? "" });
    setTitle(el("chat-title").textContent ?? "");
    setState(task.state);
    selectedState = task.state;
    parentTask = typeof task.request.parent_task === 'string' ? task.request.parent_task : null;
    taskRequest = task.request;
    if (!observedTaskMode) {
      if (task.request.mode === 'plan') select('mode').value = 'plan';
      else if (select('mode').value === 'plan') select('mode').value = 'agent';
      observedTaskMode = task.request.mode || 'unknown'; syncMode(); syncComposer();
    }
    renderTerminal();
    renderBrowser();
    renderChecklist(); await loadPlan(id);
    if (id !== selected || epoch !== viewEpoch) return;
    button("cancel").disabled = isTerminal(task.state);
    const messages = eventsToChat(task.events as TaskEvent[], task.request.objective);
    lastMessages = messages;
    renderMessageStream(messages);
    el("working").hidden = isTerminal(task.state);
    el("working-label").textContent = task.state === "queued" ? "Queued" : "Working";
    renderRunState();
    renderReview(task.id, task.state, task.result);
    syncApplyBar();
    const known = lastTasks.find(item => item.id === id);
    if (isTerminal(task.state) || (known && known.state !== task.state)) await loadHistory();
    if (id !== selected || epoch !== viewEpoch) return;
    if (isTerminal(task.state)) watching = false;
    else startStream(id);
  } catch (error) {
    if (error instanceof Error) message(error.message, true);
  } finally { polling = false; if (observeQueued) { observeQueued = false; void observe(); } }
}

// --- sign-in and panels ---------------------------------------------------
const m4Create: M4Factory = (tag: string): M4Node => document.createElement(tag) as unknown as M4Node;
function mountPanel(containerId: string, panel: (dom: { root: M4Node; create: M4Factory; request: <T>(p: string, m: string, b?: unknown) => Promise<T> }) => { refresh(): Promise<void> }): () => Promise<void> {
  const container = document.getElementById(containerId);
  if (!container) throw new Error(`Missing M4 panel container: ${containerId}`);
  container.replaceChildren();
  return panel({ root: container as unknown as M4Node, create: m4Create, request: apiM4 }).refresh;
}
let refreshPanels: Record<string, () => Promise<void>> = {};

async function connect(): Promise<void> {
  await project(); await loadHistory();
  void loadModel().catch(() => undefined);
  void loadAgents().catch(() => undefined);
  el("boot").hidden = true;
  el("connect-view").hidden = true;
  el("workspace").hidden = false;
  if (!connected) {
    connected = true;
    refreshPanels = {
      models: modelsPane(el("panel-profiles"), paneDeps),
      secrets: keysPane(el("panel-secrets"), paneDeps),
      skills: mountPanel("panel-skills", skillsPanel),
    };
  }
  textarea("objective").focus?.();
  const linked = globalThis.location ? new URLSearchParams(globalThis.location.search).get("task") : null;
  if (linked && !selected && lastTasks.some(task => task.id === linked)) selectTask(linked);
  else await composerDrafts.select(selected ?? 'home');
  void observe();
  void loadAppSettings().catch(() => undefined);
  void loadExposedSecrets();
  routeSettings();
}
function showSignIn(): void {
  el("boot").hidden = true;
  el("workspace").hidden = true;
  el("connect-view").hidden = false;
}
async function startSession(value: string): Promise<boolean> {
  try { await request<{ signed_in: boolean }>("/api/session", "POST", { token: value }); return true; }
  catch (error) { message(error instanceof Error ? error.message : "Sign-in failed", true); return false; }
}
async function boot(): Promise<void> {
  const loc = globalThis.location;
  const match = loc ? /^#token=([\w-]+)$/.exec(loc.hash) : null;
  if (match && loc) {
    globalThis.history?.replaceState(null, "", loc.pathname + loc.search);  // keep the secret out of the address bar
    await startSession(match[1] ?? "");
  }
  try { await connect(); }
  catch (error) {
    if (error instanceof RequestError && error.status === 401) showSignIn();
    else { showSignIn(); message(error instanceof Error ? error.message : "Dikdik is not reachable", true); }
  }
}

el("connect-form").addEventListener("submit", async event => {
  event.preventDefault();
  const value = input("token").value.trim().replace(/^.*#token=/, "");
  token = value;
  try {
    await startSession(value);  // remembered by the browser from now on
    await connect();
    input("token").value = "";
    message("Signed in. This browser will stay signed in.");
  } catch (error) { token = ""; message(error instanceof Error ? error.message : "Connection failed", true); }
});

// --- layout: sidebar, canvas, settings ------------------------------------
function setSidebarCollapsed(collapsed: boolean): void {
  setClass(el("workspace"), "sidebar-collapsed", collapsed);
  el("show-sidebar").hidden = !collapsed;
  storageSet("dikdik.sidebar", collapsed ? "collapsed" : "open");
}
function closeSidebarDrawer(): void { setClass(el("workspace"), "sidebar-open", false); }
el("toggle-sidebar").addEventListener("click", () => {
  if (wide("(max-width: 760px)")) closeSidebarDrawer(); else setSidebarCollapsed(true);
});
el("show-sidebar").addEventListener("click", () => setSidebarCollapsed(false));
el("toggle-sidebar-mobile").addEventListener("click", () => setClass(el("workspace"), "sidebar-open", true));
el("scrim").addEventListener("click", closeSidebarDrawer);
if (storageGet("dikdik.sidebar") === "collapsed") setSidebarCollapsed(true);

el("toggle-canvas").addEventListener("click", () => {
  if (el("canvas").hidden === true || canvasTab !== "changes") openCanvas("changes"); else setCanvas(false);
});
el("open-files").addEventListener("click", () => {
  if (el("canvas").hidden === true || canvasTab === "changes") openCanvas("files"); else setCanvas(false);
});
el("apply").addEventListener("click", () => { void applyChanges(); });
el("apply-cancel").addEventListener("click", () => { applyStep = "idle"; syncApplyBar(); });
el("apply-commit").addEventListener("click", () => openCanvas("git"));
el("file-search").addEventListener("input", renderFileList);
el("copy-file").addEventListener("click", () => { void copyText(el("file-view").textContent ?? "", el("copy-file") as HTMLButtonElement); });
if (typeof el("canvas").querySelectorAll === "function") {
  for (const tab of el("canvas").querySelectorAll<HTMLElement>(".canvas-tab")) tab.addEventListener("click", () => openCanvas(tab.dataset.tab ?? "changes"));
  for (const node of el("file-version").querySelectorAll<HTMLElement>("button")) node.addEventListener("click", () => { if (openFile) void showFile(openFile, node.dataset.version ?? "workspace"); });
}
el("close-canvas").addEventListener("click", () => setCanvas(false));
el("copy-diff").addEventListener("click", () => { void copyText(renderedDiff, el("copy-diff") as HTMLButtonElement); });
el("new-task").addEventListener("click", newTask);
el("task-search").addEventListener("input", renderHistory);
el("show-archived").addEventListener("click", () => { showArchived = !showArchived; renderHistory(); });
el("task-actions").addEventListener("click", () => { if (selected) openTaskMenu(selected, el("task-actions")); });
if (typeof el("task-menu").querySelectorAll === "function") {
  for (const node of el("task-menu").querySelectorAll<HTMLButtonElement>("button")) node.addEventListener("click", () => { void runMenuAction(node.dataset.action ?? ""); });
}
if (typeof document.addEventListener === "function") {
  document.addEventListener("click", event => {
    const menu = el("task-menu");
    if (!menu.hidden && event.target instanceof Node && !menu.contains(event.target) && !(event.target as HTMLElement).closest?.(".conv-more, #task-actions")) closeTaskMenu();
  });
}
el("thread").addEventListener("scroll", () => {
  setClass(el("main"), "scrolled", el("thread").scrollTop > 4);
});

function applyTheme(theme: string): void {
  const root = globalThis.document?.documentElement;
  if (!root) return;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}
const themeSelect = el("theme") as HTMLSelectElement;
const savedTheme = storageGet("dikdik.theme") ?? "system";
applyTheme(savedTheme);
themeSelect.value = savedTheme;
themeSelect.addEventListener("change", () => {
  storageSet("dikdik.theme", themeSelect.value);
  applyTheme(themeSelect.value);
});

const PANE_TITLES: Record<string, string> = { general: "General", models: "Models", agents: "Agents", secrets: "API keys", skills: "Skills", mcp: "MCP servers", automations: "Automations" };

// --- automations ----------------------------------------------------------
let automations: Automation[] = [];
function syncAutoForm(): void {
  const daily = (el("auto-kind") as HTMLSelectElement).value === "daily";
  el("auto-daily").hidden = !daily;
  el("auto-minutes").hidden = daily;
  const agent = (el("auto-mode") as HTMLSelectElement).value === "agent";
  const picker = el("auto-profile") as HTMLSelectElement;
  picker.hidden = !agent || !agentProfiles.length;
  if (picker.options && picker.options.length !== agentProfiles.length + 1) {
    const first = make("option", "", "Default agent");
    first.value = "";
    picker.replaceChildren(first, ...agentProfiles.map(profile => { const option = make("option", "", profile.name); option.value = profile.id; return option; }));
  }
  el("auto-execution-field").hidden = (el("auto-verification") as HTMLSelectElement).value !== "docker";
}
function fillAutoForm(auto: Automation | null): void {
  const field = (id: string) => el(id) as HTMLInputElement;
  field("auto-id").value = auto?.id ?? "";
  field("auto-name").value = auto?.name ?? "";
  (el("auto-objective") as HTMLTextAreaElement).value = auto?.objective ?? "";
  const daily = !auto || "daily_at" in auto.schedule;
  (el("auto-kind") as HTMLSelectElement).value = daily ? "daily" : "interval";
  field("auto-daily").value = auto && "daily_at" in auto.schedule ? auto.schedule.daily_at : "07:00";
  field("auto-minutes").value = auto && "interval_seconds" in auto.schedule ? String(Math.round(auto.schedule.interval_seconds / 60)) : "60";
  (el("auto-mode") as HTMLSelectElement).value = auto?.run?.mode ?? "agent";
  (el("auto-verification") as HTMLSelectElement).value = auto?.run?.verification ?? "syntax";
  syncAutoForm();
  (el("auto-profile") as HTMLSelectElement).value = auto?.run?.agent_profile ?? "";
  field("auto-external").checked = auto?.run?.approve_external ?? false;
  field("auto-execution").checked = auto?.run?.approve_execution ?? false;
  el("auto-save").textContent = auto ? "Save changes" : "Add automation";
  el("auto-cancel").hidden = !auto;
}
async function loadAutomations(): Promise<void> {
  try {
    automations = parseAutomations(await api<unknown>("/api/automations"));
  } catch (error) { message(error instanceof Error ? error.message : "Could not load automations", true); return; }
  syncAutoForm();
  el("auto-list").replaceChildren(...automations.map(auto => {
    const row = make("li", "m4-row");
    const head = make("p", "m4-title");
    const state = auto.last_state ?? "";
    head.append(make("span", "m4-strong", auto.name));
    if (state) head.append(make("span", `auto-state state-${state}`, state === "needs_setup" ? "needs setup" : state));
    const next = typeof auto.next_run === "number" ? new Date(auto.next_run * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : "";
    const how = auto.run ? `${auto.run.mode === "agent" ? "Agent" : "Single proposal"} · ${auto.run.verification === "docker" ? "Docker tests" : "syntax check"}` : "Edit to choose a mode and approvals";
    row.append(head, make("span", "m4-meta", `${describeSchedule(auto.schedule)}${auto.enabled && next ? ` · next ${next}` : ""} · ${how}${auto.failures ? ` · ${auto.failures} failed in a row` : ""}`));
    row.append(make("p", "m4-objective", auto.objective));
    const actions = make("span", "m4-actions");
    const toggle = make("input");
    toggle.type = "checkbox"; toggle.checked = auto.enabled;
    toggle.setAttribute("aria-label", `Enable ${auto.name}`);
    toggle.onchange = async () => {
      try { await request(`/api/automations/${auto.id}`, "PATCH", { enabled: toggle.checked }); await loadAutomations(); }
      catch (error) { toggle.checked = !toggle.checked; message(error instanceof Error ? error.message : "Could not update", true); }
    };
    const enabled = make("label", "check");
    enabled.append(toggle, make("span", "", "On"));
    const runNow = make("button", "secondary", "Run now");
    runNow.type = "button";
    runNow.onclick = async () => {
      try {
        const started = await request<{ task_id: string }>(`/api/automations/${auto.id}/run`, "POST", {});
        (el("settings") as HTMLDialogElement).close?.();
        await loadHistory();
        selectTask(started.task_id); void observe();
      } catch (error) { message(error instanceof Error ? error.message : "Could not start a run", true); }
    };
    const edit = make("button", "secondary", "Edit");
    edit.type = "button";
    edit.onclick = () => fillAutoForm(auto);
    const runsButton = make("button", "secondary", "Runs");
    runsButton.type = "button";
    const runs = make("ol", "auto-runs");
    runsButton.onclick = async () => {
      if (runs.childElementCount) { runs.replaceChildren(); return; }
      try {
        const list = parseRunSummaries(await api<unknown>(`/api/automations/${auto.id}/runs`));
        runs.replaceChildren(...(list.length ? list.slice(0, 20).map(run => {
          const item = make("li");
          const open = make("button");
          open.type = "button";
          open.append(make("span", `auto-state state-${run.state}`, run.state), make("span", "", run.created ? new Date(run.created * 1000).toLocaleString() : run.id.slice(0, 8)));
          open.onclick = () => { (el("settings") as HTMLDialogElement).close?.(); selectTask(run.id); void observe(); };
          item.append(open);
          return item;
        }) : [make("li", "hint", "No runs yet.")]));
      } catch (error) { message(error instanceof Error ? error.message : "Could not load runs", true); }
    };
    const remove = make("button", "secondary", "Delete");
    remove.type = "button";
    remove.onclick = async () => {
      try { await request(`/api/automations/${auto.id}`, "DELETE"); await loadAutomations(); } catch (error) { message(error instanceof Error ? error.message : "Delete failed", true); }
    };
    actions.append(enabled, runNow, edit, runsButton, remove);
    row.append(actions, runs);
    return row;
  }));
  if (!automations.length) el("auto-list").replaceChildren(make("li", "hint", "No automations yet."));
}
for (const id of ["auto-kind", "auto-mode", "auto-verification"]) el(id).addEventListener("change", syncAutoForm);
el("auto-cancel").addEventListener("click", () => fillAutoForm(null));
el("auto-form").addEventListener("submit", async event => {
  event.preventDefault();
  const field = (id: string) => el(id) as HTMLInputElement;
  try {
    const body = buildAutomation({
      name: field("auto-name").value, objective: (el("auto-objective") as HTMLTextAreaElement).value,
      scheduleKind: (el("auto-kind") as HTMLSelectElement).value === "daily" ? "daily" : "interval",
      intervalMinutes: field("auto-minutes").value, dailyAt: field("auto-daily").value,
      mode: (el("auto-mode") as HTMLSelectElement).value, verification: (el("auto-verification") as HTMLSelectElement).value,
      approveExternal: field("auto-external").checked, approveExecution: field("auto-execution").checked,
      agentProfile: (el("auto-profile") as HTMLSelectElement).value,
    });
    const id = field("auto-id").value;
    await request(id ? `/api/automations/${id}` : "/api/automations", id ? "PATCH" : "POST", body);
    message(id ? "Automation saved." : "Automation added.");
    fillAutoForm(null);
    await loadAutomations();
  } catch (error) { message(error instanceof Error ? error.message : "Could not save the automation", true); }
});

// --- MCP servers ----------------------------------------------------------
interface McpServer { id: string; name: string; transport: "http" | "stdio"; url: string; command: string; secret_ref: string; env_var: string; enabled: boolean }
async function loadMcp(): Promise<void> {
  try {
    const servers = await api<McpServer[]>("/api/mcp");
    el("mcp-list").replaceChildren(...servers.map(server => {
      const row = make("li", "m4-row");
      const head = make("p", "m4-title");
      head.append(make("span", "m4-strong", server.name), make("span", "m4-badge", server.transport === "http" ? "remote" : "command"));
      const target = make("span", "m4-meta m4-mono", server.transport === "http" ? server.url : server.command);
      const result = make("p", "hint");
      const actions = make("span", "m4-actions");
      const toggle = make("input");
      toggle.type = "checkbox"; toggle.checked = server.enabled;
      toggle.setAttribute("aria-label", `Enable ${server.name}`);
      toggle.onchange = async () => {
        try { await request(`/api/mcp/${server.id}`, "PATCH", { enabled: toggle.checked }); message(toggle.checked ? `${server.name} enabled for agent tasks.` : `${server.name} disabled.`); }
        catch (error) { toggle.checked = !toggle.checked; message(error instanceof Error ? error.message : "Could not update", true); }
      };
      const enabled = make("label", "check");
      enabled.append(toggle, make("span", "", "Enabled"));
      const test = make("button", "secondary", "Test");
      test.type = "button";
      test.onclick = async () => {
        result.textContent = "Connecting…"; result.className = "hint";
        try {
          const info = await request<{ server: string; tools: { name: string }[] }>(`/api/mcp/${server.id}/test`, "POST", {});
          result.textContent = `Connected${info.server ? ` to ${info.server}` : ""}: ${info.tools.length} tool${info.tools.length === 1 ? "" : "s"} (${info.tools.map(t => t.name).join(", ") || "none"})`;
        } catch (error) { result.textContent = error instanceof Error ? error.message : "Connection failed"; result.className = "error"; }
      };
      const remove = make("button", "secondary", "Delete");
      remove.type = "button";
      remove.onclick = async () => {
        try { await request(`/api/mcp/${server.id}`, "DELETE"); await loadMcp(); } catch (error) { message(error instanceof Error ? error.message : "Delete failed", true); }
      };
      actions.append(enabled, test, remove);
      row.append(head, target, actions, result);
      return row;
    }));
    if (!servers.length) el("mcp-list").replaceChildren(make("li", "hint", "No MCP servers yet."));
  } catch (error) { message(error instanceof Error ? error.message : "Could not load MCP servers", true); }
}
function syncMcpForm(): void {
  const stdio = (el("mcp-transport") as HTMLSelectElement).value === "stdio";
  if (typeof el("mcp-form").querySelectorAll !== "function") return;
  for (const node of el("mcp-form").querySelectorAll<HTMLElement>(".mcp-http")) node.hidden = stdio;
  for (const node of el("mcp-form").querySelectorAll<HTMLElement>(".mcp-stdio")) node.hidden = !stdio;
}
el("mcp-transport").addEventListener("change", syncMcpForm);
el("mcp-form").addEventListener("submit", async event => {
  event.preventDefault();
  const value = (id: string) => (el(id) as HTMLInputElement).value.trim();
  const transport = value("mcp-transport");
  try {
    await request("/api/mcp", "POST", {
      name: value("mcp-name"), transport, ...(transport === "http" ? { url: value("mcp-url") } : { command: value("mcp-command"), env_var: value("mcp-env") || undefined }),
      secret_ref: value("mcp-secret") || undefined,
    });
    for (const id of ["mcp-name", "mcp-url", "mcp-command", "mcp-secret", "mcp-env"]) (el(id) as HTMLInputElement).value = "";
    message("Server added. Test it, then enable it for agent tasks.");
    await loadMcp();
  } catch (error) { message(error instanceof Error ? error.message : "Could not add the server", true); }
});
function showPane(name: string): void {
  if (name === "general") void loadBrowserSettings().catch(error => message(error instanceof Error ? error.message : "Could not load browser setting", true));
  const dialog = el("settings");
  for (const tab of dialog.querySelectorAll<HTMLElement>(".settings-tab")) tab.setAttribute("aria-selected", String(tab.dataset.pane === name));
  for (const pane of dialog.querySelectorAll<HTMLElement>(".settings-pane")) pane.hidden = pane.dataset.pane !== name;
  el("settings-pane-title").textContent = PANE_TITLES[name] ?? "Settings";
  settingsHash(name);
  if (name === "mcp") void loadMcp();
  if (name === "automations") { void loadAutomations(); void loadAgents().catch(() => undefined); }
  if (name === "agents") { void loadAgents().catch(() => undefined); void loadAgentModels().catch(() => undefined); }
  void refreshPanels[name]?.();
}
function openSettings(pane: string): void {
  const dialog = el("settings") as HTMLDialogElement;
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
  showPane(pane);
  closeSidebarDrawer();
  void loadAppSettings().catch(() => undefined);
}
el("open-settings").addEventListener("click", () => openSettings("general"));
el("model-pill").addEventListener("click", event => { event.stopPropagation(); void openModelMenu(); });
// Profiles or keys may have changed while settings were open.
el("settings").addEventListener("close", () => { settingsHash(null); });
el("settings").addEventListener("close", () => { void loadModel().catch(() => undefined); void loadAgents().catch(() => undefined); void ensureLists(true); });
el("skill-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await request("/api/skills", "POST", {
      name: (el("skill-name") as HTMLInputElement).value, scope: (el("skill-scope") as HTMLSelectElement).value,
      description: (el("skill-description") as HTMLInputElement).value, content: (el("skill-content") as HTMLTextAreaElement).value,
    });
    for (const id of ["skill-name", "skill-description", "skill-content"]) (el(id) as HTMLInputElement).value = "";
    message("Skill added. The agent will read it from the next task.");
    await refreshPanels.skills?.();
  } catch (error) { message(error instanceof Error ? error.message : "Could not add the skill", true); }
});
el("close-settings").addEventListener("click", () => (el("settings") as HTMLDialogElement).close());
if (typeof el("settings").querySelectorAll === "function") {
  for (const tab of el("settings").querySelectorAll<HTMLElement>(".settings-tab")) tab.addEventListener("click", () => showPane(tab.dataset.pane ?? "general"));
}

if (typeof document.addEventListener === "function") {
  document.addEventListener("keydown", (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && select('mode').value === 'plan' && !textarea('objective').value.trim()) { event.preventDefault(); void buildReviewedPlan(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); togglePalette(); }
    else if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "o") { event.preventDefault(); newTask(); }
    else if (event.key === "Escape" && !el("task-menu").hidden) closeTaskMenu();
    else if (event.key === "Escape" && !el("canvas").hidden && !(el("settings") as HTMLDialogElement).open) setCanvas(false);
  });
}

// --- composer / task submission -------------------------------------------
function autosize(): void {
  syncSend();
  const area = textarea("objective");
  if (!area.style) return;
  const maximum = composerMaximum(globalThis.innerHeight || 900, Math.max(0, (el('task-form').offsetHeight || 0) - (area.offsetHeight || 0)));
  area.style.maxHeight = `${maximum}px`;
  area.style.height = "auto";
  const height = composerSize.fit(area.value, area.scrollHeight, maximum);
  area.style.height = `${height}px`;
  el('composer-resize').setAttribute('aria-valuenow', String(Math.round(height)));
  el('composer-resize').setAttribute('aria-valuemax', String(Math.round(maximum)));
}
let composerDrag: { pointer: number; y: number; height: number } | null = null;
const composerGrip = el('composer-resize');
function resizeComposer(height: number): void {
  const area = textarea('objective');
  const maximum = composerMaximum(globalThis.innerHeight || 900, Math.max(0, el('task-form').offsetHeight - area.offsetHeight));
  composerSize.resize(height, maximum); autosize();
}
composerGrip.addEventListener('pointerdown', event => {
  if (!event.isPrimary || event.button !== 0 || textarea('objective').disabled) return;
  event.preventDefault();
  composerDrag = { pointer: event.pointerId, y: event.clientY, height: textarea('objective').getBoundingClientRect().height };
  composerGrip.setPointerCapture(event.pointerId); composerGrip.blur();
});
composerGrip.addEventListener('pointermove', event => {
  if (composerDrag?.pointer !== event.pointerId) return;
  resizeComposer(composerDrag.height + composerDrag.y - event.clientY);
});
function endComposerDrag(event: PointerEvent): void {
  if (composerDrag?.pointer !== event.pointerId) return;
  composerDrag = null;
  if (composerGrip.hasPointerCapture(event.pointerId)) composerGrip.releasePointerCapture(event.pointerId);
}
composerGrip.addEventListener('pointerup', endComposerDrag);
composerGrip.addEventListener('pointercancel', endComposerDrag);
composerGrip.addEventListener('lostpointercapture', () => { composerDrag = null; });
composerGrip.addEventListener('keydown', event => {
  if (textarea('objective').disabled) return;
  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    event.preventDefault(); resizeComposer(textarea('objective').getBoundingClientRect().height + (event.key === 'ArrowUp' ? 24 : -24));
  } else if (event.key === 'Home') { event.preventDefault(); composerSize.reset(); autosize(); }
});
composerGrip.addEventListener('dblclick', () => { composerSize.reset(); autosize(); });
globalThis.addEventListener?.('resize', autosize);
el('draft-conflict').addEventListener('toggle', autosize);
function setOptions(open: boolean): void {
  el("options-panel").hidden = !open;
  el("options-toggle").setAttribute("aria-expanded", String(open));
  autosize();
}
function syncMode(): void {
  const mode = parseMode(select("mode").value);
  const policy = modePolicy(mode, parseVerification(select("verification").value));
  (el("offline-fields") as HTMLElement).hidden = !policy.showEdits;
  textarea("edits").disabled = !policy.showEdits;
  (el("model-fields") as HTMLElement).hidden = !policy.requiresExternal;
  (el("model-hint") as HTMLElement).hidden = mode !== "model";
  (el("agent-hint") as HTMLElement).hidden = mode !== "agent";
  if (mode === 'plan') { el('agent-hint').hidden = false; el('agent-hint').textContent = 'The planner can inspect files and write a private PLAN.md for review. It cannot execute tools or modify repository files.'; }
  (el("external-field") as HTMLElement).hidden = !policy.requiresExternal;
  input("external").disabled = !policy.requiresExternal;
  input("external").required = policy.requiresExternal;
  select("verification").value = policy.verification;
  select("verification").disabled = policy.locksVerification;
  el("browser-field").hidden = mode !== "agent";
  input("browser-approval").disabled = mode !== "agent";
  const execution = policy.requiresExecution || (mode === "agent" && input("browser-approval").checked);
  el("secrets-field").hidden = !(mode === "agent" && execution && exposedSecrets > 0);
  input("secrets-approval").disabled = el("secrets-field").hidden === true;
  (el("execution-field") as HTMLElement).hidden = !execution;
  input("execution").disabled = !execution;
  input("execution").required = execution;
  el("execution-field").title = "Runs approved tools and tests inside restricted Docker containers.";
  el("model-pill").hidden = !policy.requiresExternal;
  syncModelBanner();
  el("agent-profile-pill").hidden = !['agent','plan'].includes(mode) || !agentProfiles.length;
  if (policy.showEdits) setOptions(true);
  syncComposer();
  autosize();
}
select("mode").addEventListener("change", syncMode);
select("verification").addEventListener("change", syncMode);
el("browser-approval").addEventListener("change", syncMode);
el("options-toggle").addEventListener("click", () => setOptions(el("options-panel").hidden === true));
syncMode();

textarea("objective").addEventListener("input", () => { autosize(); syncSend(); composerDrafts.update(textarea('objective').value); renderSlash(); });
textarea("objective").addEventListener("keydown", (event: KeyboardEvent) => {
  if (slashKey(event)) return;
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && select('mode').value === 'plan' && !textarea('objective').value.trim()) { event.preventDefault(); void buildReviewedPlan(); return; }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    const form = el("task-form") as HTMLFormElement;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  }
});
if (typeof document.querySelectorAll === "function") {
  for (const suggestion of document.querySelectorAll<HTMLButtonElement>(".suggestion")) {
    suggestion.addEventListener("click", () => {
      textarea("objective").value = suggestion.dataset.prompt ?? "";
      composerDrafts.update(textarea('objective').value);
      autosize();
      textarea("objective").focus();
    });
  }
}

function composerRequest(text: string, goalMaxTurns?: number) {
  return buildTaskRequest({
    modelProfile: modelOverride?.id, attachments: attached.map(file => file.id), goalMaxTurns,
    objective: text, mode: parseMode(select("mode").value),
    edits: textarea("edits").value, verification: parseVerification(select("verification").value),
    approveExecution: input("execution").checked, approveExternal: input("external").checked,
    approveBrowser: input("browser-approval").checked,
    agentProfile: (el("agent-profile") as HTMLSelectElement).value ?? "",
    approveSecrets: input("secrets-approval").checked,
  });
}

// --- agent profiles -------------------------------------------------------
async function loadAgents(): Promise<void> {
  agentProfiles = await api<AgentProfile[]>("/api/agent-profiles");
  const picker = el("agent-profile") as HTMLSelectElement;
  const current = picker.value;
  picker.replaceChildren(make("option", "", "Default agent"), ...agentProfiles.map(profile => {
    const option = make("option", "", profile.name);
    option.value = profile.id;
    return option;
  }));
  (picker.firstChild as HTMLOptionElement | null)?.setAttribute("value", "");
  picker.value = agentProfiles.some(profile => profile.id === current) ? current : "";
  el("agent-profile-pill").hidden = parseMode(select("mode").value) !== "agent" || !agentProfiles.length;
  renderAgentList();
}
function renderAgentList(): void {
  const list = el("agent-list");
  if (typeof list.replaceChildren !== "function") return;
  list.replaceChildren(...agentProfiles.map(profile => {
    const row = make("li", "m4-row");
    const head = make("p", "m4-title");
    head.append(make("span", "m4-strong", profile.name));
    const tools = [profile.allow_shell && "shell", profile.allow_browser && "browser", profile.use_mcp && "MCP", profile.use_skills && "skills"].filter(Boolean).join(", ") || "no extra tools";
    const limits = [profile.max_model_calls && `${profile.max_model_calls} model calls`, profile.deadline_seconds && `${profile.deadline_seconds}s`].filter(Boolean).join(" · ");
    row.append(head, make("span", "m4-meta", `${tools}${limits ? " · " + limits : ""}`));
    if (profile.instructions) row.append(make("p", "m4-objective", profile.instructions));
    const actions = make("span", "m4-actions");
    const edit = make("button", "secondary", "Edit");
    edit.type = "button";
    edit.onclick = () => fillAgentForm(profile);
    const remove = make("button", "secondary", "Delete");
    remove.type = "button";
    remove.onclick = async () => {
      try { await request(`/api/agent-profiles/${profile.id}`, "DELETE"); await loadAgents(); } catch (error) { message(error instanceof Error ? error.message : "Delete failed", true); }
    };
    actions.append(edit, remove);
    row.append(actions);
    return row;
  }));
  if (!agentProfiles.length) list.replaceChildren(make("li", "hint", "No agent profiles yet. The default agent uses your default model and every tool your approvals allow."));
}
function fillAgentForm(profile: AgentProfile | null): void {
  const field = (id: string) => el(id) as HTMLInputElement;
  field("agent-id").value = profile?.id ?? "";
  field("agent-name").value = profile?.name ?? "";
  (el("agent-instructions") as HTMLTextAreaElement).value = profile?.instructions ?? "";
  (el("agent-model") as HTMLSelectElement).value = profile?.model_profile_id ?? "";
  field("agent-steps").value = profile?.max_model_calls ? String(profile.max_model_calls) : "";
  field("agent-minutes").value = profile?.deadline_seconds ? String(profile.deadline_seconds) : "";
  field("agent-shell").checked = profile?.allow_shell ?? true;
  field("agent-browser").checked = profile?.allow_browser ?? true;
  field("agent-mcp").checked = profile?.use_mcp ?? true;
  field("agent-skills").checked = profile?.use_skills ?? true;
  field("agent-critic").checked = profile?.critic ?? false;
  field("agent-memory").checked = profile?.memory ?? false;
  field("agent-secrets").value = profile?.secret_refs == null ? "" : profile.secret_refs.length ? profile.secret_refs.join(", ") : "none";
  el("agent-save").textContent = profile ? "Save changes" : "Add agent";
  el("agent-cancel").hidden = !profile;
}
async function loadAgentModels(): Promise<void> {
  const models = await api<{ id: string; name: string; model: string }[]>("/api/profiles");
  const picker = el("agent-model") as HTMLSelectElement;
  const current = picker.value;
  const first = make("option", "", "Default model profile");
  first.value = "";
  picker.replaceChildren(first, ...models.map(model => { const option = make("option", "", `${model.name} (${model.model})`); option.value = model.id; return option; }));
  picker.value = current;
}
el("agent-cancel").addEventListener("click", () => fillAgentForm(null));
el("agent-form").addEventListener("submit", async event => {
  event.preventDefault();
  const field = (id: string) => el(id) as HTMLInputElement;
  const number = (id: string) => field(id).value.trim() ? Number(field(id).value) : null;
  const body = {
    name: field("agent-name").value, instructions: (el("agent-instructions") as HTMLTextAreaElement).value,
    model_profile_id: (el("agent-model") as HTMLSelectElement).value || null,
    max_model_calls: number("agent-steps"), deadline_seconds: number("agent-minutes"),
    allow_shell: field("agent-shell").checked, allow_browser: field("agent-browser").checked, use_mcp: field("agent-mcp").checked, use_skills: field("agent-skills").checked,
    critic: field("agent-critic").checked, memory: field("agent-memory").checked, secret_refs: secretRefsField(field("agent-secrets").value),
  };
  const id = field("agent-id").value;
  try {
    await request(id ? `/api/agent-profiles/${id}` : "/api/agent-profiles", id ? "PATCH" : "POST", body);
    message(id ? "Agent profile saved." : "Agent profile added. Pick it in the composer.");
    fillAgentForm(null);
    await loadAgents();
  } catch (error) { message(error instanceof Error ? error.message : "Could not save the agent profile", true); }
});
/** A follow-up on the open task; queued by the server if that task is still running. */
async function sendMessage(text: string, goalMaxTurns?: number): Promise<boolean> {
  if (!selected) return false;
  const task = selected;
  try {
    const reply = await api<{ queued: boolean }>(`/api/tasks/${encodeURIComponent(task)}/messages`, composerRequest(text, goalMaxTurns));
    clearAttachments();
    if (task === selected) watching = true;
    if (reply.queued) message("Queued. Dikdik will reply when the current step finishes.");
  } catch (error) { message(error instanceof Error ? error.message : "Message failed", true); return false; }
  try { await loadHistory(); if (task === selected) await observe(true); }
  catch { message('Message sent. Refresh to load the latest conversation.', true); }
  return true;
}

el("task-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || (!textarea('objective').value.trim() && !attached.length)) return;
  hideSlash();
  submitting = true; syncSend(); textarea('objective').disabled = true;
  const originalScope = composerDrafts.scope, originalText = textarea('objective').value;
  try {
    let text = textarea("objective").value;
    let goalTurns: number | undefined;
    const slash = parseSlash(text);
    const command = slash && (slash.name === "plan" || slash.name === "code") ? slash : null;
    if (command) {
      if (command.name === 'plan') await enterPlanning(); else await enterCode();
      text = command.arg; textarea('objective').value = text;
      if (!text) { await composerDrafts.sent(originalScope, originalText); return; }
    } else if (slash) {
      const done = await runSlash(slash.name, slash.arg);
      if (done === true) { await composerDrafts.sent(originalScope, originalText); return; }
      if (done === false) return;  // the command explained what is missing; keep the text
      ({ text, turns: goalTurns } = done);
    }
    if (!text.trim() && attached.length) text = attached.length === 1 ? `Take a look at the attached file “${attached[0]?.name ?? "file"}”.` : "Take a look at the attached files.";
    if (selected && select('mode').value === 'plan' && observedTaskMode !== 'plan' && command?.name !== 'plan') await enterPlanning();
    if (selected) {
      composerRequest(text, goalTurns);  // validate before clearing the field
      if (await sendMessage(text, goalTurns)) await composerDrafts.sent(originalScope, originalText);
      return;
    }
    const task = await api<TaskSummary>("/api/tasks", composerRequest(text, goalTurns));
    clearAttachments();
    await composerDrafts.sent(originalScope, originalText);
    selectTask(task.id);
    await loadHistory(); await observe();
  } catch (error) { message(error instanceof Error ? error.message : "Task failed", true); }
  finally { submitting = false; textarea('objective').disabled = false; composerDrafts.display(); autosize(); syncSend(); }
});

// --- run control: slash commands, attachments, pause/resume, goals, forks ------
async function ensureLists(force = false): Promise<void> {
  if (!force && (modelProfiles.length || skillNames.length)) return;
  const [models, skills] = await Promise.all([
    api<{ id: string; name: string; model: string }[]>("/api/profiles").catch(() => []),
    request<unknown>("/api/skills", "GET").catch(() => []),
  ]);
  modelProfiles = Array.isArray(models) ? models : [];
  skillNames = (Array.isArray(skills) ? skills : []).flatMap(item => {
    const skill = item as { name?: unknown; enabled?: unknown };
    return typeof skill.name === "string" && skill.enabled !== false && /^[a-z0-9_-]{1,64}$/i.test(skill.name) ? [skill.name] : [];
  });
}
function modelMatches(value: string): SlashCommand[] {
  const typed = (/^\/model\s+(.*)$/i.exec(value)?.[1] ?? "").toLowerCase();
  return [{ name: "default", hint: "", description: "Your default model profile" },
    ...modelProfiles.map(profile => ({ name: profile.name, hint: profile.model, description: "" }))]
    .filter(item => item.name.toLowerCase().startsWith(typed)).slice(0, 12);
}
function hideSlash(): void { el("slash-menu").hidden = true; slashItems = []; }
function renderSlash(): void {
  const value = textarea("objective").value;
  if (!value.startsWith("/")) { hideSlash(); return; }
  void ensureLists().then(() => { if (textarea("objective").value === value) drawSlash(value); });
  drawSlash(value);
}
function drawSlash(value: string): void {
  slashModels = /^\/model\s/i.test(value) && !/\n/.test(value);
  slashItems = slashModels ? modelMatches(value) : slashMatches(value, skillNames);
  const menu = el("slash-menu");
  if (!slashItems.length) { hideSlash(); return; }
  slashIndex = Math.min(slashIndex, slashItems.length - 1);
  menu.replaceChildren(...slashItems.map((item, index) => {
    const row = make("li", "slash-item");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === slashIndex));
    row.append(make("span", "slash-name", slashModels ? item.name : `/${item.name}`));
    if (item.hint) row.append(make("span", "slash-hint", item.hint));
    if (item.description) row.append(make("span", "slash-desc", item.description));
    row.addEventListener("mousedown", event => { event.preventDefault(); chooseSlash(index); });
    return row;
  }));
  menu.hidden = false;
}
function chooseSlash(index: number): void {
  const item = slashItems[index];
  if (!item) return;
  const field = textarea("objective");
  hideSlash();
  if (slashModels || ["condense", "fork"].includes(item.name)) {
    field.value = slashModels ? `/model ${item.name}` : `/${item.name}`;
    const form = el("task-form") as HTMLFormElement;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    return;
  }
  field.value = `/${item.name} `;
  composerDrafts.update(field.value);
  field.focus?.();
  syncSend();
  if (item.name === "model") renderSlash();
}
/** Arrow keys, Enter/Tab and Escape drive the open command menu. */
function slashKey(event: KeyboardEvent): boolean {
  if (el("slash-menu").hidden || !slashItems.length) return false;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    slashIndex = (slashIndex + (event.key === "ArrowDown" ? 1 : slashItems.length - 1)) % slashItems.length;
    drawSlash(textarea("objective").value);
  } else if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") chooseSlash(slashIndex);
  else if (event.key === "Escape") hideSlash();
  else return false;
  event.preventDefault();
  return true;
}
/** Runs a composer command. true: done, clear the field; false: keep the text; otherwise send this text. */
async function runSlash(name: string, arg: string): Promise<boolean | { text: string; turns?: number }> {
  if (name === "btw") {
    if (!arg) { message("Ask your question after /btw.", true); return false; }
    await askSide(arg);
    return true;
  }
  if (name === "goal") {
    const goal = parseGoal(arg);
    if (select("mode").value !== "agent") { select("mode").value = "agent"; syncMode(); }
    return { text: goal.goal, turns: goal.turns };
  }
  if (name === "model") {
    await ensureLists(true);
    if (!arg) { textarea("objective").value = "/model "; drawSlash("/model "); return false; }
    if (arg.toLowerCase() === "default") modelOverride = null;
    else {
      const wanted = arg.toLowerCase();
      const found = modelProfiles.find(profile => profile.name.toLowerCase() === wanted) ?? modelProfiles.find(profile => profile.name.toLowerCase().startsWith(wanted));
      if (!found) { message(`No model profile is called “${arg}”. Add one in Settings → Models.`, true); return false; }
      modelOverride = { id: found.id, name: found.name };
    }
    message(modelOverride ? `Using ${modelOverride.name} for your next messages in this tab.` : "Back to your default model.");
    await loadModel().catch(() => undefined);
    return true;
  }
  if (name === "condense") { await condenseNow(); return true; }
  if (name === "fork") {
    if (!selected) { message("Open a conversation to fork it.", true); return false; }
    await forkTask(selected);
    return true;
  }
  if (skillNames.includes(name)) return { text: `Use the “${name}” skill for this request.${arg ? "\n\n" + arg : ""}` };
  message(`Unknown command /${name}. Type / to see the commands.`, true);
  return false;
}
function needsSharing(what: string): void {
  if (!selected) throw new Error(`Open a conversation first; ${what} works from its history.`);
  if (!input("external").checked) throw new Error(`Turn on “Share code with model” to ${what}.`);
}
async function askSide(question: string): Promise<void> {
  needsSharing("ask a side question");
  const task = selected!;
  el("side-answer").hidden = false;
  el("side-question").textContent = question;
  el("side-body").replaceChildren(make("p", "hint", "Thinking…"));
  try {
    const reply = await api<{ answer: string }>(`/api/tasks/${encodeURIComponent(task)}/ask`, { question, approve_external: true });
    if (task === selected) el("side-body").replaceChildren(richText(reply.answer));
  } catch (error) { el("side-answer").hidden = true; throw error; }
}
async function condenseNow(): Promise<void> {
  needsSharing("condense the conversation");
  const task = selected!;
  message("Condensing older turns…");
  await api(`/api/tasks/${encodeURIComponent(task)}/condense`, { approve_external: true });
  message("Condensed. Older turns now travel as a summary.");
  if (task === selected) await observe(true);
}
async function forkTask(task: string, upto?: number): Promise<void> {
  const reply = await api<{ id: string }>(`/api/tasks/${encodeURIComponent(task)}/fork`, upto === undefined ? {} : { upto_seq: upto });
  await loadHistory();
  selectTask(reply.id);
  await observe();
  message("Forked into a new conversation. The original is unchanged.");
}
async function resumeTask(): Promise<void> {
  if (!selected || !taskRequest) return;
  const r = taskRequest, task = selected;
  const chosen = modelOverride?.id ?? r.model_profile;
  const body = {
    verification: r.verification, approve_external: r.approve_external === true, approve_execution: r.approve_execution === true,
    ...(r.approve_browser ? { approve_browser: true } : {}), ...(r.agent_profile ? { agent_profile: r.agent_profile } : {}),
    ...(r.confirmation ? { confirmation: r.confirmation } : {}), ...(chosen ? { model_profile: chosen } : {}),
  };
  try {
    await api(`/api/tasks/${encodeURIComponent(task)}/resume`, body);
    watching = true;
    await observe(true);
  } catch (error) {
    const text = error instanceof Error ? error.message : "Resume failed";
    if (/no saved agent state/i.test(text)) { await sendMessage("Continue where you left off. Finish the original request."); return; }
    message(text, true);
  }
}
const GOAL_LABELS: Record<string, string> = { achieved: "Goal achieved", capped: "Goal stopped at its turn limit", stopped: "Goal stopped", error: "Goal check failed" };
function renderRunState(): void {
  const usage = selected ? contextUsage(liveEvents) : null;
  const meter = el("context-meter");
  meter.hidden = !usage;
  if (usage) {
    const pct = Math.round(usage.ratio * 100);
    el("context-label").textContent = `${pct}%`;
    el("context-fill").style?.setProperty?.("--fill", `${pct}%`);
    setClass(meter, "context-high", usage.ratio >= 0.8);
    meter.title = `${pct}% of the context window at the last model call (${Math.round(usage.bytes / 1024)} of ${Math.round(usage.limit / 1024)} KB). Click to condense older turns.`;
  }
  const goal = selected ? goalState(liveEvents) : null;
  el("goal-banner").hidden = !goal;
  if (goal) {
    const label = goal.status === "active" ? `Goal · turn ${Math.min(goal.turns + 1, goal.maxTurns)} of ${goal.maxTurns}` : `${GOAL_LABELS[goal.status] ?? "Goal"} · ${goal.turns}/${goal.maxTurns}`;
    el("goal-text").textContent = `${label}: ${goal.objective}`;
    el("goal-text").title = goal.reason;
    el("goal-stop").hidden = goal.status !== "active";
    el("goal-resume").hidden = goal.status === "active" || goal.status === "achieved";
  }
  el("pause").hidden = !selected || !selectedState || isTerminal(selectedState) || taskRequest?.mode !== "agent";
}
async function readBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
const TEXT_TYPES: Record<string, string> = { md: "text/markdown", py: "text/x-python", ts: "text/plain", tsx: "text/plain", rs: "text/plain", toml: "text/plain", yml: "text/plain", yaml: "text/plain", log: "text/plain", sh: "text/plain", sql: "text/plain", go: "text/plain" };
async function uploadFiles(files: File[]): Promise<void> {
  for (const file of files) {
    if (attached.length >= 10) { message("Attach up to 10 files per message.", true); break; }
    if (file.size > 3 * 1024 * 1024) { message(`${file.name} is larger than 3 MB.`, true); continue; }
    const extension = (file.name.split(".").pop() ?? "").toLowerCase();
    const type = file.type || TEXT_TYPES[extension] || "text/plain";
    try {
      const data = await readBase64(file);
      const meta = await api<AttachmentMeta>("/api/attachments", { name: file.name || "pasted.png", type, data });
      attached.push({ ...meta, preview: meta.type.startsWith("image/") ? `data:${meta.type};base64,${data}` : "" });
    } catch (error) { message(error instanceof Error ? `${file.name}: ${error.message}` : "Upload failed", true); }
  }
  renderTray(); syncSend(); autosize();
}
function renderTray(): void {
  const tray = el("attachment-tray");
  tray.hidden = !attached.length;
  tray.replaceChildren(...attached.map(file => {
    const chip = make("span", file.preview ? "tray-item tray-image" : "tray-item");
    if (file.preview) { const image = make("img"); image.src = file.preview; image.alt = ""; chip.append(image); }
    else chip.append(icon("file"));
    chip.append(make("span", "tray-name", file.name));
    const remove = make("button", "ghost icon tray-remove"); remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${file.name}`); remove.title = "Remove";
    remove.append(icon("x"));
    remove.onclick = () => { attached = attached.filter(item => item.id !== file.id); renderTray(); syncSend(); autosize(); };
    chip.append(remove);
    return chip;
  }));
}
function clearAttachments(): void { attached = []; renderTray(); syncSend(); }
el("attach").addEventListener("click", () => input("attach-input").click());
input("attach-input").addEventListener("change", () => {
  const files = [...(input("attach-input").files ?? [])];
  input("attach-input").value = "";
  void uploadFiles(files);
});
textarea("objective").addEventListener("paste", (event: ClipboardEvent) => {
  const files = [...(event.clipboardData?.files ?? [])];
  if (!files.length) return;
  event.preventDefault();
  void uploadFiles(files);
});
textarea("objective").addEventListener("blur", () => setTimeout(hideSlash, 120));
el("task-form").addEventListener("dragover", (event: DragEvent) => {
  if (!event.dataTransfer?.types?.includes("Files")) return;
  event.preventDefault();
  setClass(el("task-form"), "dropping", true);
});
el("task-form").addEventListener("dragleave", () => setClass(el("task-form"), "dropping", false));
el("task-form").addEventListener("drop", (event: DragEvent) => {
  setClass(el("task-form"), "dropping", false);
  const files = [...(event.dataTransfer?.files ?? [])];
  if (!files.length) return;
  event.preventDefault();
  void uploadFiles(files);
});
el("context-meter").addEventListener("click", () => { void condenseNow().catch(error => message(error instanceof Error ? error.message : "Condense failed", true)); });
el("side-close").addEventListener("click", () => { el("side-answer").hidden = true; });
button("pause").onclick = async () => {
  if (!selected) return;
  try {
    const data = await api<{ requested: boolean }>(`/api/tasks/${encodeURIComponent(selected)}/pause`, {});
    message(data.requested ? "Pausing after the current step…" : "This run has already stopped.");
  } catch (error) { message(error instanceof Error ? error.message : "Pause failed", true); }
};
for (const [id, action] of [["goal-stop", "stop"], ["goal-resume", "resume"]] as const) {
  el(id).addEventListener("click", async () => {
    if (!selected) return;
    try { await api(`/api/tasks/${encodeURIComponent(selected)}/goal/${action}`, {}); await observe(true); }
    catch (error) { message(error instanceof Error ? error.message : "Goal update failed", true); }
  });
}
// General settings: when to ask before tools run, and the automatic condenser.
async function loadAppSettings(): Promise<void> {
  const settings = await api<NonNullable<typeof appSettings>>("/api/settings/app");
  appSettings = settings;
  select("confirmation-policy").value = settings.confirmation;
  input("condenser").checked = settings.condenser;
  input("sounds").checked = settings.sounds === true;
  input("git-name").value = settings.git_name ?? "";
  input("git-email").value = settings.git_email ?? "";
}
async function saveAppSetting(change: Record<string, unknown>): Promise<void> {
  try { appSettings = await request<NonNullable<typeof appSettings>>("/api/settings/app", "PATCH", change); message("Saved."); }
  catch (error) { message(error instanceof Error ? error.message : "Could not save", true); void loadAppSettings().catch(() => undefined); }
}
select("confirmation-policy").addEventListener("change", () => { void saveAppSetting({ confirmation: select("confirmation-policy").value }); });
input("condenser").addEventListener("change", () => { void saveAppSetting({ condenser: input("condenser").checked }); });

// --- files: tree, tabs, Markdown preview, open from chat, resizable panel -------
function treeRows(paths: string[], changed: Set<string>): HTMLElement[] {
  return fileTree(paths, expandedDirs).map(row => {
    const item = make("button", row.dir ? "tree-row tree-dir" : "tree-row");
    item.type = "button";
    item.title = row.path;
    item.style?.setProperty?.("--depth", String(row.depth));
    item.setAttribute("role", "treeitem");
    if (row.dir) {
      item.setAttribute("aria-expanded", String(row.open));
      const chevron = icon("chevron"); chevron.setAttribute("class", "i tree-chevron");
      item.append(chevron, icon("folder"), make("span", "tree-name", row.name));
      item.onclick = () => { if (expandedDirs.has(row.path)) expandedDirs.delete(row.path); else expandedDirs.add(row.path); renderFileList(); };
    } else {
      item.setAttribute("aria-selected", String(row.path === openFile));
      item.append(icon("file"), make("span", "tree-name", row.name));
      if (changed.has(row.path)) item.dataset.changed = "true";
      item.onclick = () => { void showFile(row.path, changed.has(row.path) && selected ? "candidate" : "workspace"); };
    }
    return item;
  });
}
function renderFileTabs(): void {
  const strip = el("file-tabs");
  strip.hidden = !openTabs.length;
  strip.replaceChildren(...openTabs.map(path => {
    const tab = make("span", "file-tab");
    tab.setAttribute("aria-selected", String(path === openFile));
    const open = make("button", "file-tab-name", path.split("/").pop() ?? path); open.type = "button"; open.title = path;
    open.setAttribute("role", "tab");
    open.onclick = () => { void showFile(path, changedPaths().has(path) && selected ? "candidate" : "workspace"); };
    const close = make("button", "ghost icon file-tab-close"); close.type = "button";
    close.setAttribute("aria-label", `Close ${path}`); close.append(icon("x"));
    close.onclick = () => {
      openTabs = openTabs.filter(item => item !== path);
      if (path === openFile) {
        const next = openTabs[openTabs.length - 1];
        if (next) void showFile(next, changedPaths().has(next) && selected ? "candidate" : "workspace");
        else { openFile = null; fileText = ""; el("file-name").textContent = "Choose a file"; el("file-view").replaceChildren(); el("copy-file").hidden = true; el("file-preview").hidden = true; filePreview = false; showFilePreview(); renderFileList(); }
      }
      renderFileTabs();
    };
    tab.append(open, close);
    return tab;
  }));
}
function showFilePreview(): void {
  el("file-view").hidden = filePreview;
  el("file-rendered").hidden = !filePreview;
  el("file-preview").setAttribute("aria-pressed", String(filePreview));
  if (filePreview) el("file-rendered").replaceChildren(richText(fileText));
}
el("file-preview").addEventListener("click", () => { filePreview = !filePreview; showFilePreview(); });
async function openFileFromChat(path: string, line?: number): Promise<void> {
  openCanvas("files");
  await showFile(path, changedPaths().has(path) && selected ? "candidate" : "workspace");
  if (!line) return;
  for (const span of el("file-view").querySelectorAll<HTMLElement>(".ln")) {
    const hit = span.dataset.n === String(line);
    setClass(span, "ln-hit", hit);
    if (hit) span.scrollIntoView?.({ block: "center" });
  }
}
/** After a run ends the workspace may have changed (Apply); refresh the inventory and the open file. */
async function refreshFiles(): Promise<void> {
  await project().catch(() => undefined);
  if (canvasTab !== "files" || el("canvas").hidden) return;
  renderFileList();
  if (openFile) await showFile(openFile, fileVersion);
}
const canvasGrip = el("canvas-resize");
let canvasDrag: number | null = null;
function setCanvasWidth(width: number | null): void {
  const canvas = el("canvas");
  if (width === null) { canvas.style?.removeProperty?.("width"); storageSet("dikdik.canvas.width", ""); return; }
  const clamped = Math.round(Math.max(320, Math.min(width, (globalThis.innerWidth || 1440) * 0.72)));
  canvas.style?.setProperty?.("width", `${clamped}px`);
  storageSet("dikdik.canvas.width", String(clamped));
}
{
  const saved = Number(storageGet("dikdik.canvas.width"));
  if (saved > 0) setCanvasWidth(saved);
}
canvasGrip.addEventListener("pointerdown", (event: PointerEvent) => { canvasDrag = event.pointerId; canvasGrip.setPointerCapture?.(event.pointerId); event.preventDefault(); });
canvasGrip.addEventListener("pointermove", (event: PointerEvent) => { if (canvasDrag === event.pointerId) setCanvasWidth((globalThis.innerWidth || 1440) - event.clientX); });
for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) canvasGrip.addEventListener(name, () => { canvasDrag = null; });
canvasGrip.addEventListener("keydown", (event: KeyboardEvent) => {
  const width = el("canvas").getBoundingClientRect?.().width ?? 560;
  if (event.key === "ArrowLeft") setCanvasWidth(width + 32);
  else if (event.key === "ArrowRight") setCanvasWidth(width - 32);
  else if (event.key === "Home") setCanvasWidth(null);
  else return;
  event.preventDefault();
});
canvasGrip.addEventListener("dblclick", () => setCanvasWidth(null));

// --- command menu (Ctrl+K) ---------------------------------------------------------
interface Command { label: string; keywords?: string; hint?: string; icon: string; run: () => void }
const PANEL_TITLES: Record<string, string> = { changes: "Changes", files: "Files", terminal: "Terminal", browser: "Browser", planner: "Planner", git: "Git" };
let paletteItems: Command[] = [], paletteIndex = 0;
function paletteCommands(): Command[] {
  const task = selected;
  const actions: Command[] = [{ label: "New task", hint: "Ctrl ⇧ O", icon: "plus", keywords: "conversation start", run: newTask }];
  if (task) {
    actions.push(
      { label: "Rename this conversation", icon: "pencil", run: () => startRename(task) },
      { label: "Folder and tags…", icon: "tag", keywords: "organise move label", run: () => openMeta(task) },
      { label: "Fork this conversation", icon: "branch", keywords: "copy branch", run: () => { void forkTask(task).catch(error => message(String(error), true)); } },
      { label: "Condense the conversation", icon: "list", keywords: "summarise compact context", run: () => { void condenseNow().catch(error => message(error instanceof Error ? error.message : String(error), true)); } },
      { label: "Export transcript (Markdown)", icon: "download", run: () => { void exportTask(task); } },
      { label: "Download conversation data (.zip)", icon: "download", run: () => { void downloadTask(task); } },
    );
    if (!el("pause").hidden) actions.push({ label: "Pause after this step", icon: "pause", run: () => button("pause").click() });
  }
  actions.push(
    { label: "Skills, hooks and tools", icon: "info", keywords: "agent capabilities subagents", run: () => { void showAgentInfo(); } },
    ...Object.entries(PANEL_TITLES).map(([tab, title]) => ({ label: `Open ${title} panel`, icon: "sidebar", keywords: "canvas tab", run: () => openCanvas(tab) })),
    ...Object.entries(PANE_TITLES).map(([pane, title]) => ({ label: `Settings: ${title}`, icon: "gear", keywords: "preferences options configure", run: () => openSettings(pane) })),
    { label: el("workspace").className.includes("sidebar-collapsed") ? "Show sidebar" : "Hide sidebar", icon: "sidebar", run: () => button("toggle-sidebar").click() },
    { label: showArchived ? "Hide archived conversations" : "Show archived conversations", icon: "archive", run: () => button("show-archived").click() },
    ...["system", "light", "dark"].map(theme => ({ label: `Appearance: ${theme[0]!.toUpperCase()}${theme.slice(1)}`, icon: "sun", keywords: "theme dark mode", run: () => { themeSelect.value = theme; themeSelect.dispatchEvent(new Event("change")); } })),
  );
  const conversations = lastTasks.filter(item => item.id !== task).map(item => ({
    label: conversationLabel(item), keywords: `${item.folder ?? ""} ${(item.tags ?? []).join(" ")} conversation`,
    hint: item.folder || (item.archived ? "Archived" : ""), icon: "list",
    run: () => { selectTask(item.id); void observe(); },
  }));
  return [...actions, ...conversations];
}
function renderPalette(): void {
  const query = input("palette-input").value;
  const all = paletteCommands();
  paletteItems = query.trim() ? rankCommands(query, all) : all.slice(0, 40);
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
  const list = el("palette-list");
  list.replaceChildren(...paletteItems.map((item, index) => {
    const row = make("li", "palette-item");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === paletteIndex));
    row.append(icon(item.icon), make("span", "palette-label", item.label));
    if (item.hint) row.append(make("span", "palette-hint", item.hint));
    row.addEventListener("mousedown", event => { event.preventDefault(); runPalette(index); });
    return row;
  }));
  if (!paletteItems.length) list.replaceChildren(make("li", "palette-empty", "Nothing matches"));
  list.children[paletteIndex]?.scrollIntoView?.({ block: "nearest" });
}
function runPalette(index: number): void {
  const item = paletteItems[index];
  if (!item) return;
  (el("palette") as HTMLDialogElement).close();
  item.run();
}
function togglePalette(): void {
  const dialog = el("palette") as HTMLDialogElement;
  if (dialog.open) { dialog.close(); return; }
  if (typeof dialog.showModal !== "function") return;
  input("palette-input").value = "";
  paletteIndex = 0;
  renderPalette();
  dialog.showModal();
  input("palette-input").focus();
}
input("palette-input").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
input("palette-input").addEventListener("keydown", (event: KeyboardEvent) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!paletteItems.length) return;
    paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : paletteItems.length - 1)) % paletteItems.length;
    renderPalette();
  } else if (event.key === "Enter") { event.preventDefault(); runPalette(paletteIndex); }
});
el("palette").addEventListener("click", event => { if (event.target === el("palette")) (el("palette") as HTMLDialogElement).close(); });
el("open-palette").addEventListener("click", togglePalette);

// --- folders, tags, archive clean-up --------------------------------------------------
let metaTask: string | null = null;
function openMeta(task: string): void {
  const info = lastTasks.find(item => item.id === task);
  const dialog = el("meta-dialog") as HTMLDialogElement;
  metaTask = task;
  input("meta-folder").value = info?.folder ?? "";
  input("meta-tags").value = (info?.tags ?? []).join(", ");
  el("meta-error").hidden = true;
  el("folder-options").replaceChildren(...folderOptions.map(name => { const option = make("option"); option.value = name; return option; }));
  el("meta-title").textContent = `Folder and tags · ${info ? conversationLabel(info) : "conversation"}`;
  if (typeof dialog.showModal === "function") dialog.showModal();
  input("meta-folder").focus();
}
el("meta-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!metaTask) return;
  try {
    const tags = parseTags(input("meta-tags").value);
    await patchTask(metaTask, { folder: input("meta-folder").value.trim() || null, tags });
    (el("meta-dialog") as HTMLDialogElement).close();
    message("Saved.");
  } catch (error) {
    el("meta-error").textContent = error instanceof Error ? error.message : "Could not save";
    el("meta-error").hidden = false;
  }
});
el("meta-cancel").addEventListener("click", () => (el("meta-dialog") as HTMLDialogElement).close());
select("task-sort").addEventListener("change", () => { storageSet("dikdik.list.sort", select("task-sort").value); renderHistory(); });
select("task-since").addEventListener("change", () => { storageSet("dikdik.list.since", select("task-since").value); renderHistory(); });
select("task-sort").value = storageGet("dikdik.list.sort") || "recent";
select("task-since").value = storageGet("dikdik.list.since") || "0";
el("delete-archived").addEventListener("click", async () => {
  const archived = lastTasks.filter(task => task.archived && task.id !== selected);
  if (!deletingArchived) {
    deletingArchived = true;
    el("delete-archived").textContent = `Click again to delete ${archived.length} for good`;
    setTimeout(() => { deletingArchived = false; renderHistory(); }, 4000);
    return;
  }
  deletingArchived = false;
  let failed = 0;
  for (const task of archived) {
    try { await request(`/api/tasks/${encodeURIComponent(task.id)}`, "DELETE"); } catch { failed++; }
  }
  await loadHistory();
  message(failed ? `Deleted ${archived.length - failed}; ${failed} could not be deleted.` : `Deleted ${archived.length} archived conversations.`, failed > 0);
});

// --- conversation data, agent info ------------------------------------------------------
async function downloadTask(task: string): Promise<void> {
  const headers: Record<string, string> = token ? { Authorization: "Bearer " + token } : {};
  const response = await fetch(`/api/tasks/${encodeURIComponent(task)}/download`, { headers, credentials: "same-origin" });
  if (!response.ok) { message("Could not download this conversation", true); return; }
  const url = URL.createObjectURL(await response.blob());
  const info = lastTasks.find(item => item.id === task);
  const link = make("a");
  link.href = url;
  link.download = `dikdik-${(info ? conversationLabel(info) : "conversation").replace(/[^\w-]+/g, "-").slice(0, 40)}-${task.slice(0, 8)}.zip`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
interface AgentInfo {
  tools: { name: string; description: string }[];
  skills: { name: string; description: string; scope: string; enabled: boolean }[];
  subagents: { name: string; description: string; source: string }[];
  hooks: { tool: string; pattern: string; message: string }[];
}
async function showAgentInfo(): Promise<void> {
  const info = await api<AgentInfo>("/api/agent-info");
  const section = (title: string, rows: [string, string][], empty: string) => {
    const box = make("section", "info-section");
    box.append(make("h4", "", title));
    if (!rows.length) box.append(make("p", "hint", empty));
    const list = make("dl", "info-list");
    for (const [name, text] of rows) list.append(make("dt", "", name), make("dd", "", text));
    if (rows.length) box.append(list);
    return box;
  };
  el("info-body").replaceChildren(
    section("Tools", info.tools.map(tool => [tool.name, tool.description]), "No tools."),
    section("Skills", info.skills.map(skill => [skill.name, `${skill.description || "No description"} · ${skill.scope}${skill.enabled ? "" : " · off"}`]), "No skills. Add them in Settings → Skills."),
    section("Subagents", info.subagents.map(agent => [agent.name, `${agent.description} · ${agent.source}`]), "Only the built-in explorer."),
    section("Hooks", info.hooks.map(hook => [`${hook.tool} · ${hook.pattern}`, hook.message || "Blocked"]), "No hooks. Add block rules to .dikdik/hooks.json in the workspace."),
  );
  const dialog = el("info-dialog") as HTMLDialogElement;
  if (typeof dialog.showModal === "function") dialog.showModal();
}
el("info-close").addEventListener("click", () => (el("info-dialog") as HTMLDialogElement).close());

// --- onboarding and the missing-model banner -------------------------------------------
function fillComposer(text: string): void {
  textarea("objective").value = text;
  composerDrafts.update(text);
  autosize();
  textarea("objective").focus?.();
}
function renderOnboarding(): void {
  const steps = [
    { done: modelReady, label: "Set up a model", detail: "Add a model profile and its API key, or a local server.", action: "Open Models", run: () => openSettings("models") },
    { done: lastTasks.length > 0, label: "Say hello", detail: "Ask Dikdik about this project.", action: "Try it", run: () => fillComposer("Hello! Give me a quick tour of this project: what it does and how it is organised.") },
    { done: storageGet("dikdik.onboarding.applied") === "1", label: "Review and apply a change", detail: "Let Dikdik fix something, read the diff, then Apply.", action: "Try it", run: () => fillComposer("Find one small bug or rough edge in this project and fix it.") },
  ];
  const box = el("onboarding");
  box.hidden = storageGet("dikdik.onboarding.dismissed") === "1" || steps.every(step => step.done);
  el("onboarding-steps").replaceChildren(...steps.map(step => {
    const li = make("li", step.done ? "onb-step done" : "onb-step");
    const mark = make("span", "onb-mark"); if (step.done) mark.append(icon("check"));
    const text = make("span", "onb-text"); text.append(make("strong", "", step.label), make("span", "", step.detail));
    li.append(mark, text);
    if (!step.done) { const go = make("button", "small", step.action); go.type = "button"; go.onclick = step.run; li.append(go); }
    return li;
  }));
}
el("onboarding-dismiss").addEventListener("click", () => { storageSet("dikdik.onboarding.dismissed", "1"); renderOnboarding(); });
function syncModelBanner(): void {
  const needsModel = parseMode(select("mode").value) !== "offline";
  el("model-banner").hidden = modelReady || !needsModel;
}
el("model-banner-setup").addEventListener("click", () => openSettings("models"));

// --- settings routes, Git identity, sounds -----------------------------------------------
function settingsHash(pane: string | null): void {
  const loc = globalThis.location;
  if (!loc || typeof globalThis.history?.replaceState !== "function") return;
  const wanted = pane ? `#settings/${pane}` : "";
  if (loc.hash !== wanted && (pane || loc.hash.startsWith("#settings"))) globalThis.history.replaceState(null, "", loc.pathname + loc.search + wanted);
}
/** `#settings/<pane>` opens that settings page; an unknown page falls back to General. */
function routeSettings(): void {
  const match = /^#settings(?:\/([\w-]*))?$/.exec(globalThis.location?.hash ?? "");
  if (!match) return;
  const pane = match[1] ?? "";
  if (pane && !(pane in PANE_TITLES)) message(`There is no “${pane}” settings page; showing General.`, true);
  openSettings(pane in PANE_TITLES ? pane : "general");
}
el("git-identity").addEventListener("submit", event => {
  event.preventDefault();
  void saveAppSetting({ git_name: input("git-name").value.trim(), git_email: input("git-email").value.trim() });
});
input("sounds").addEventListener("change", () => { void saveAppSetting({ sounds: input("sounds").checked }); });
function chime(): void {
  if (!appSettings?.sounds || (globalThis.document?.hasFocus?.() && !document.hidden)) return;
  const Context = (globalThis as { AudioContext?: typeof AudioContext }).AudioContext;
  if (!Context) return;
  const audio = new Context();
  [660, 880].forEach((pitch, index) => {
    const tone = audio.createOscillator(), gain = audio.createGain();
    tone.frequency.value = pitch;
    gain.gain.setValueAtTime(0.0001, audio.currentTime + index * 0.16);
    gain.gain.exponentialRampToValueAtTime(0.15, audio.currentTime + index * 0.16 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + index * 0.16 + 0.3);
    tone.connect(gain).connect(audio.destination);
    tone.start(audio.currentTime + index * 0.16);
    tone.stop(audio.currentTime + index * 0.16 + 0.32);
  });
  setTimeout(() => { void audio.close(); }, 1000);
}
function resetStop(): void {
  if (stopArmed !== undefined) clearTimeout(stopArmed);
  stopArmed = undefined;
  el("cancel").replaceChildren(icon("stop"), document.createTextNode("Stop"));
  setClass(el("cancel"), "confirming", false);
}

// --- models: settings panes, the model picker on the pill, sandbox secrets ----------------
const paneDeps = {
  request: <T>(path: string, method: string, body?: unknown) => request<T>(path, method, body),
  make, message,
  changed: () => { void loadModel().catch(() => undefined); void loadExposedSecrets(); void ensureLists(true); },
};
/** Agent-profile sandbox secrets: blank → every exposed key (null), "none" → [], else the listed names. */
function secretRefsField(text: string): string[] | null {
  const value = text.trim();
  if (!value) return null;
  if (value.toLowerCase() === "none") return [];
  return [...new Set(value.split(",").map(item => item.trim()).filter(Boolean))];
}
async function loadExposedSecrets(): Promise<void> {
  try {
    const keys = await api<{ env: string }[]>("/api/secrets");
    exposedSecrets = keys.filter(key => key.env).length;
  } catch { exposedSecrets = 0; }
  syncMode();
}
function closeModelMenu(): void { el("model-menu").hidden = true; }
async function openModelMenu(): Promise<void> {
  const menu = el("model-menu");
  if (!menu.hidden) { closeModelMenu(); return; }
  let profiles: { id: string; name: string; model: string; is_default: boolean }[] = [];
  try { profiles = await api<typeof profiles>("/api/profiles"); } catch { /* the menu still offers Manage models */ }
  const pick = (choice: { id: string; name: string } | null) => {
    modelOverride = choice;
    closeModelMenu();
    message(choice ? `Using ${choice.name} for your next messages in this tab.` : "Back to the default model.");
    void loadModel().catch(() => undefined);
  };
  const item = (label: string, detail: string, checked: boolean, run: () => void) => {
    const node = make("button", "model-item"); node.type = "button";
    node.setAttribute("role", "menuitemradio");
    node.setAttribute("aria-checked", String(checked));
    const mark = make("span", "model-check"); if (checked) mark.append(icon("check"));
    node.append(mark, make("span", "model-name", label));
    if (detail) node.append(make("span", "model-detail", detail));
    node.onclick = run;
    return node;
  };
  const fallback = profiles.find(profile => profile.is_default);
  const manage = make("button"); manage.type = "button"; manage.setAttribute("role", "menuitem");
  manage.append(icon("gear"), make("span", "", "Manage models…"));
  manage.onclick = () => { closeModelMenu(); openSettings("models"); };
  menu.replaceChildren(
    item("Default", fallback ? `${fallback.name} · ${fallback.model}` : "from settings", modelOverride === null, () => pick(null)),
    ...profiles.map(profile => item(profile.name, profile.model, modelOverride?.id === profile.id, () => pick({ id: profile.id, name: profile.name }))),
    make("hr"), manage,
  );
  const box = el("model-pill").getBoundingClientRect();
  menu.hidden = false;
  const width = menu.offsetWidth, height = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(box.left, innerWidth - width - 8))}px`;
  menu.style.top = `${box.top - height - 6 > 8 ? box.top - height - 6 : box.bottom + 6}px`;
  menu.querySelector<HTMLButtonElement>("[aria-checked=true]")?.focus();
}
if (typeof document.addEventListener === "function") {
  document.addEventListener("click", event => {
    const menu = el("model-menu");
    if (!menu.hidden && event.target instanceof Node && !menu.contains(event.target)) closeModelMenu();
  });
  document.addEventListener("keydown", (event: KeyboardEvent) => { if (event.key === "Escape" && !el("model-menu").hidden) closeModelMenu(); });
}

let stopArmed: ReturnType<typeof setTimeout> | undefined;
button("cancel").onclick = async () => {
  if (!selected) return;
  if (stopArmed === undefined) {
    // A second click within three seconds confirms; the run's work so far is kept either way.
    el("cancel").replaceChildren(icon("stop"), document.createTextNode("Stop run?"));
    setClass(el("cancel"), "confirming", true);
    stopArmed = setTimeout(resetStop, 3000);
    return;
  }
  resetStop();
  try {
    const data = await api<{ requested: boolean }>(`/api/tasks/${selected}/cancel`, {});
    message(data.requested ? "Stopping…" : "Task has already stopped.");
    await observe();
  } catch (error) { message(error instanceof Error ? error.message : "Cancellation failed", true); }
};

button("refresh").onclick = async () => {
  try { await Promise.all([project(), loadHistory()]); message(`Re-scanned ${projectName || "the workspace"}.`); }
  catch (error) { message(error instanceof Error ? error.message : "Refresh failed", true); }
};

// Poll real durable events. No simulated execution or progress percentages.
setInterval(() => { void observe(); }, 1000);
// Pasting the sign-in link into an already-open tab only changes the hash.
globalThis.addEventListener?.("hashchange", () => { if (!connected) void boot(); else routeSettings(); });
void boot();
