export type Mode = "offline" | "model" | "agent" | "plan";
export type Verification = "syntax" | "docker";
export interface Edit { path: string; before: string; after: string }
export interface TaskRequest {
  objective: string;
  mode: Mode;
  edits: Edit[];
  verification: Verification;
  approve_execution: boolean;
  approve_external: boolean;
  approve_browser?: boolean;
  parent_task?: string;
  agent_profile?: string;
  model_profile?: string;
  confirmation?: ConfirmationPolicy;
  attachments?: string[];
  goal_max_turns?: number;
  approve_secrets?: boolean;
}
export type ConfirmationPolicy = "never" | "risky" | "always";
export interface AttachmentMeta { id: string; name: string; type: string; size: number }
export interface AgentProfile {
  id: string; name: string; instructions: string; model_profile_id: string;
  max_model_calls: number | null; max_tool_calls: number | null; deadline_seconds: number | null;
  allow_shell: boolean; allow_browser: boolean; use_mcp: boolean; use_skills: boolean;
  critic?: boolean; memory?: boolean; secret_refs?: string[] | null;
}
export interface Project {
  name: string;
  files: { path: string; bytes: number }[];
  python_tests: string[];
  manifests: string[];
  limitations: string;
}
export interface TaskResult {
  diff?: string;
  changed_files?: string[];
  file_changes?: FileChange[];
  error?: string;
  tests_verified?: boolean;
  checks?: { kind: string; status: string }[];
}
export type FileStatus = "added" | "modified" | "deleted";
export interface FileChange { path: string; status: FileStatus; start_line: number; end_line: number }
export interface ReviewedFile extends FileChange { diff: string }
export interface DiffReview { diff: string; files: ReviewedFile[]; metadata: "available" | "legacy" | "invalid" }

// --- Conversation/message stream (M5) ---------------------------------------
// The event stream maps to a chat: user objective is the opening message,
// agent events become assistant/tool messages with expandable evidence.
export type MessageRole = "user" | "assistant" | "tool" | "system";
export type EventKind =
  | "queued" | "user_message" | "plan" | "inspected" | "proposal" | "agent_started"
  | "agent_message" | "agent_tool" | "agent_model" | "agent_check"
  | "agent_tool_call" | "agent_tool_result" | "agent_repair"
  | "agent_finished" | "execution" | "syntax" | "cancellation_requested"
  | "recovery_cleanup" | "completed" | "failed" | "blocked" | "cancelled" | "interrupted"
  | string;
export interface TaskEvent { seq: number; task_id: string; time: number; kind: EventKind; data: Record<string, unknown> }
export interface ChatMessage {
  id: string;          // stable id = `${role}-${seq}` or `${role}-terminal`
  role: MessageRole;
  time: number;
  kind: EventKind;     // the originating event kind (for tool/system messages)
  title: string;       // human-readable label for the card
  body?: string;       // primary text (model message, tool arguments, etc.)
  details?: unknown;   // structured payload (tool result data, check status, etc.)
  state?: string;      // terminal state for system/terminal messages
  testsVerified?: boolean;
}
export interface TerminalCommandView {
  id: string;
  command: string;
  output: string;
  running: boolean;
  exitCode?: number | null;
  status?: string;
}
export interface TerminalView { commands: TerminalCommandView[]; text: string }
export interface ConversationSummary { id: string; created: number; state: string; objective: string; messages: number }

export function fileStatusLabel(status: FileStatus): string {
  return { added: "Added", modified: "Modified", deleted: "Deleted" }[status];
}

// Git quotes unusual path bytes using C escapes and UTF-8 octal sequences.
function gitPath(path: string, quoteUnicode: boolean): string {
  let escaped = "";
  for (const byte of new TextEncoder().encode(path)) {
    if (byte === 34 || byte === 92) escaped += "\\" + String.fromCharCode(byte);
    else if (byte === 9) escaped += "\\t";
    else if (byte === 10) escaped += "\\n";
    else if (byte === 13) escaped += "\\r";
    else if (byte < 32 || byte === 127 || (quoteUnicode && byte >= 128)) escaped += "\\" + byte.toString(8).padStart(3, "0");
    else escaped += String.fromCharCode(byte);
  }
  // With core.quotePath=false, preserve Unicode rather than reinterpreting UTF-8 bytes.
  if (!quoteUnicode) escaped = new TextDecoder().decode(Uint8Array.from(escaped, char => char.charCodeAt(0)));
  return escaped === path ? path : `"${escaped}"`;
}

function candidateGitPath(path: string): string {
  const bytes = new TextEncoder().encode(path);
  if (bytes.every(byte => byte >= 33 && byte < 127 && byte !== 34 && byte !== 92)) return path;
  return '"' + Array.from(bytes, byte => byte >= 32 && byte < 127 && byte !== 34 && byte !== 92 ? String.fromCharCode(byte) : "\\" + byte.toString(8).padStart(3, "0")).join("") + '"';
}

function supportedGitPaths(path: string): string[] {
  const paths = [candidateGitPath(path)];
  if (!/\s/.test(path)) paths.push(gitPath(path, true), gitPath(path, false));
  return paths;
}

function validatesTextPatch(chunk: string[], path: string, status: FileStatus): boolean {
  const hunkStart = chunk.findIndex(line => line.startsWith("@@"));
  const headers = chunk.slice(1, hunkStart < 0 ? chunk.length : hunkStart);
  if (headers.some(line => !["new file mode ", "deleted file mode ", "old mode ", "new mode ", "index ", "--- ", "+++ "].some(prefix => line.startsWith(prefix)))) return false;
  const oldPaths = headers.filter(line => line.startsWith("--- ")).map(line => line.slice(4));
  const newPaths = headers.filter(line => line.startsWith("+++ ")).map(line => line.slice(4));
  if (hunkStart < 0) return oldPaths.length === 0 && newPaths.length === 0;
  const expectedOld = status === "added" ? ["/dev/null"] : supportedGitPaths("a/" + path);
  const expectedNew = status === "deleted" ? ["/dev/null"] : supportedGitPaths("b/" + path);
  if (oldPaths.length !== 1 || newPaths.length !== 1 || !expectedOld.includes(oldPaths[0]!) || !expectedNew.includes(newPaths[0]!)) return false;
  let before = 0, after = 0, lastData = false;
  for (const line of chunk.slice(hunkStart)) {
    if (line.startsWith("@@")) {
      if (before || after) return false;
      const match = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$/.exec(line);
      if (!match) return false;
      before = Number(match[2] ?? 1); after = Number(match[4] ?? 1); lastData = false;
      if (!Number.isSafeInteger(before) || !Number.isSafeInteger(after) || before > chunk.length || after > chunk.length) return false;
    } else if (line === "\\ No newline at end of file") {
      if (!lastData) return false;
      lastData = false;
    } else {
      const prefix = line[0];
      if (prefix === " ") { if (!before || !after) return false; before--; after--; }
      else if (prefix === "-") { if (!before) return false; before--; }
      else if (prefix === "+") { if (!after) return false; after--; }
      else return false;
      lastData = true;
    }
  }
  return before === 0 && after === 0;
}

/** All-or-nothing validation: never associate a path with another file's diff. */
export function reviewDiff(result: unknown): DiffReview {
  const record = typeof result === "object" && result !== null ? result as Record<string, unknown> : {};
  const diff = typeof record.diff === "string" ? record.diff : "";
  const fallback = (metadata: "legacy" | "invalid"): DiffReview => ({ diff, files: [], metadata });
  if (record.file_changes === undefined) return fallback("legacy");
  if (!Array.isArray(record.file_changes)) return fallback("invalid");
  if (!diff) return record.file_changes.length ? fallback("invalid") : { diff, files: [], metadata: "available" };
  if (!diff.endsWith("\n")) return fallback("invalid");
  const lines = diff.split("\n"), files: ReviewedFile[] = [], paths = new Set<string>();
  let end = 0;
  for (const entry of record.file_changes) {
    if (typeof entry !== "object" || entry === null) return fallback("invalid");
    const { path, status, start_line, end_line } = entry as Record<string, unknown>;
    if (typeof path !== "string" || !path || /[\r\n\0]/.test(path) || new TextDecoder().decode(new TextEncoder().encode(path)) !== path || paths.has(path) ||
        (status !== "added" && status !== "modified" && status !== "deleted") ||
        typeof start_line !== "number" || typeof end_line !== "number" ||
        !Number.isSafeInteger(start_line) || !Number.isSafeInteger(end_line) ||
        start_line !== end || end_line <= start_line || end_line > lines.length - 1) return fallback("invalid");
    const chunk = lines.slice(start_line, end_line);
    const oldPaths = supportedGitPaths("a/" + path), newPaths = supportedGitPaths("b/" + path);
    const headers = oldPaths.flatMap(before => newPaths.map(after => `diff --git ${before} ${after}`));
    if (!headers.includes(chunk[0] || "") || chunk.slice(1).some(line => line.startsWith("diff --git "))) return fallback("invalid");
    const added = chunk.some(line => line.startsWith("new file mode "));
    const deleted = chunk.some(line => line.startsWith("deleted file mode "));
    if ((added && deleted) || status !== (added ? "added" : deleted ? "deleted" : "modified")) return fallback("invalid");
    if (!validatesTextPatch(chunk, path, status)) return fallback("invalid");
    files.push({ path, status, start_line, end_line, diff: chunk.join("\n") + "\n" });
    paths.add(path); end = end_line;
  }
  return end === lines.length - 1 ? { diff, files, metadata: "available" } : fallback("invalid");
}
export interface TaskSummary {
  id: string; state: string;
  objective?: string; title?: string; created?: number; updated?: number;
  pinned?: boolean; archived?: boolean; mode?: string;
  folder?: string; tags?: string[];
}
export interface Task extends TaskSummary {
  request: TaskRequest;
  events: { seq: number; task_id: string; time: number; kind: string; data: Record<string, unknown> }[];
  result?: TaskResult | null;
}
export function isTerminal(state: string): boolean {
  return ["completed", "failed", "blocked", "cancelled", "interrupted", "draft", "paused"].includes(state);
}
/** States an agent run can be resumed from with its saved transcript. */
export function isResumable(state: string): boolean {
  return ["paused", "failed", "cancelled", "interrupted", "blocked"].includes(state);
}
export function modePolicy(mode: Mode, verification: Verification) {
  // Every mode can run with a syntax check only, or with tests in Docker.
  return {
    showEdits: mode === "offline",
    requiresExternal: mode !== "offline",
    verification: mode === 'plan' ? 'syntax' as const : verification,
    locksVerification: mode === 'plan',
    requiresExecution: mode !== 'plan' && verification === "docker",
  };
}
export function parseMode(value: string): Mode {
  if (value === "offline" || value === "model" || value === "agent" || value === 'plan') return value;
  throw new Error("Select a supported proposal source.");
}
export function parseVerification(value: string): Verification {
  if (value === "syntax" || value === "docker") return value;
  throw new Error("Select a supported verification boundary.");
}
export function buildTaskRequest(input: {
  objective: string; mode: Mode; verification: Verification;
  approveExecution: boolean; approveExternal: boolean; approveBrowser?: boolean; edits: string; agentProfile?: string;
  modelProfile?: string | undefined; attachments?: string[]; goalMaxTurns?: number | undefined; approveSecrets?: boolean;
}): TaskRequest {
  const policy = modePolicy(input.mode, input.verification);
  if (!input.objective.trim()) throw new Error("Enter an objective.");
  if (policy.requiresExternal && !input.approveExternal) throw new Error("Approve sending code and the objective to the external provider.");
  if (policy.requiresExecution && !input.approveExecution) throw new Error("Approve execution inside Docker before starting this task.");
  const browsing = input.mode === "agent" && input.approveBrowser === true;
  if (browsing && !input.approveExecution) throw new Error("Approve Docker execution to use the browser.");
  let edits: Edit[] = [];
  if (policy.showEdits) {
    const parsed: unknown = JSON.parse(input.edits);
    if (!Array.isArray(parsed) || !parsed.every((edit: unknown) =>
      typeof edit === "object" && edit !== null &&
      "path" in edit && typeof edit.path === "string" &&
      "before" in edit && typeof edit.before === "string" &&
      "after" in edit && typeof edit.after === "string"
    )) throw new Error("Exact replacements must be an array of path, before, and after strings.");
    edits = parsed as Edit[];
  }
  return {
    objective: input.objective, mode: input.mode, edits,
    verification: policy.verification,
    approve_execution: (policy.requiresExecution || browsing) && input.approveExecution,
    approve_external: policy.requiresExternal && input.approveExternal,
    ...(browsing ? { approve_browser: true } : {}),
    ...(["agent", "plan"].includes(input.mode) && input.agentProfile ? { agent_profile: input.agentProfile } : {}),
    ...(input.mode !== "offline" && input.modelProfile ? { model_profile: input.modelProfile } : {}),
    ...(input.attachments?.length ? { attachments: input.attachments } : {}),
    ...(input.mode === "agent" && input.approveSecrets && input.approveExecution && (policy.requiresExecution || browsing) ? { approve_secrets: true } : {}),
    ...(input.goalMaxTurns ? goalTurns(input.mode, input.goalMaxTurns) : {}),
  };
}
function goalTurns(mode: Mode, turns: number): { goal_max_turns: number } {
  if (mode !== "agent") throw new Error("A goal needs Agent mode.");
  if (!Number.isInteger(turns) || turns < 1 || turns > 20) throw new Error("A goal can run 1–20 turns.");
  return { goal_max_turns: turns };
}

// --- composer slash commands ----------------------------------------------------
export interface SlashCommand { name: string; hint: string; description: string }
export const SLASH_COMMANDS: readonly SlashCommand[] = [
  { name: "plan", hint: "[request]", description: "Switch to the read-only planner" },
  { name: "code", hint: "[request]", description: "Back to the coding agent" },
  { name: "goal", hint: "[--max N] goal", description: "Keep working, turn after turn, until a judge says the goal is met" },
  { name: "btw", hint: "question", description: "Ask a side question; the answer is not added to the conversation" },
  { name: "model", hint: "[name]", description: "Use another model profile for the next messages" },
  { name: "condense", hint: "", description: "Summarise older turns to free up context" },
  { name: "fork", hint: "", description: "Copy this conversation into a new one" },
];
/** `/name rest` → its parts, or null when the text is not a slash command. */
export function parseSlash(text: string): { name: string; arg: string } | null {
  const match = /^\/([a-z][a-z0-9_-]{0,63})(?:\s+([\s\S]*))?$/i.exec(text.trim());
  return match ? { name: (match[1] ?? "").toLowerCase(), arg: (match[2] ?? "").trim() } : null;
}
/** `/goal --max 5 ship it` → { turns: 5, goal: "ship it" }; the default is 5 turns. */
export function parseGoal(arg: string): { turns: number; goal: string } {
  const match = /^--max\s+(\d{1,2})\s*([\s\S]*)$/.exec(arg.trim());
  const turns = match ? Number(match[1]) : 5;
  const goal = (match ? match[2] ?? "" : arg).trim();
  if (!goal) throw new Error("Say what the goal is, for example /goal --max 5 make the tests pass.");
  if (turns < 1 || turns > 20) throw new Error("A goal can run 1–20 turns.");
  return { turns, goal };
}
/** Commands (and skill names) matching what has been typed after the slash. */
export function slashMatches(text: string, skills: readonly string[] = []): SlashCommand[] {
  const typed = /^\/([a-z0-9_-]*)$/i.exec(text);
  if (!typed) return [];
  const all = [...SLASH_COMMANDS, ...skills.map(name => ({ name, hint: "[request]", description: "Use this skill" }))];
  const prefix = (typed[1] ?? "").toLowerCase();
  return all.filter(command => command.name.startsWith(prefix)).slice(0, 12);
}

// --- run state read from events -----------------------------------------------
export interface ContextUsage { bytes: number; limit: number; ratio: number }
/** How full the model's context was at the latest model call. */
export function contextUsage(events: TaskEvent[]): ContextUsage | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (!event) continue;
    if (event.kind === "condensed") return null;
    const bytes = numOrUndef(event.data?.context_bytes), limit = numOrUndef(event.data?.context_limit);
    if (event.kind === "agent_model" && bytes !== undefined && limit !== undefined && limit > 0) return { bytes, limit, ratio: Math.min(1, bytes / limit) };
  }
  return null;
}
export interface TokenUsage {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  totalTokens?: number;
  reportedCost?: number;
}
export interface TurnUsage extends TokenUsage { turn: number; calls: number; profile: string }
export interface UsageMetrics { context: ContextUsage | null; task: TokenUsage; turns: TurnUsage[] }

function reportedUsage(value: unknown): TokenUsage {
  if (typeof value !== "object" || value === null) return {};
  const data = value as Record<string, unknown>, out: TokenUsage = {};
  const fields: [keyof TokenUsage, string][] = [
    ["inputTokens", "input_tokens"], ["outputTokens", "output_tokens"],
    ["cacheReadTokens", "cache_read_tokens"], ["cacheWriteTokens", "cache_write_tokens"],
    ["totalTokens", "total_tokens"], ["reportedCost", "reported_cost"],
  ];
  for (const [target, source] of fields) {
    const number = numOrUndef(data[source]);
    if (number !== undefined && number >= 0) out[target] = number;
  }
  return out;
}
function addUsage(target: TokenUsage, usage: TokenUsage): void {
  for (const key of ["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "totalTokens", "reportedCost"] as const) {
    const value = usage[key];
    if (value !== undefined) target[key] = (target[key] ?? 0) + value;
  }
}
/** Provider-reported usage grouped by run/turn. Unknown metadata is discarded. */
export function usageMetrics(events: TaskEvent[]): UsageMetrics {
  const task: TokenUsage = {}, turns: TurnUsage[] = [];
  let current: TurnUsage | null = null, cumulative = 0;
  const ensureTurn = (): TurnUsage => {
    if (!current) {
      current = { turn: turns.length + 1, calls: 0, profile: "" };
      turns.push(current);
    }
    return current;
  };
  for (const event of events) {
    const data = event.data ?? {};
    if (event.kind === "running") {
      current = { turn: turns.length + 1, calls: 0, profile: "" };
      turns.push(current); cumulative = 0;
      continue;
    }
    if (event.kind === "agent_started") {
      const model = typeof data.model === "object" && data.model !== null ? data.model as Record<string, unknown> : {};
      ensureTurn().profile = textOf(model.profile, 120) || textOf(data.profile, 120);
      continue;
    }
    if (event.kind !== "agent_model" || data.phase !== "end") continue;
    const usage = reportedUsage(data.usage);
    const reportedTotal = numOrUndef(data.usage_tokens);
    if (reportedTotal !== undefined && reportedTotal >= 0) {
      // usage_tokens is a running total that stays 0 when the provider reports nothing.
      if (usage.totalTokens === undefined && reportedTotal > cumulative) usage.totalTokens = reportedTotal - cumulative;
      cumulative = reportedTotal;
    }
    if (!Object.keys(usage).length) continue;
    const turn = ensureTurn();
    turn.calls += 1;
    addUsage(turn, usage); addUsage(task, usage);
  }
  return { context: contextUsage(events), task, turns: turns.filter(turn => turn.calls > 0) };
}
export interface GoalView { status: string; turns: number; maxTurns: number; objective: string; reason: string }
export function goalState(events: TaskEvent[]): GoalView | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (!event || event.kind !== "goal_status") continue;
    const d = event.data ?? {};
    return { status: textOf(d.status, 20), turns: numOrUndef(d.turns) ?? 0, maxTurns: numOrUndef(d.max_turns) ?? 0, objective: textOf(d.objective, 2000), reason: textOf(d.reason, 2000) };
  }
  return null;
}
/** Approval requests that are still waiting for a decision. */
export function openConfirmations(events: TaskEvent[]): string[] {
  const open = new Set<string>();
  for (const event of events) {
    const id = textOf(event.data?.id, 200);
    if (event.kind === "confirmation_requested" && id) open.add(id);
    else if (event.kind === "confirmation_resolved" && id) open.delete(id);
    else if (isTerminal(event.kind)) open.clear();
  }
  return [...open];
}
export function attachmentList(value: unknown): AttachmentMeta[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    if (typeof item !== "object" || item === null) return [];
    const r = item as Record<string, unknown>;
    const id = textOf(r.id, 64), name = textOf(r.name, 255), type = textOf(r.type, 100);
    return /^[0-9a-f]{32}$/.test(id) && name ? [{ id, name, type, size: numOrUndef(r.size) ?? 0 }] : [];
  });
}

// =====================================================================
// M5 — conversation/message stream. Transforms the persisted event
// history into a Claude-Desktop/OpenCode-style chat: the objective opens
// as a user message; agent events become assistant/tool messages with
// expandable evidence cards. Untrusted event data is never executed.
// =====================================================================
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;  // excludes \t \n \r which are valid text
function textOf(value: unknown, max = 8000): string {
  if (typeof value !== "string" || !value || CONTROL.test(value)) return "";
  return value.length > max ? value.slice(0, max) + "\n…(truncated)" : value;
}
function numOrUndef(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Map a persisted event to one or more chat messages. */
export function eventToMessages(event: TaskEvent, objective: string): ChatMessage[] {
  const { seq, time, kind, data } = event;
  const d = (data ?? {}) as Record<string, unknown>;
  const base = { time, kind, id: `tool-${seq}` };
  switch (kind) {
    case "queued":
      return [{ id: `user-${seq}`, role: "user", time, kind, title: "Objective", body: objective }];
    case "user_message": {
      const text = textOf(d.text, 4000);
      if (d.goal === true && text) return [{ ...base, role: "system", kind: "goal_turn", title: "Next goal turn", body: text }];
      return text ? [{ id: `user-${seq}`, role: "user", time, kind, title: d.queued === true ? "Queued" : "Message", body: text }] : [];
    }
    case "agent_tool": {
      const name = textOf(d.name, 120) || "tool";
      if (d.phase === "start") {
        const args = d.arguments;
        return [{ ...base, role: "tool", kind: "agent_tool_call", title: `Tool call: ${name}`, details: typeof args === "object" && args !== null ? args : undefined }];
      }
      if (d.phase === "end") {
        return [{ ...base, role: "tool", kind: "agent_tool_result", title: `Tool result: ${name}`, body: observationText(name, d.observation), ...(d.failed === true ? { state: "tool_failed" } : {}) }];
      }
      return [];
    }
    case "agent_model":
      return d.phase === "error" ? [{ ...base, role: "system", title: "Model request failed", details: { error: textOf(d.error) } }] : [];
    case "agent_check":
      return [{ ...base, role: "tool", kind: "syntax", title: `Check: ${textOf(d.kind, 60) || "check"}`, details: d }];
    case "plan":
      return [{ ...base, role: "assistant", title: "Plan", details: d.steps ?? d }];
    case "inspected":
      return [{ ...base, role: "tool", title: "Inspected repository", details: { file_count: d.file_count, manifests: d.manifests } }];
    case "proposal":
      return [{ ...base, role: "assistant", title: "Proposed edits", details: d }];
    case "agent_started":
      return [{ ...base, role: "assistant", title: "Started tool-using agent", details: d }];
    case "agent_thinking": {
      const content = textOf(d.content, 16000);
      return content ? [{ ...base, role: "tool", kind: "agent_thinking", title: "Thinking", body: content }] : [];
    }
    case "agent_message": {
      const content = textOf(d.content ?? d.message ?? d.text);
      return content ? [{ ...base, role: "assistant", title: "Agent", body: content, details: d.usage }] : [];
    }
    case "agent_tool_call": {
      const name = textOf(d.name ?? d.tool, 120);
      const args = d.arguments ?? d.args;
      return [{ ...base, role: "tool", title: `Tool call: ${name || "tool"}`, body: typeof args === "string" ? textOf(args) : "", details: typeof args === "object" && args !== null ? args : undefined }];
    }
    case "agent_tool_result": {
      const toolName = textOf(d.tool ?? d.name, 120);
      const result = d.result ?? d.output ?? d.data;
      const summary = typeof result === "string" ? textOf(result, 2000) : JSON.stringify(result ?? d).slice(0, 2000);
      return [{ ...base, role: "tool", title: `Tool result: ${toolName || "tool"}`, body: summary }];
    }
    case "agent_repair":
      return [{ ...base, role: "tool", title: "Repair attempt", details: { attempt: d.attempt, reason: textOf(d.reason) } }];
    case "agent_finished":
      return [{ ...base, role: "assistant", title: "Agent finished", details: { state: d.state } }];
    case "execution":
      return [{ ...base, role: "tool", title: "Running tests in Docker", details: { runner: textOf(d.runner), command: textOf(d.command), timeout_seconds: numOrUndef(d.timeout_seconds) } }];
    case "syntax":
      return [{ ...base, role: "tool", title: `Syntax check: ${textOf(d.kind, 60) || "python"}`, details: { status: d.status, note: textOf(d.note) } }];
    case "mcp": {
      const servers = Array.isArray(d.servers) ? d.servers.filter((n): n is string => typeof n === "string") : [];
      const errors = Array.isArray(d.errors) ? d.errors.map(e => typeof e === "object" && e !== null ? `${String((e as Record<string, unknown>).server)}: ${String((e as Record<string, unknown>).error)}` : "").filter(Boolean) : [];
      return servers.length ? [{ ...base, role: "system", title: "Connected MCP servers", body: servers.join("\n"), details: { tools: d.tools, errors } }] : [];
    }
    case "skills": {
      const names = Array.isArray(d.names) ? d.names.filter((n): n is string => typeof n === "string") : [];
      return names.length ? [{ ...base, role: "system", title: "Using skills", body: names.join("\n") }] : [];
    }
    case "applied": {
      const files = Array.isArray(d.files) ? d.files.filter((f): f is string => typeof f === "string") : [];
      return [{ ...base, role: "system", title: files.length ? "Applied to workspace" : "Already applied", body: files.join("\n"), details: d }];
    }
    case "cancellation_requested":
      return [{ ...base, role: "system", title: "Cancellation requested" }];
    case "interrupt_requested":
      return [{ ...base, role: "system", title: "Stopping the current action" }];
    case "recovery_cleanup":
      return [{ ...base, role: "system", title: "Recovered after restart", details: { warning: textOf(d.warning) } }];
    case "terminal_command":
    case "terminal_output":
      return [];  // rendered in the read-only Terminal panel, never duplicated into chat
    case "browser_started":
      return [{ ...base, role: "tool", title: "Opening public web page", body: textOf(d.url, 4096) }];
    case "browser_result":
      return [{ ...base, role: "tool", title: "Browser capture", details: d }];
    case 'plan_updated':
      return [{ ...base, role: 'assistant', title: 'PLAN.md', body: textOf(d.content, 32768), details: d }];
    case 'planner_initialized':
      return [{ ...base, role: 'system', title: 'Planning agent initialized' }];
    case 'task_tracker':
    case 'plan_handoff':
      return [];
    case 'draft':
      return [];
    case "attachments": {
      const files = attachmentList(d.files);
      return files.length ? [{ id: `user-${seq}`, role: "user", time, kind, title: "Attachments", details: files }] : [];
    }
    case "confirmation_requested":
      return [{ ...base, role: "system", title: "Approval needed", details: { id: textOf(d.id, 200), tool: textOf(d.tool, 120), risk: textOf(d.risk, 20), reason: textOf(d.reason, 500), arguments: d.arguments } }];
    case "confirmation_resolved":
      return [{ ...base, role: "system", title: d.accepted === true ? "Approved" : "Rejected", details: { id: textOf(d.id, 200), accepted: d.accepted === true, reason: textOf(d.reason, 500) } }];
    case "hook_blocked":
      return [{ ...base, role: "system", title: "Blocked by a project hook", body: textOf(d.message, 1000), details: { tool: textOf(d.tool, 120) } }];
    case "subagent":
      return d.phase === "end"
        ? [{ ...base, role: "tool", title: `${textOf(d.agent, 64) || "Subagent"} reported back`, body: textOf(d.findings, 4000), details: { agent: textOf(d.agent, 64), state: textOf(d.state, 40), tool_calls: numOrUndef(d.tool_calls) } }]
        : [{ ...base, role: "tool", title: `Asked ${textOf(d.agent, 64) || "a subagent"}`, body: textOf(d.task, 500), details: { agent: textOf(d.agent, 64) } }];
    case "condensed":
      return [{ ...base, role: "system", title: "Condensed the earlier conversation", body: textOf(d.summary, 8000) }];
    case "resumed":
      return [{ ...base, role: "system", title: "Resumed", details: { from_state: textOf(d.from_state, 40) } }];
    case "pause_requested":
      return [{ ...base, role: "system", title: "Pausing after this step" }];
    case "forked":
      return [{ ...base, role: "system", title: "Forked from another conversation", details: { from_task: textOf(d.from_task, 64), from_seq: numOrUndef(d.from_seq) } }];
    case "secret_requested":
      return [{ ...base, role: "system", title: "The agent needs a secret", body: textOf(d.reason, 300), details: { id: textOf(d.id, 64), name: textOf(d.name, 64) } }];
    case "secret_answered":
      return [{ ...base, role: "system", title: d.provided === true ? `Secret ${textOf(d.name, 64)} provided` : `Secret ${textOf(d.name, 64)} not provided`, details: { id: textOf(d.id, 64), provided: d.provided === true } }];
    case "model_routed":
      return [{ ...base, role: "system", title: `Routed to ${textOf(d.profile, 64) || "a model"}`, body: textOf(d.reason, 200), details: { router: textOf(d.router, 64) } }];
    case "critic":
      return [{ ...base, role: "system", title: d.error ? "Critic unavailable" : d.achieved === true ? "Critic: looks done" : "Critic: not done yet",
        body: textOf(d.error ?? [d.reason, d.next].filter(item => typeof item === "string" && item).join("\n"), 2000) }];
    case "sandbox_secrets": {
      const names = Array.isArray(d.names) ? d.names.filter((n): n is string => typeof n === "string") : [];
      return [{ ...base, role: "system", title: "Sandbox secrets available", body: names.join(", ") }];
    }
    case "goal_status": {
      const turns = numOrUndef(d.turns) ?? 0, max = numOrUndef(d.max_turns) ?? 0;
      const titles: Record<string, string> = { active: "Goal in progress", achieved: "Goal achieved", capped: "Goal stopped at its turn limit", stopped: "Goal stopped", error: "Goal check failed" };
      return [{ ...base, role: "system", title: `${titles[textOf(d.status, 20)] ?? "Goal"} · ${turns}/${max}`, body: textOf(d.reason, 2000), details: { status: textOf(d.status, 20), objective: textOf(d.objective, 2000) } }];
    }
    case "completed":
    case "failed":
    case "blocked":
    case "cancelled":
    case "interrupted":
    case "paused":
      return [{ id: `terminal-${seq}`, role: "system", time, kind, title: `Task ${kind}`, state: kind, details: d }];
    default:
      return [];  // unknown event kinds are ignored, not executed
  }
}

/** Build a safe read-only terminal transcript from durable task events. */
export function terminalTranscript(events: TaskEvent[]): TerminalView {
  const commands = new Map<string, TerminalCommandView>();
  const order: string[] = [];
  let total = 0;
  for (const event of events) {
    if (event.kind !== "terminal_command" && event.kind !== "terminal_output") continue;
    const data = event.data ?? {};
    const id = textOf(data.command_id, 80);
    if (!id) continue;
    if (event.kind === "terminal_command") {
      const command = textOf(data.command, 4000);
      if (!command || commands.has(id)) continue;
      commands.set(id, { id, command, output: "", running: true });
      order.push(id);
      continue;
    }
    const row = commands.get(id);
    if (!row) continue;
    const chunk = textOf(data.output, 1024 * 1024);
    if (chunk && total < 1024 * 1024) {
      const kept = chunk.slice(0, 1024 * 1024 - total);
      row.output += kept;
      total += kept.length;
    }
    if (data.final === true) {
      row.running = false;
      if (data.exit_code === null || typeof data.exit_code === "number") row.exitCode = data.exit_code as number | null;
      const status = textOf(data.status, 80);
      if (status) row.status = status;
    }
  }
  const list = order.map(id => commands.get(id)!).filter(Boolean);
  const text = list.map(command => {
    const ending = command.running ? "\n[running]"
      : command.status && !["passed", "failed"].includes(command.status) ? `\n[${command.status}]`
      : command.exitCode !== undefined && command.exitCode !== null && command.exitCode !== 0 ? `\n[exit ${command.exitCode}]`
      : command.status === "failed" ? "\n[failed]" : "";
    return `$ ${command.command}\n${command.output}${ending}`;
  }).join("\n\n");
  return { commands: list, text };
}

export interface BrowserView { url: string; title: string; text: string; screenshot: string; status: string; error: string }
export interface PlanView { task_id: string; planner_id: string | null; content: string; revision: number; built_revision: number }
export function parsePlan(value: unknown): PlanView | null {
  if (typeof value !== 'object' || value === null) return null;
  const row = value as Record<string, unknown>;
  if (typeof row.task_id !== 'string' || !/^[a-f0-9]{32}$/.test(row.task_id) || (row.planner_id !== null && (typeof row.planner_id !== 'string' || !/^[a-f0-9]{32}$/.test(row.planner_id))) ||
      typeof row.content !== 'string' || row.content.length > 32768 || typeof row.revision !== 'number' || !Number.isSafeInteger(row.revision) || row.revision < 0 ||
      typeof row.built_revision !== 'number' || !Number.isSafeInteger(row.built_revision) || row.built_revision < 0 || row.built_revision > row.revision) return null;
  return { task_id: row.task_id, planner_id: row.planner_id, content: row.content, revision: row.revision, built_revision: row.built_revision };
}
export interface ChecklistItem { id: string; title: string; status: 'todo' | 'in_progress' | 'done' }
export function taskChecklist(events: TaskEvent[]): { used: boolean; tasks: ChecklistItem[] } {
  let tasks: ChecklistItem[] = [], used = false;
  for (const event of events) {
    if (event.kind !== 'task_tracker') continue;
    const items = event.data?.tasks;
    if (!Array.isArray(items) || items.length > 50) continue;
    const next: ChecklistItem[] = [];
    for (const item of items) {
      if (typeof item !== 'object' || item === null) continue;
      const row = item as Record<string, unknown>;
      if (typeof row.id === 'string' && typeof row.title === 'string' && row.title.length <= 300 && ['todo','in_progress','done'].includes(String(row.status))) next.push({ id: row.id, title: textOf(row.title, 300), status: row.status as ChecklistItem['status'] });
    }
    if (next.length === items.length) { used = true; tasks = next; }
  }
  return { used, tasks };
}
export function browserView(events: TaskEvent[]): BrowserView {
  const view = { url: "", title: "", text: "", screenshot: "", status: "", error: "" };
  for (const event of events) {
    const data = event.data ?? {};
    if (event.kind === "browser_started") { view.status = "running"; view.error = ""; continue; }
    if (event.kind !== "browser_result") continue;
    view.status = textOf(data.status, 80);
    view.error = textOf(data.error, 1000);
    const screenshot = textOf(data.screenshot, 200);
    let url = "";
    try {
      const parsed = new URL(textOf(data.url, 4096));
      if (["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password) url = parsed.href;
    } catch { /* malformed data cannot become a link */ }
    if (view.status === "passed" && url && /^\/api\/tasks\/[a-f0-9]{32}\/browser\/screenshots\/[a-f0-9]{32}$/.test(screenshot)) {
      view.url = url; view.screenshot = screenshot;
      view.title = textOf(data.title, 256); view.text = textOf(data.text, 12000);
    }
  }
  return view;
}

/** Readable text for a tool observation; falls back to JSON for unknown shapes. */
export function observationText(tool: string, observation: unknown): string {
  if (typeof observation !== "object" || observation === null) return textOf(observation, 8000);
  const o = observation as Record<string, unknown>;
  if (typeof o.error === "string" && tool !== "run_command") return `Error: ${textOf(o.error, 2000)}`;
  if (o.truncated === true && typeof o.preview === "string") return textOf(o.preview, 8000) + "\n…(truncated)";
  const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  switch (tool) {
    case "read_file": return textOf(strings(o.lines).join("\n"), 8000);
    case "list_files": return textOf(strings(o.files).join("\n"), 8000) || "(no files)";
    case "search_text": {
      const hits = Array.isArray(o.matches) ? o.matches : [];
      const lines = hits.map(hit => typeof hit === "object" && hit !== null ? `${String((hit as Record<string, unknown>).path)}:${String((hit as Record<string, unknown>).line)}: ${String((hit as Record<string, unknown>).text)}` : "");
      return lines.length ? textOf(lines.join("\n") + (o.truncated === true ? "\n…(more matches)" : ""), 8000) : "(no matches)";
    }
    case "edit_file": return typeof o.edited === "string" ? `Edited ${o.edited}` : textOf(JSON.stringify(o, null, 2), 8000);
    case "create_file": return typeof o.created === "string" ? `Created ${o.created}` : textOf(JSON.stringify(o, null, 2), 8000);
    case "run_tests": {
      const checks = Array.isArray(o.checks) ? o.checks : [];
      return checks.map(check => {
        const c = (typeof check === "object" && check !== null ? check : {}) as Record<string, unknown>;
        const output = typeof c.output === "string" && c.output ? `\n${c.output}` : "";
        return `${String(c.kind)}: ${String(c.status)}${output}`;
      }).join("\n\n") || textOf(JSON.stringify(o, null, 2), 8000);
    }
    case "run_command": {
      if (o.status === undefined) return `Error: ${textOf(o.error, 2000)}`;  // rejected before it ran
      const lines = [`exit ${o.exit_code === null || o.exit_code === undefined ? "–" : String(o.exit_code)} · ${String(o.status)}`];
      const changed = strings(o.changed_files), rejected = strings(o.rejected_changes);
      if (changed.length) lines.push(`Kept changes: ${changed.join(", ")}`);
      if (rejected.length) lines.push(`Rejected changes: ${rejected.join(", ")}`);
      if (typeof o.note === "string") lines.push(o.note);
      if (typeof o.error === "string") lines.push(o.error);
      if (typeof o.output === "string" && o.output) lines.push("", o.output);
      return textOf(lines.join("\n"), 12000);
    }
    case "finish": return "Finished";
    default: return textOf(JSON.stringify(o, null, 2), 8000);
  }
}

/** Build the full chat message list from a task's events. */
export function eventsToChat(events: TaskEvent[], objective: string): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (const event of events) messages.push(...eventToMessages(event, objective));
  return messages;
}

/** A short label for the conversation list (sidebar). */
/** One line, at most `max` characters, cut at a word where possible and ended with "…". */
export function shortLabel(value: unknown, max: number): string {
  if (typeof value !== "string" || CONTROL.test(value)) return "";
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return (space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd() + "…";
}

export function conversationLabel(task: { id: string; state: string; objective?: string; title?: string }): string {
  return shortLabel(task.title, 200) || shortLabel(task.objective, 60) || task.id.slice(0, 8);
}

/** A Markdown transcript of a conversation, built only from persisted events. */
export function transcriptMarkdown(title: string, messages: ChatMessage[], diff: string): string {
  const out = [`# ${title}`, ""];
  for (const msg of messages) {
    const when = new Date(msg.time * 1000).toISOString();
    if (msg.role === "user") out.push(`## You · ${when}`, "", msg.body ?? "", "");
    else if (msg.role === "assistant" && msg.body) out.push(`## Dikdik · ${when}`, "", msg.body, "");
    else if (msg.state && msg.kind !== "agent_tool_result") out.push(`### ${msg.title}`, "", "```json", JSON.stringify(msg.details ?? {}, null, 2), "```", "");
    else {
      out.push(`- **${msg.title}**`);
      if (msg.body) out.push("", "  ```", ...msg.body.split("\n").map(line => "  " + line), "  ```");
    }
  }
  if (diff) out.push("", "## Changes", "", "```diff", diff.trimEnd(), "```", "");
  return out.join("\n");
}

/** Providers accepted by POST /api/profiles (validation mirrors the server). */
export const PROFILE_PROVIDERS = ["openai", "anthropic", "azure", "local"] as const;
export type ProfileProvider = (typeof PROFILE_PROVIDERS)[number];
/** Skill scopes served by GET /api/skills. */
export type SkillScope = "personal" | "project";
/** Times come back as epoch seconds in Python, or ISO strings elsewhere; both display. */
export type EpochOrIso = number | string;

export interface Profile {
  id: string; name: string; provider: ProfileProvider; model: string;
  endpoint: string; timeout_seconds: number; is_default: boolean;
}
export interface ProfileCreateInput {
  name: string; provider: ProfileProvider; model: string;
  endpoint?: string; timeout_seconds?: number; credential_secret_ref?: string;
}
export interface ProfilePatch { model?: string; endpoint?: string; timeout_seconds?: number; is_default?: boolean; credential_secret_ref?: string }
/** GET /api/model: what the next model or agent task will use. Never includes a key. */
export interface ActiveModel {
  configured: boolean; source: "profile" | "environment" | "none";
  profile?: string; provider?: string; provider_name?: string; model?: string; host?: string; problem?: string;
}
export interface CreatedId { id: string }

export interface SecretRefEntry { ref: string; set_at: EpochOrIso | null }
export interface SecretSetInput { ref: string; plaintext: string }
export interface SecretSetResult { ref: string; set: true }

export interface Skill { id: string; name: string; scope: SkillScope; enabled: boolean; path: string; description?: string }
export interface SkillToggle { enabled: boolean }

export type AutomationSchedule = { interval_seconds: number } | { daily_at: string } |
  { cron: string; timezone: string } | { event: { type: "git_commit"; repository: string; ref: string } };
export interface AutomationRunSettings { mode: "model" | "agent"; verification: Verification; approve_external: boolean; approve_execution: boolean; agent_profile?: string }
export interface Automation {
  id: string; name: string; schedule: AutomationSchedule; workspace_path: string;
  objective: string; enabled: boolean; last_state: string | null;
  next_run: EpochOrIso | null; failures: number;
  run: AutomationRunSettings | null; last_task_id: string | null;
  repositories: string[]; timeout_seconds: number; source: "custom" | "template" | "import"; created: number; updated: number;
}
export interface AutomationInput {
  name: string; schedule: AutomationSchedule; objective: string;
  mode: "model" | "agent"; verification: Verification; approve_external: boolean; approve_execution: boolean; agent_profile?: string;
  repositories?: string[]; timeout_seconds?: number; source?: "custom" | "template" | "import";
}
export interface AutomationRun { id: string; state: string; created: number | null }

// --- mandatory status labels (single source of truth for all panels) ---
export const M4_TEXT = {
  contractStatus: "tested: contract",
  contractNote: "Contract-tested only; until a live run is recorded in docs/VERIFICATION.md, no provider has been verified against the real API.",
  credentialsNote: "Every model and agent task uses the default profile. Store the provider's key under API keys, then enter that key's name on the profile; the key itself is never displayed or returned.",
  secretsNote: "Values are encrypted at rest and never returned. After a successful set the plaintext field is cleared and the value is never shown again.",
  skillsNote: "Skill text is untrusted data; enabling a skill never changes permissions.",
  refreshProfiles: "Refresh profiles",
  refreshSecrets: "Refresh secret refs",
  refreshSkills: "Refresh skills",
  emptyProfiles: "No profiles loaded yet. Refresh to load the persisted list.",
  emptySecrets: "No secret refs set yet. Refresh to load.",
  emptySkills: "No skills found. Refresh to re-scan personal and project skill files.",
  createProfile: "Create profile",
  profileCreated: "Profile created.",
  profileSaved: "Profile updated; server list re-fetched.",
  profileDeleted: "Profile deleted; server list re-fetched.",
  setSecret: "Set secret",
  secretSetConfirmation: "Server confirmed the secret was stored (set: true). The plaintext is not displayed again.",
  secretDeleted: "Secret ref deleted.",
  skillToggleSaved: "Skill updated.",
  delete: "Delete",
  deleteConfirm: "Confirm delete",
  keep: "Keep",
  edit: "Edit",
  cancelEdit: "Cancel",
  save: "Save",
  defaultBadge: "default",
  genericFailure: "Request failed.",
  intervalRule: "Enter how many minutes apart runs should be (a whole number, at least 1).",
  timeoutRule: "Timeout must be a whole number between 1 and 600 seconds.",
  secretRefRule: "Ref must start with a letter or digit and use only letters, digits, dots, underscores, or dashes.",
  plaintextRequired: "Enter a secret value.",
  secretHiddenReply: "The secret could not be set. The value is never echoed back; try again or use a different ref.",
  unsafeUrlSegment: "This identifier cannot be used in a request URL (empty or contains control characters).",
} as const;

// --- safe request paths -------------------------------------------------
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

/** URL-safe path segment: rejects empty values and control characters (incl. newlines). */
export function safeUrlSegment(value: unknown, label = "identifier"): string {
  if (typeof value !== "string" || !value || CONTROL_CHARS.test(value)) {
    throw new Error(`${M4_TEXT.unsafeUrlSegment} (${label})`);
  }
  return encodeURIComponent(value);
}

/** Builds /api/... paths with validated, percent-encoded segments. */
export function apiUrl(base: string, ...segments: string[]): string {
  safeUrlSegment(base, "endpoint base");
  return `${base}/${segments.map(segment => safeUrlSegment(segment)).join("/")}`;
}

export function deleteProfileUrl(id: string, confirmDefault: boolean): string {
  return apiUrl("/api/profiles", id) + (confirmDefault ? "?confirm_default=1" : "");
}

// --- request builders (client-side mirroring of the frozen contract) ------
const FIXED_API_ENDPOINTS = ["https://api.openai.com/v1", "https://api.anthropic.com/v1", "https://mojave.cloud/v1"];
const LOOPBACK_ENDPOINT = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/;

export function validateEndpoint(value: string): string {
  let parsed: URL;
  try { parsed = new URL(value); } catch { throw new Error("Endpoint must be an absolute http(s) URL."); }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error("Endpoint must use http or https.");
  if (FIXED_API_ENDPOINTS.includes(value.replace(/\/+$/, ""))) return value;
  if (!LOOPBACK_ENDPOINT.test(value)) throw new Error("Endpoint must be a loopback URL or one of the known provider API bases.");
  return value;
}

export function parseTimeoutSeconds(value: string | number | undefined): number | undefined {
  if (value === undefined || value === "") return undefined;
  const seconds = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 600) throw new Error(M4_TEXT.timeoutRule);
  return seconds;
}

const SECRET_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function buildProfileCreate(input: {
  name: string; provider: string; model: string;
  endpoint?: string; timeoutSeconds?: string | number; credentialSecretRef?: string;
}): ProfileCreateInput {
  const name = input.name.trim();
  if (!name) throw new Error("Enter a profile name.");
  if (!(PROFILE_PROVIDERS as readonly string[]).includes(input.provider)) throw new Error("Choose a supported provider.");
  const model = input.model.trim();
  if (!model) throw new Error("Enter a model name.");
  const endpoint = (input.endpoint ?? "").trim();
  if (endpoint) validateEndpoint(endpoint);
  const timeout_seconds = parseTimeoutSeconds(input.timeoutSeconds);
  const credential_secret_ref = (input.credentialSecretRef ?? "").trim();
  if (credential_secret_ref && !SECRET_REF_PATTERN.test(credential_secret_ref)) throw new Error(M4_TEXT.secretRefRule);
  const body: ProfileCreateInput = { name, provider: input.provider as ProfileProvider, model, ...(endpoint ? { endpoint } : {}), ...(timeout_seconds !== undefined ? { timeout_seconds } : {}), ...(credential_secret_ref ? { credential_secret_ref } : {}) };
  return body;
}

export function buildProfilePatch(input: {
  model?: string; endpoint?: string; timeoutSeconds?: string | number; isDefault?: boolean; credentialSecretRef?: string;
}): ProfilePatch {
  const patch: ProfilePatch = {};
  if (input.model !== undefined) {
    const model = input.model.trim();
    if (!model) throw new Error("Enter a model name to save.");
    patch.model = model;
  }
  if (input.endpoint !== undefined) {
    const endpoint = input.endpoint.trim();
    if (endpoint) validateEndpoint(endpoint);
    patch.endpoint = endpoint;
  }
  const timeout = parseTimeoutSeconds(input.timeoutSeconds);
  if (timeout !== undefined) patch.timeout_seconds = timeout;
  if (input.isDefault !== undefined) patch.is_default = input.isDefault;
  // Blank keeps the current key binding; the server never reveals which name is bound.
  const ref = input.credentialSecretRef?.trim();
  if (ref) {
    if (!SECRET_REF_PATTERN.test(ref)) throw new Error(M4_TEXT.secretRefRule);
    patch.credential_secret_ref = ref;
  }
  if (Object.keys(patch).length === 0) throw new Error("Nothing to update for this profile.");
  return patch;
}

export function buildSecretInput(input: { ref: string; plaintext: string }): SecretSetInput {
  const ref = input.ref.trim();
  if (!SECRET_REF_PATTERN.test(ref)) throw new Error(M4_TEXT.secretRefRule);
  if (input.plaintext === "") throw new Error(M4_TEXT.plaintextRequired);
  if (input.plaintext.length > 64 * 1024) throw new Error("Secret exceeds the 64KB request limit.");
  return { ref, plaintext: input.plaintext };
}

/** An automation's settings. Its runs use exactly the mode and approvals given here. */
export function buildAutomation(input: {
  name: string; objective: string; scheduleKind: "interval" | "daily" | "cron" | "event"; intervalMinutes?: string | number; dailyAt?: string;
  cron?: string; timezone?: string; eventRepository?: string; eventRef?: string; repositories?: string; timeoutSeconds?: string | number; source?: "custom" | "template";
  mode: string; verification: string; approveExternal: boolean; approveExecution: boolean; agentProfile?: string;
}): AutomationInput {
  const name = input.name.trim();
  if (!name) throw new Error("Enter an automation name.");
  const objective = input.objective.trim();
  if (!objective) throw new Error("Enter an objective.");
  let schedule: AutomationSchedule;
  if (input.scheduleKind === "daily") {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(input.dailyAt ?? "")) throw new Error("Pick a time of day.");
    schedule = { daily_at: input.dailyAt as string };
  } else if (input.scheduleKind === "interval") {
    const minutes = Number(String(input.intervalMinutes ?? "").trim());
    if (!Number.isInteger(minutes) || minutes < 1) throw new Error(M4_TEXT.intervalRule);
    schedule = { interval_seconds: minutes * 60 };
  } else if (input.scheduleKind === "cron") {
    const cron = (input.cron ?? "").trim(), timezone = (input.timezone ?? "local").trim() || "local";
    if (cron.split(/\s+/).length !== 5) throw new Error("Enter a five-field cron schedule.");
    if (timezone.length > 80) throw new Error("Time zone is too long.");
    schedule = { cron, timezone };
  } else {
    const repository = (input.eventRepository ?? ".").trim() || ".", ref = (input.eventRef ?? "HEAD").trim() || "HEAD";
    if (repository.length > 260 || ref.length > 200 || ref.startsWith("-")) throw new Error("Enter a safe repository path and Git revision.");
    schedule = { event: { type: "git_commit", repository, ref } };
  }
  if (input.mode !== "model" && input.mode !== "agent") throw new Error("Scheduled runs use agent or single-proposal mode.");
  const verification = parseVerification(input.verification);
  if (!input.approveExternal) throw new Error("Approve sending code to your model provider for these runs.");
  if (verification === "docker" && !input.approveExecution) throw new Error("Approve running tests in Docker for these runs.");
  const timeout = Number(String(input.timeoutSeconds ?? "1800").trim());
  if (!Number.isInteger(timeout) || timeout < 60 || timeout > 86400) throw new Error("Timeout must be 60–86400 seconds.");
  const repositories = (input.repositories ?? "").split("\n").map(item => item.trim()).filter(Boolean);
  return {
    name, schedule, objective, mode: input.mode, verification, approve_external: true, approve_execution: verification === "docker",
    timeout_seconds: timeout, ...(repositories.length ? { repositories } : {}), ...(input.source ? { source: input.source } : {}),
    ...(input.mode === "agent" && input.agentProfile ? { agent_profile: input.agentProfile } : {}),
  };
}

/** "every 2 hours", "daily at 07:30". */
export function describeSchedule(schedule: AutomationSchedule): string {
  if ("daily_at" in schedule) return `daily at ${schedule.daily_at}`;
  if ("cron" in schedule) return `${schedule.cron} (${schedule.timezone})`;
  if ("event" in schedule) return `when ${schedule.event.repository}@${schedule.event.ref} changes`;
  const minutes = Math.round(schedule.interval_seconds / 60);
  if (minutes % 1440 === 0) return minutes === 1440 ? "every day" : `every ${minutes / 1440} days`;
  if (minutes % 60 === 0) return minutes === 60 ? "every hour" : `every ${minutes / 60} hours`;
  return minutes === 1 ? "every minute" : `every ${minutes} minutes`;
}

export const buildSkillToggle = (enabled: boolean): SkillToggle => ({ enabled });

// --- response parsers (tolerate unknown extra fields; throw friendly errors) ---
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const safeText = (value: unknown): string =>
  typeof value === "string" && value !== "" && !CONTROL_CHARS.test(value) ? value : "";

export function parseProfiles(value: unknown): Profile[] {
  if (!Array.isArray(value)) throw new Error("Profile list response did not match the expected shape.");
  const profiles: Profile[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const id = safeText(item.id), name = safeText(item.name), model = safeText(item.model);
    const endpoint = item.endpoint === null ? "" : item.endpoint;
    const timeout = item.timeout_seconds === null ? 60 : item.timeout_seconds;
    const isDefault = item.is_default;
    if (!id || !name || !model || typeof endpoint !== "string" || typeof timeout !== "number" || !Number.isInteger(timeout) || typeof isDefault !== "boolean") continue;
    if (!(PROFILE_PROVIDERS as readonly string[]).includes(item.provider as string)) continue;
    profiles.push({ id, name, provider: item.provider as ProfileProvider, model, endpoint, timeout_seconds: timeout, is_default: isDefault });
  }
  return profiles;
}

export function parseSecretRefs(value: unknown): SecretRefEntry[] {
  if (!Array.isArray(value)) throw new Error("Secret list response did not match the expected shape.");
  const refs: SecretRefEntry[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const ref = safeText(item.ref);
    if (!ref) continue;
    const setAt = item.set_at;
    if (setAt !== null && typeof setAt !== "number" && typeof setAt !== "string") continue;
    refs.push({ ref, set_at: setAt as EpochOrIso | null });
  }
  return refs;
}

const SKILL_SCOPES: readonly SkillScope[] = ["personal", "project"];

export function parseSkills(value: unknown): Skill[] {
  if (!Array.isArray(value)) throw new Error("Skill list response did not match the expected shape.");
  const skills: Skill[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const id = safeText(item.id), name = safeText(item.name), path = item.path, enabled = item.enabled;
    if (!id || !name || enabled !== true && enabled !== false || typeof path !== "string" || CONTROL_CHARS.test(path)) continue;
    if (!(SKILL_SCOPES as readonly string[]).includes(item.scope as string)) continue;
    const description = safeText(item.description);
    skills.push({ id, name, scope: item.scope as SkillScope, enabled, path, ...(description ? { description } : {}) });
  }
  return skills;
}

export function parseAutomations(value: unknown): Automation[] {
  if (!Array.isArray(value)) throw new Error("Automation list response did not match the expected shape.");
  const automations: Automation[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const id = safeText(item.id), name = safeText(item.name), objective = item.objective, workspacePath = item.workspace_path;
    const enabled = item.enabled, lastState = item.last_state, nextRun = item.next_run, failures = item.failures;
    const raw = isRecord(item.schedule) ? item.schedule : {};
    const event = isRecord(raw.event) && raw.event.type === "git_commit" && typeof raw.event.repository === "string" && typeof raw.event.ref === "string"
      ? { event: { type: "git_commit" as const, repository: raw.event.repository, ref: raw.event.ref } } : null;
    const schedule: AutomationSchedule | null = typeof raw.daily_at === "string" ? { daily_at: raw.daily_at }
      : typeof raw.interval_seconds === "number" && Number.isInteger(raw.interval_seconds) ? { interval_seconds: raw.interval_seconds }
      : typeof raw.cron === "string" && typeof raw.timezone === "string" ? { cron: raw.cron, timezone: raw.timezone }
      : event;
    if (!id || !name || typeof objective !== "string" || objective === "" || typeof workspacePath !== "string" ||
        (enabled !== true && enabled !== false) || (lastState !== null && typeof lastState !== "string") ||
        (nextRun !== null && typeof nextRun !== "number" && typeof nextRun !== "string") ||
        typeof failures !== "number" || !Number.isInteger(failures) || schedule === null) continue;
    const run = isRecord(item.run) && (item.run.mode === "model" || item.run.mode === "agent") && (item.run.verification === "syntax" || item.run.verification === "docker")
      ? { mode: item.run.mode, verification: item.run.verification, approve_external: item.run.approve_external === true, approve_execution: item.run.approve_execution === true,
          ...(typeof item.run.agent_profile === "string" ? { agent_profile: item.run.agent_profile } : {}) } as AutomationRunSettings
      : null;
    const repositories = Array.isArray(item.repositories) && item.repositories.every(value => typeof value === "string") ? item.repositories as string[] : [workspacePath];
    const timeout = typeof item.timeout_seconds === "number" && Number.isInteger(item.timeout_seconds) ? item.timeout_seconds : 1800;
    const source = item.source === "template" || item.source === "import" ? item.source : "custom";
    automations.push({
      id, name, schedule, workspace_path: workspacePath, objective,
      enabled, last_state: lastState as string | null, next_run: nextRun as EpochOrIso | null, failures,
      run, last_task_id: safeText(item.last_task_id) || null,
      repositories, timeout_seconds: timeout, source,
      created: typeof item.created === "number" ? item.created : 0, updated: typeof item.updated === "number" ? item.updated : 0,
    });
  }
  return automations;
}

export function parseRunSummaries(value: unknown): AutomationRun[] {
  if (!Array.isArray(value)) throw new Error("Runs response did not match the expected shape.");
  const runs: AutomationRun[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    // The server names the run's conversation `task_id`.
    const id = safeText(item.task_id), state = safeText(item.state) || "deleted";
    if (!id) continue;
    runs.push({ id, state, created: typeof item.created === "number" ? item.created : null });
  }
  return runs;
}

export function parseCreatedId(value: unknown): CreatedId {
  if (!isRecord(value) || typeof value.id !== "string" || value.id === "" || CONTROL_CHARS.test(value.id)) {
    throw new Error("The server did not return a usable id for the new record.");
  }
  return { id: value.id };
}

export function parseSecretSet(value: unknown): SecretSetResult {
  if (!isRecord(value) || typeof value.ref !== "string" || value.ref === "" || value.set !== true) {
    throw new Error("The server did not confirm the secret was stored; treat it as not set.");
  }
  return { ref: value.ref, set: true };
}

/** Never lets an error message echo back the just-typed plaintext. */
export function sanitizeSecretError(error: unknown, plaintext: string): string {
  const message = error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
  return (plaintext.length >= 4 && message.includes(plaintext)) ? M4_TEXT.secretHiddenReply : message;
}

export function displayTimestamp(value: EpochOrIso | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    try { return new Date(value < 1e12 ? value * 1000 : value).toISOString().replace("T", " ").replace(".000Z", " UTC"); }
    catch { return String(value); }
  }
  if (typeof value === "string") return value;
  return String(value);
}

// --- panel DOM contract -------------------------------------------------
/** Minimal safe-DOM surface used by M4 panels; real DOM nodes satisfy it at runtime. */
export interface M4Node {
  textContent: string; className: string; hidden: boolean; disabled: boolean;
  checked: boolean; value: string; required: boolean; type: string;
  setAttribute(name: string, value: string): void;
  addEventListener(name: string, callback: (event: { preventDefault(): void }) => void): void;
  append(...children: M4Node[]): void;
  replaceChildren(...children: M4Node[]): void;
  onclick?: () => void;
  onchange?: () => void;
  focus?: () => void;
}
export type M4Factory = (tag: string) => M4Node;
export type M4PanelRequest = <T>(path: string, method: string, body?: unknown) => Promise<T>;
export interface M4PanelDom { root: M4Node; create: M4Factory; request: M4PanelRequest }
export interface M4PanelHandle { refresh(): Promise<void> }

function m4el(create: M4Factory, tag: string, className = "", text = ""): M4Node {
  const node = create(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function m4focus(node: M4Node): void {
  if (typeof node.focus === "function") node.focus();
}
function m4input(create: M4Factory, container: M4Node, kind: string, labelText: string, type: string, required: boolean): M4Node {
  const id = `m4-${kind}-${(m4FieldCounter++).toString(36)}`;
  const label = m4el(create, "label"); label.setAttribute("for", id); label.textContent = labelText;
  const input = m4el(create, "input"); input.type = type; input.required = required; input.setAttribute("id", id);
  container.append(label, input);
  return input;
}
function m4textarea(create: M4Factory, container: M4Node, kind: string, labelText: string, rows = 3): M4Node {
  const id = `m4-${kind}-${(m4FieldCounter++).toString(36)}`;
  const label = m4el(create, "label"); label.setAttribute("for", id); label.textContent = labelText;
  const area = m4el(create, "textarea"); area.setAttribute("rows", String(rows)); area.setAttribute("id", id);
  container.append(label, area);
  return area;
}
function m4select(create: M4Factory, container: M4Node, kind: string, labelText: string, options: readonly string[], selected: string): M4Node {
  const id = `m4-${kind}-${(m4FieldCounter++).toString(36)}`;
  const label = m4el(create, "label"); label.setAttribute("for", id); label.textContent = labelText;
  const select = m4el(create, "select"); select.setAttribute("id", id);
  const nodes = options.map(option => { const node = m4el(create, "option", "", option); node.value = option; return node; });
  select.append(...nodes);
  select.value = selected;
  container.append(label, select);
  return select;
}
function m4button(create: M4Factory, container: M4Node, labelText: string, type: string): M4Node {
  const button = m4el(create, "button", type === "submit" ? "" : "secondary", labelText);
  button.type = type;
  container.append(button);
  return button;
}
function m4toggler(create: M4Factory, container: M4Node, label: string, checked: boolean): M4Node {
  const box = m4el(create, "input");
  box.type = "checkbox";
  box.setAttribute("aria-label", label);
  box.checked = checked;
  container.append(box);
  return box;
}
let m4FieldCounter = 0;

// --- profiles panel -----------------------------------------------------
export function profilesPanel({ root, create, request }: M4PanelDom): M4PanelHandle {
  let profiles: Profile[] = [];
  let confirmId: string | null = null, editId: string | null = null, loading = false;

  const note = m4el(create, "p", "hint", M4_TEXT.credentialsNote);
  const verify = m4el(create, "p", "hint m4-mono", `${M4_TEXT.contractStatus} — ${M4_TEXT.contractNote}`);
  const form = m4el(create, "form");
  form.addEventListener("submit", event => { event.preventDefault(); void submitCreate(); });
  const nameField = m4input(create, form, "profile-name", "Name", "text", true);
  const providerField = m4select(create, form, "profile-provider", "Provider", PROFILE_PROVIDERS, "openai");
  const modelField = m4input(create, form, "profile-model", "Model", "text", true);
  const endpointField = m4input(create, form, "profile-endpoint", "Endpoint (optional)", "text", false);
  endpointField.setAttribute("placeholder", "https://api.openai.com/v1");
  const timeoutField = m4input(create, form, "profile-timeout", "Timeout seconds (optional, 1–600)", "number", false);
  const secretRefField = m4input(create, form, "profile-secret-ref", "API key name from API keys (optional for local)", "text", false);
  m4button(create, form, M4_TEXT.createProfile, "submit");
  const actions = m4el(create, "p", "row");
  const refreshButton = m4button(create, actions, M4_TEXT.refreshProfiles, "button");
  const statusLine = m4el(create, "p", "hint");
  statusLine.setAttribute("role", "status"); statusLine.setAttribute("aria-live", "polite");
  const errorLine = m4el(create, "p", "error");
  errorLine.setAttribute("role", "alert");
  const list = m4el(create, "ol", "m4-list");
  root.append(note, verify, form, actions, statusLine, errorLine, list);

  const failMessage = (error: unknown): string => error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
  const showStatus = (message: string): void => { statusLine.textContent = message; statusLine.hidden = false; };
  const showError = (message: string): void => { errorLine.textContent = message; errorLine.hidden = false; };
  const clearFeedback = (): void => { statusLine.hidden = true; showErrorReset(); };
  const showErrorReset = (): void => { errorLine.hidden = true; };

  const refresh = async (): Promise<void> => {
    if (loading) return;
    loading = true; refreshButton.disabled = true; clearFeedback();
    try {
      profiles = parseProfiles(await request<unknown>("/api/profiles", "GET"));
      confirmId = null; editId = null;
      renderList();
      if (!profiles.length) showStatus(M4_TEXT.emptyProfiles);
    } catch (error) { showError(failMessage(error)); }
    finally { loading = false; refreshButton.disabled = false; }
  };
  refreshButton.onclick = () => void refresh();

  const submitCreate = async (): Promise<void> => {
    clearFeedback();
    try {
      const created = parseCreatedId(await request<unknown>("/api/profiles", "POST", buildProfileCreate({
        name: nameField.value, provider: providerField.value, model: modelField.value,
        endpoint: endpointField.value, timeoutSeconds: timeoutField.value, credentialSecretRef: secretRefField.value,
      })));
      nameField.value = ""; modelField.value = ""; endpointField.value = ""; timeoutField.value = ""; secretRefField.value = "";
      providerField.value = "openai";
      editId = null; confirmId = null;
      showStatus(`${M4_TEXT.profileCreated} (${created.id.slice(0, 8)})`);
      renderList();
      await refresh();
    } catch (error) { showError(failMessage(error)); }
  };

  const saveEdit = async (id: string, model: string, endpoint: string, timeout: string, isDefault: boolean, secretRef: string): Promise<void> => {
    clearFeedback();
    try {
      const patch = buildProfilePatch({ model, endpoint, timeoutSeconds: timeout, isDefault: isDefault, credentialSecretRef: secretRef });
      const local = profiles.find(profile => profile.id === id);
      if (local) { // optimistic; server list re-fetched below
        if (patch.model !== undefined) local.model = patch.model;
        if (patch.endpoint !== undefined) local.endpoint = patch.endpoint;
        if (patch.timeout_seconds !== undefined) local.timeout_seconds = patch.timeout_seconds;
        if (patch.is_default !== undefined) local.is_default = patch.is_default;
      }
      editId = null; confirmId = null;
      renderList();
      await request<unknown>(apiUrl("/api/profiles", id), "PATCH", patch);
      showStatus(M4_TEXT.profileSaved);
      await refresh();
    } catch (error) { editId = null; showError(failMessage(error)); await refresh(); }
  };

  const removeProfile = async (id: string, isDefault: boolean): Promise<void> => {
    clearFeedback();
    try {
      await request<unknown>(deleteProfileUrl(id, isDefault), "DELETE");
      profiles = profiles.filter(profile => profile.id !== id);
      confirmId = null; renderList();
      showStatus(M4_TEXT.profileDeleted);
      await refresh();
    } catch (error) { confirmId = null; renderList(); showError(failMessage(error)); }
  };

  function renderList(): void {
    const rows: M4Node[] = [];
    for (const profile of profiles) {
      const row = m4el(create, "li", "m4-row");
      const head = m4el(create, "p", "m4-title");
      const title = m4el(create, "span", "m4-strong", `${profile.name} · ${profile.provider} · ${profile.model}`);
      head.append(title, m4el(create, "span", profile.is_default ? "m4-badge" : "m4-badge m4-dim", profile.is_default ? M4_TEXT.defaultBadge : ""));
      row.append(head);
      const meta = m4el(create, "p", "m4-meta", `${profile.endpoint || "no endpoint override"} · timeout ${profile.timeout_seconds}s`);
      row.append(meta, m4el(create, "span", "m4-status", M4_TEXT.contractStatus));
      const actionsRow = m4el(create, "span", "m4-actions");
      const editButton = m4el(create, "button", "secondary", M4_TEXT.edit);
      editButton.type = "button";
      editButton.onclick = () => { editId = profile.id; confirmId = null; renderList(); };
      const deleteButton = m4el(create, "button", confirmId === profile.id ? "danger" : "secondary", confirmId === profile.id ? M4_TEXT.deleteConfirm : M4_TEXT.delete);
      deleteButton.type = "button";
      deleteButton.setAttribute("aria-pressed", String(confirmId === profile.id));
      deleteButton.onclick = () => {
        if (confirmId !== profile.id) { confirmId = profile.id; editId = null; renderList(); }
        else void removeProfile(profile.id, profile.is_default);
      };
      actionsRow.append(editButton, deleteButton);
      if (confirmId === profile.id) {
        const keepButton = m4el(create, "button", "secondary", M4_TEXT.keep);
        keepButton.type = "button";
        keepButton.onclick = () => { confirmId = null; renderList(); };
        actionsRow.append(keepButton);
        m4focus(deleteButton);
      }
      row.append(actionsRow);
      if (editId === profile.id) {
        const editor = m4el(create, "form", "m4-editor");
        const modelEdit = m4input(create, editor, "edit-model", "Model", "text", true);
        modelEdit.value = profile.model;
        const endpointEdit = m4input(create, editor, "edit-endpoint", "Endpoint", "text", false);
        endpointEdit.value = profile.endpoint;
        const timeoutEdit = m4input(create, editor, "edit-timeout", "Timeout seconds", "number", false);
        timeoutEdit.value = String(profile.timeout_seconds);
        const keyEdit = m4input(create, editor, "edit-secret-ref", "API key name (leave blank to keep the current key)", "text", false);
        const defaultToggle = m4toggler(create, editor, "Set as default profile", profile.is_default);
        const saveButton = m4el(create, "button", "", M4_TEXT.save);
        saveButton.type = "submit";
        const cancelButton = m4el(create, "button", "secondary", M4_TEXT.cancelEdit);
        cancelButton.type = "button";
        cancelButton.onclick = () => { editId = null; renderList(); };
        editor.append(saveButton, cancelButton);
        editor.addEventListener("submit", event => { event.preventDefault(); void saveEdit(profile.id, modelEdit.value, endpointEdit.value, timeoutEdit.value, defaultToggle.checked, keyEdit.value); });
        row.append(editor);
        m4focus(modelEdit);
      }
      rows.push(row);
    }
    list.replaceChildren(...rows);
  }
  return { refresh };
}

// --- secrets panel --------------------------------------------------------
export function secretsPanel({ root, create, request }: M4PanelDom): M4PanelHandle {
  let refs: SecretRefEntry[] = [];
  let confirmId: string | null = null, loading = false;

  const note = m4el(create, "p", "hint", M4_TEXT.secretsNote);
  const form = m4el(create, "form");
  form.addEventListener("submit", event => { event.preventDefault(); void submitSet(); });
  const refField = m4input(create, form, "secret-ref", "Ref (letters, digits, . _ -)", "text", true);
  const plaintextField = m4input(create, form, "secret-value", "Secret value", "password", true);
  plaintextField.setAttribute("autocomplete", "new-password");
  m4button(create, form, M4_TEXT.setSecret, "submit");
  const actions = m4el(create, "p", "row");
  const refreshButton = m4button(create, actions, M4_TEXT.refreshSecrets, "button");
  const statusLine = m4el(create, "p", "hint");
  statusLine.setAttribute("role", "status"); statusLine.setAttribute("aria-live", "polite");
  const errorLine = m4el(create, "p", "error");
  errorLine.setAttribute("role", "alert");
  const list = m4el(create, "ol", "m4-list");
  root.append(note, form, actions, statusLine, errorLine, list);

  const failMessage = (error: unknown): string => error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
  const showStatus = (message: string): void => { statusLine.textContent = message; statusLine.hidden = false; };
  const showError = (message: string): void => { errorLine.textContent = message; errorLine.hidden = false; };
  const clearFeedback = (): void => { statusLine.hidden = true; errorLine.hidden = true; };

  const refresh = async (): Promise<void> => {
    if (loading) return;
    loading = true; refreshButton.disabled = true; clearFeedback();
    try {
      refs = parseSecretRefs(await request<unknown>("/api/secrets", "GET"));
      confirmId = null;
      renderList();
      if (!refs.length) showStatus(M4_TEXT.emptySecrets);
    } catch (error) { showError(failMessage(error)); }
    finally { loading = false; refreshButton.disabled = false; }
  };
  refreshButton.onclick = () => void refresh();

  const submitSet = async (): Promise<void> => {
    clearFeedback();
    let input: SecretSetInput;
    try { input = buildSecretInput({ ref: refField.value, plaintext: plaintextField.value }); }
    catch (error) { showError(failMessage(error)); return; }
    const attemptedValue = plaintextField.value; // transient only, for error sanitization
    plaintextField.value = ""; // never echo after submit, success or failure
    try {
      const result = parseSecretSet(await request<unknown>("/api/secrets", "POST", input));
      showStatus(`${M4_TEXT.secretSetConfirmation} (ref: ${result.ref}, set: ${String(result.set)})`);
      refField.value = "";
      await refresh();
    } catch (error) { showError(sanitizeSecretError(error, attemptedValue)); }
  };

  const removeRef = async (ref: string): Promise<void> => {
    clearFeedback();
    try {
      await request<unknown>(apiUrl("/api/secrets", ref), "DELETE");
      refs = refs.filter(entry => entry.ref !== ref);
      confirmId = null; renderList();
      showStatus(M4_TEXT.secretDeleted);
    } catch (error) { confirmId = null; renderList(); showError(failMessage(error)); }
  };

  function renderList(): void {
    const rows: M4Node[] = [];
    for (const entry of refs) {
      const row = m4el(create, "li", "m4-row");
      const head = m4el(create, "p", "m4-title", entry.ref);
      head.className = "m4-title m4-mono";
      row.append(head, m4el(create, "span", "m4-meta", `set at: ${displayTimestamp(entry.set_at)}`));
      const actionsRow = m4el(create, "span", "m4-actions");
      const deleteButton = m4el(create, "button", confirmId === entry.ref ? "danger" : "secondary", confirmId === entry.ref ? M4_TEXT.deleteConfirm : M4_TEXT.delete);
      deleteButton.type = "button";
      deleteButton.setAttribute("aria-pressed", String(confirmId === entry.ref));
      deleteButton.onclick = () => {
        if (confirmId !== entry.ref) { confirmId = entry.ref; renderList(); }
        else void removeRef(entry.ref);
      };
      actionsRow.append(deleteButton);
      if (confirmId === entry.ref) {
        const keepButton = m4el(create, "button", "secondary", M4_TEXT.keep);
        keepButton.type = "button";
        keepButton.onclick = () => { confirmId = null; renderList(); };
        actionsRow.append(keepButton);
        m4focus(deleteButton);
      }
      row.append(actionsRow);
      rows.push(row);
    }
    list.replaceChildren(...rows);
  }
  return { refresh };
}

// --- skills panel ---------------------------------------------------------
export function skillsPanel({ root, create, request }: M4PanelDom): M4PanelHandle {
  let skills: Skill[] = [];
  let loading = false;

  const note = m4el(create, "p", "hint", M4_TEXT.skillsNote);
  const actions = m4el(create, "p", "row");
  const refreshButton = m4button(create, actions, M4_TEXT.refreshSkills, "button");
  const statusLine = m4el(create, "p", "hint");
  statusLine.setAttribute("role", "status"); statusLine.setAttribute("aria-live", "polite");
  const errorLine = m4el(create, "p", "error");
  errorLine.setAttribute("role", "alert");
  const list = m4el(create, "ol", "m4-list");
  root.append(note, actions, statusLine, errorLine, list);

  const failMessage = (error: unknown): string => error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
  const showStatus = (message: string): void => { statusLine.textContent = message; statusLine.hidden = false; };
  const showError = (message: string): void => { errorLine.textContent = message; errorLine.hidden = false; };
  const clearFeedback = (): void => { statusLine.hidden = true; errorLine.hidden = true; };

  const refresh = async (): Promise<void> => {
    if (loading) return;
    loading = true; refreshButton.disabled = true; clearFeedback();
    try {
      skills = parseSkills(await request<unknown>("/api/skills", "GET"));
      renderList();
      if (!skills.length) showStatus(M4_TEXT.emptySkills);
    } catch (error) { showError(failMessage(error)); }
    finally { loading = false; refreshButton.disabled = false; }
  };
  refreshButton.onclick = () => void refresh();

  const removeSkill = async (id: string): Promise<void> => {
    clearFeedback();
    try {
      await request<unknown>(apiUrl("/api/skills", id), "DELETE");
      showStatus("Skill deleted.");
      await refresh();
    } catch (error) { showError(failMessage(error)); }
  };

  const toggle = async (id: string, enabled: boolean): Promise<void> => {
    clearFeedback();
    try {
      await request<unknown>(apiUrl("/api/skills", id), "PUT", buildSkillToggle(enabled));
      showStatus(M4_TEXT.skillToggleSaved);
    } catch (error) {
      const skill = skills.find(item => item.id === id); // revert the optimistic flip
      if (skill) { skill.enabled = !enabled; renderList(); }
      showError(failMessage(error));
    }
  };

  function renderList(): void {
    const rows: M4Node[] = [];
    for (const skill of skills) {
      const row = m4el(create, "li", "m4-row");
      const head = m4el(create, "p", "m4-title");
      head.append(m4el(create, "span", "m4-strong", skill.name), m4el(create, "span", "m4-badge", skill.scope));
      row.append(head);
      if (skill.description) row.append(m4el(create, "p", "m4-objective", skill.description));
      row.append(m4el(create, "span", "m4-meta m4-mono", skill.path));
      const actionsRow = m4el(create, "span", "m4-actions");
      const toggleBox = m4toggler(create, actionsRow, `Enable skill ${skill.name}`, skill.enabled);
      toggleBox.onchange = () => {
        skill.enabled = toggleBox.checked; // optimistic; reverted on failure
        renderList();
        void toggle(skill.id, toggleBox.checked);
      };
      if (!skill.path.endsWith("SKILL.md")) {
        const remove = m4el(create, "button", "secondary", M4_TEXT.delete);
        remove.type = "button";
        remove.onclick = () => { void removeSkill(skill.id); };
        actionsRow.append(remove);
      }
      row.append(actionsRow);
      rows.push(row);
    }
    list.replaceChildren(...rows);
  }
  return { refresh };
}


export function resultStatus(state: string, result?: TaskResult | null): string {
  if (result && typeof result.error === "string" && result.error) return result.error;
  if (state === "paused") return "Paused. Resume to continue from the exact step where it stopped.";
  if (result?.tests_verified === true) return "Container tests passed. Review the diff; this is not proof of every acceptance criterion.";
  if (result) {
    const checks = (Array.isArray(result.checks) ? result.checks : []).filter(check => check && typeof check.kind === "string" && typeof check.status === "string").map(check => `${check.kind}: ${check.status}`).join(" · ");
    return `NOT TEST-VERIFIED. ${checks ? checks + ". " : ""}Source repository unchanged. Inspect the check evidence above.`;
  }
  return isTerminal(state)
    ? "Task stopped without a result. Inspect the persisted events above."
    : "Work is running. Events are persisted backend activity.";
}
