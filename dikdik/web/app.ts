import { browserView, buildTaskRequest, conversationLabel, eventsToChat, fileStatusLabel, isTerminal, modePolicy, parseMode, parseVerification, resultStatus, reviewDiff, terminalTranscript, transcriptMarkdown } from "./contracts";
import { buildAutomation, describeSchedule, parseAutomations, parseRunSummaries, profilesPanel, secretsPanel, skillsPanel } from "./contracts";
import type { ActiveModel, AgentProfile, Automation, ChatMessage, DiffReview, M4Factory, M4Node, Project, Task, TaskEvent, TaskResult, TaskSummary } from "./contracts";

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
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) nodes.push(document.createTextNode(text.slice(last, index)));
    const token = match[0];
    nodes.push(token.startsWith("`") ? make("code", "", token.slice(1, -1)) : make("strong", "", token.slice(2, -2)));
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
    li.append(make("p", "turn-note", "No files changed in this turn."));
    return li;
  }
  const card = make("div", `result-card result-${state}`);
  const head = make("div", "result-head");
  const badge = make("span", "result-icon");
  badge.append(icon(state === "completed" ? "check" : state === "cancelled" ? "stop" : "alert"));
  const titles: Record<string, string> = { completed: "Candidate ready for review", failed: "Task failed", blocked: "Task blocked", cancelled: "Task cancelled", interrupted: "Task interrupted" };
  head.append(badge, make("span", "result-title", titles[state] ?? `Task ${state}`));
  if (state === "completed") {
    const verified = result?.tests_verified === true;
    head.append(make("span", verified ? "verify-badge verified" : "verify-badge", verified ? "Tests passed" : "Not test-verified"));
  }
  card.append(head, make("p", "result-text", resultStatus(state, result)));
  const actions = make("div", "result-actions");
  if (result && typeof result.diff === "string" && result.diff) {
    const open = make("button", "small");
    open.type = "button";
    open.append(icon("diff"), make("span", "", "Review & apply"));
    open.onclick = () => openCanvas("changes");
    actions.append(open, copyButton(() => review.diff, "Copy diff"));
  }
  if (state !== "completed") {
    const resume = make("button", "small primary");
    resume.type = "button";
    resume.append(icon("refresh"), make("span", "", "Continue"));
    resume.onclick = () => { void sendMessage("Continue where you left off. Finish the original request."); };
    actions.append(resume);
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
  } else if (msg.body) content.append(richText(msg.body));
  li.append(content);
  return li;
}

let rendered = new Set<string>();
let pendingCalls: { name: string; body: HTMLElement }[] = [];
let lastRole = "";
function renderMessage(msg: ChatMessage): HTMLLIElement | null {
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
    li.append(make("div", "bubble", msg.body ?? ""));
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
function renderMessageStream(messages: ChatMessage[]): void {
  const stream = el("message-stream"), thread = el("thread");
  const stick = nearBottom(thread);
  for (const msg of messages) {
    if (rendered.has(msg.id)) continue;
    rendered.add(msg.id);
    const node = renderMessage(msg);
    if (node) stream.append(node);
  }
  if (stick) thread.scrollTop = thread.scrollHeight;
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
    openFile = path; fileVersion = data.version;
    el("file-name").textContent = path;
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
  el("message-stream").replaceChildren();
  el("review-controls").replaceChildren();
  el("diff").replaceChildren();
  el("review-note").textContent = "";
  liveEvents = [];
  input("browser-consent").checked = false;
  input("browser-approval").checked = false;
  input("browser-url").value = "";
  renderTerminal();
  renderBrowser();
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
  textarea("objective").placeholder = selected ? "Reply to Dikdik…" : "Ask Dikdik to build, fix or change something…";
  el("task-actions").hidden = !selected;
}
function selectTask(id: string): void {
  stopStream();
  selected = id; watching = true; selectedState = "";
  resetConversation();
  el("chat-title").textContent = "Loading…";
  setState("");
  markCurrent();
  closeSidebarDrawer();
  setUrl(id);
  syncComposer();
}
function newTask(): void {
  stopStream();
  selected = null; watching = false; reviewTask = null; reviewKey = ""; selectedState = "";
  resetConversation();
  el("chat-title").textContent = "New task";
  setTitle("");
  setState("");
  markCurrent();
  closeSidebarDrawer();
  setUrl(null);
  syncComposer();
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
  constructor(message: string, readonly status: number) { super(message); }
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
    throw new RequestError(detail, response.status);
  }
  return data as T;
}
const api = <T>(path: string, body?: unknown): Promise<T> => request<T>(path, body === undefined ? "GET" : "POST", body);
const apiM4 = <T>(path: string, method: string, body?: unknown): Promise<T> => request<T>(path, method, body);

// The composer names the model the next task will use, or says what's missing.
async function loadModel(): Promise<void> {
  const info = await api<ActiveModel>("/api/model");
  const pill = el("model-pill");
  setClass(pill, "unset", !info.configured);
  el("model-label").textContent = info.configured ? info.model ?? "Model" : "Set up a model";
  pill.title = info.configured
    ? info.source === "profile" ? `${info.model} · ${info.provider_name} (profile “${info.profile}”). Change it in Settings → Models.` : `${info.model} from server environment variables`
    : info.problem ?? "No model is set up";
}

async function project(): Promise<void> {
  const data = await api<Project>("/api/project");
  projectName = data.name;
  projectFiles = data.files.map(file => file.path);
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
function renderHistory(): void {
  const query = ((el("task-search") as HTMLInputElement).value ?? "").trim().toLowerCase();
  const visible = lastTasks.filter(task => (showArchived || !task.archived || task.id === selected)
    && (!query || `${task.title ?? ""} ${task.objective ?? ""}`.toLowerCase().includes(query)));
  const pinned = visible.filter(task => task.pinned), rest = visible.filter(task => !task.pinned);
  const nodes: HTMLElement[] = [];
  if (pinned.length) nodes.push(make("p", "list-head", "Pinned"), ...pinned.map(taskRow));
  if (rest.length) nodes.push(...(pinned.length ? [make("p", "list-head", showArchived ? "All" : "Recent")] : []), ...rest.map(taskRow));
  if (!nodes.length) nodes.push(make("div", "conv-empty", query ? "No matching tasks" : "No tasks yet"));
  el("history").replaceChildren(...nodes);
  el("recent-head").hidden = pinned.length > 0;
  const archived = lastTasks.filter(task => task.archived).length;
  el("show-archived").hidden = archived === 0;
  el("show-archived").textContent = showArchived ? "Hide archived" : `Show archived (${archived})`;
}
async function loadHistory(): Promise<void> {
  lastTasks = await api<TaskSummary[]>("/api/tasks");
  renderHistory();
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
    if (record.kind === "running" || isTerminal(record.kind)) {
      setState(record.kind);
      selectedState = record.kind;
      void loadHistory();
    }
    if (record.kind === "running") { el("working").hidden = false; el("working-label").textContent = "Working"; }
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

async function observe(): Promise<void> {
  if (!selected || polling || !watching || liveTask === selected) return;
  polling = true;
  const id = selected;
  try {
    const task = await api<Task>("/api/tasks/" + id);
    if (id !== selected) return;
    liveEvents = [...task.events] as TaskEvent[];
    liveObjective = task.request.objective;
    liveSeq = liveEvents.reduce((max, event) => Math.max(max, event.seq ?? 0), 0);
    el("chat-title").textContent = conversationLabel({ ...task, objective: task.request.objective, title: lastTasks.find(item => item.id === id)?.title ?? "" });
    setTitle(el("chat-title").textContent ?? "");
    setState(task.state);
    selectedState = task.state;
    renderTerminal();
    renderBrowser();
    button("cancel").disabled = isTerminal(task.state);
    const messages = eventsToChat(task.events as TaskEvent[], task.request.objective);
    lastMessages = messages;
    renderMessageStream(messages);
    el("working").hidden = isTerminal(task.state);
    el("working-label").textContent = task.state === "queued" ? "Queued" : "Working";
    renderReview(task.id, task.state, task.result);
    syncApplyBar();
    const known = lastTasks.find(item => item.id === id);
    if (isTerminal(task.state) || (known && known.state !== task.state)) await loadHistory();
    if (isTerminal(task.state)) watching = false;
    else startStream(id);
  } catch (error) {
    if (error instanceof Error) message(error.message, true);
  } finally { polling = false; }
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
      models: mountPanel("panel-profiles", profilesPanel),
      secrets: mountPanel("panel-secrets", secretsPanel),
      skills: mountPanel("panel-skills", skillsPanel),
    };
  }
  textarea("objective").focus?.();
  const linked = globalThis.location ? new URLSearchParams(globalThis.location.search).get("task") : null;
  if (linked && !selected && lastTasks.some(task => task.id === linked)) selectTask(linked);
  void observe();
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
  if (name === "mcp") void loadMcp();
  if (name === "automations") { void loadAutomations(); void loadAgents().catch(() => undefined); }
  if (name === "agents") { void loadAgents().catch(() => undefined); void loadAgentModels().catch(() => undefined); }
  void refreshPanels[name]?.();
}
function openSettings(pane: string): void {
  const dialog = el("settings") as HTMLDialogElement;
  if (typeof dialog.showModal === "function") dialog.showModal();
  showPane(pane);
  closeSidebarDrawer();
}
el("open-settings").addEventListener("click", () => openSettings("general"));
el("model-pill").addEventListener("click", () => openSettings("models"));
// Profiles or keys may have changed while settings were open.
el("settings").addEventListener("close", () => { void loadModel().catch(() => undefined); void loadAgents().catch(() => undefined); });
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
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); newTask(); }
    else if (event.key === "Escape" && !el("task-menu").hidden) closeTaskMenu();
    else if (event.key === "Escape" && !el("canvas").hidden && !(el("settings") as HTMLDialogElement).open) setCanvas(false);
  });
}

// --- composer / task submission -------------------------------------------
function autosize(): void {
  const area = textarea("objective");
  if (!area.style) return;
  area.style.height = "auto";
  area.style.height = `${Math.min(area.scrollHeight, 260)}px`;
}
function setOptions(open: boolean): void {
  el("options-panel").hidden = !open;
  el("options-toggle").setAttribute("aria-expanded", String(open));
}
function syncMode(): void {
  const mode = parseMode(select("mode").value);
  const policy = modePolicy(mode, parseVerification(select("verification").value));
  (el("offline-fields") as HTMLElement).hidden = !policy.showEdits;
  textarea("edits").disabled = !policy.showEdits;
  (el("model-fields") as HTMLElement).hidden = !policy.requiresExternal;
  (el("model-hint") as HTMLElement).hidden = mode !== "model";
  (el("agent-hint") as HTMLElement).hidden = mode !== "agent";
  (el("external-field") as HTMLElement).hidden = !policy.requiresExternal;
  input("external").disabled = !policy.requiresExternal;
  input("external").required = policy.requiresExternal;
  select("verification").value = policy.verification;
  select("verification").disabled = policy.locksVerification;
  el("browser-field").hidden = mode !== "agent";
  input("browser-approval").disabled = mode !== "agent";
  const execution = policy.requiresExecution || (mode === "agent" && input("browser-approval").checked);
  (el("execution-field") as HTMLElement).hidden = !execution;
  input("execution").disabled = !execution;
  input("execution").required = execution;
  el("execution-field").title = "Runs approved tools and tests inside restricted Docker containers.";
  el("model-pill").hidden = !policy.requiresExternal;
  el("agent-profile-pill").hidden = mode !== "agent" || !agentProfiles.length;
  if (policy.showEdits) setOptions(true);
}
select("mode").addEventListener("change", syncMode);
select("verification").addEventListener("change", syncMode);
el("browser-approval").addEventListener("change", syncMode);
el("options-toggle").addEventListener("click", () => setOptions(el("options-panel").hidden === true));
syncMode();

textarea("objective").addEventListener("input", autosize);
textarea("objective").addEventListener("keydown", (event: KeyboardEvent) => {
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
      autosize();
      textarea("objective").focus();
    });
  }
}

function composerRequest(text: string) {
  return buildTaskRequest({
    objective: text, mode: parseMode(select("mode").value),
    edits: textarea("edits").value, verification: parseVerification(select("verification").value),
    approveExecution: input("execution").checked, approveExternal: input("external").checked,
    approveBrowser: input("browser-approval").checked,
    agentProfile: (el("agent-profile") as HTMLSelectElement).value ?? "",
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
async function sendMessage(text: string): Promise<void> {
  if (!selected) return;
  try {
    const reply = await api<{ queued: boolean }>(`/api/tasks/${encodeURIComponent(selected)}/messages`, composerRequest(text));
    watching = true;
    if (reply.queued) message("Queued. Dikdik will reply when the current step finishes.");
    await loadHistory(); await observe();
  } catch (error) { message(error instanceof Error ? error.message : "Message failed", true); }
}

el("task-form").addEventListener("submit", async event => {
  event.preventDefault(); button("start").disabled = true;
  try {
    const text = textarea("objective").value;
    if (selected) {
      composerRequest(text);  // validate before clearing the field
      textarea("objective").value = "";
      autosize();
      await sendMessage(text);
      return;
    }
    const task = await api<TaskSummary>("/api/tasks", composerRequest(text));
    textarea("objective").value = "";
    autosize();
    selectTask(task.id);
    await loadHistory(); await observe();
  } catch (error) { message(error instanceof Error ? error.message : "Task failed", true); }
  finally { button("start").disabled = false; }
});

button("cancel").onclick = async () => {
  if (!selected) return;
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
globalThis.addEventListener?.("hashchange", () => { if (!connected) void boot(); });
void boot();
