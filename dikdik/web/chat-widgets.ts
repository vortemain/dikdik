// Chat message widgets: web cards, evidence blocks, structured property tables,
// tool labels and checklists. Pure DOM builders — they depend only on ./dom,
// ./code-render and the four app-level functions supplied via ChatWidgetDeps.
//
// Server-provided text is always textContent, never parsed as HTML.
import { make, icon } from "./dom";
import { codeBlock, languageFor, looksLikeDiff, safePageLink } from "./code-render";
import type { ChatMessage, ChecklistItem } from "./contracts";

export interface StepLabel { icon: string; title: string; arg: string }

export const TOOL_LABELS: Record<string, [string, string, string]> = {
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
  web_search: ["search", "Researched with Tavily", "query"],
  web_fetch: ["globe", "Fetched page", "url"],
};

// Deps that only app.ts can supply (they close over app state/imports).
export interface ChatWidgetDeps {
  richText: (text: string) => HTMLElement;
  fileBadge: (path: string) => HTMLElement;
  api: <T>(path: string, body?: unknown) => Promise<T>;
  message: (text: string, error?: boolean) => void;
}

export function detailField(details: unknown, key: string): string {
  if (typeof details !== "object" || details === null || !(key in details)) return "";
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

export function toolName(msg: ChatMessage): string {
  return (msg.title ?? "").replace(/^Tool (call|result): /, "");
}

export function stepLabel(msg: ChatMessage): StepLabel {
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
    case "agent_tool_result": return { icon: msg.state === "tool_failed" ? "alert" : "terminal", title: `${msg.state === "tool_failed" ? "Agent error" : "Output"} · ${toolName(msg)}`, arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "inspected": return { icon: "folder", title: "Inspected repository", arg: detailField(details, "file_count") ? `${detailField(details, "file_count")} files` : "" };
    case "execution": return { icon: "terminal", title: "Running tests", arg: detailField(details, "command") };
    case "syntax": return { icon: detailField(details, "status") === "passed" ? "check" : "alert", title: msg.title ?? "Syntax check", arg: detailField(details, "status") };
    case "agent_repair": return { icon: "refresh", title: "Repair attempt", arg: detailField(details, "reason") };
    case "agent_started": return { icon: "sparkle", title: "Started agent", arg: "" };
    case "agent_finished": return { icon: "flag", title: "Agent finished", arg: detailField(details, "state") };
    case "proposal": return { icon: "pencil", title: "Proposed edits", arg: "" };
    case "applied": return { icon: "check", title: msg.title ?? "Applied", arg: (msg.body ?? "").split("\n").join(", ") };
    case "skills": return { icon: "book", title: "Using skills", arg: (msg.body ?? "").split("\n").join(", ") };
    case "mcp": return { icon: "plug", title: "Connected MCP servers", arg: `${(msg.body ?? "").split("\n").join(", ")} · ${detailField(details, "tools") || 0} tools` };
    case "cancellation_requested": return { icon: "stop", title: "Cancellation requested", arg: "" };
    case "interrupt_requested": return { icon: "stop", title: "Stopping the current action", arg: "" };
    case "recovery_cleanup": return { icon: "alert", title: "Recovered after restart", arg: detailField(details, "warning") };
    case "hook_blocked": return { icon: "shield", title: "Blocked by a project hook", arg: msg.body ?? "" };
    case "agent_thinking": return { icon: "sparkle", title: "Thinking", arg: "" };
    case "model_routed": return { icon: "cpu", title: msg.title ?? "Routed", arg: msg.body ?? "" };
    case "secret_answered": return { icon: "key", title: msg.title ?? "Secret", arg: "" };
    case "critic": return { icon: msg.title === "Critic: looks done" ? "check" : "alert", title: msg.title ?? "Critic", arg: (msg.body ?? "").split("\n")[0] ?? "" };
    case "sandbox_secrets": return { icon: "key", title: "Secrets available to commands", arg: msg.body ?? "" };
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

/** A reusable chip for a URL — label is the site origin, click opens the page. */
export function siteChip(url: string): HTMLButtonElement {
  const chip = make("button", "site-chip"); chip.type = "button";
  let label = url;
  try {
    const parsed = new URL(url), path = parsed.pathname.replace(/\/$/, "");
    label = path && path !== "/" ? `${parsed.hostname}${path.length > 42 ? `${path.slice(0, 42)}…` : path}` : parsed.hostname;
  } catch { /* keep the raw text */ }
  chip.append(icon("globe"), make("span", "", label));
  chip.title = url;
  chip.onclick = () => { window.open(url, "_blank", "noopener"); };
  return chip;
}

/** Reader-style card for a fetched web page: title, source chip, meta, extracted text. */
export function webPageCard(data: Record<string, unknown>): HTMLElement {
  const card = make("article", "web-card");
  const head = make("div", "web-card-head");
  const badge = make("span", "web-card-badge"); badge.append(icon("globe"));
  const title = typeof data.title === "string" && data.title.trim() ? data.title.trim().slice(0, 160) : "Web page";
  const url = typeof data.url === "string" ? data.url : "";
  head.append(badge, make("div", "web-card-titles", title), url ? siteChip(url) : "");
  card.append(head);
  const meta: string[] = [];
  const status = typeof data.status === "number" ? data.status : 0;
  if (status === 200) meta.push("Full text extracted");
  else if (status) meta.push(`HTTP ${status}`);
  if (typeof data.content_type === "string" && data.content_type) meta.push(data.content_type.split(";")[0] ?? "");
  if (typeof data.text === "string") meta.push(`${data.text.length.toLocaleString()} characters`);
  if (data.truncated === true) meta.push("Shortened for the conversation");
  if (meta.length) card.append(make("p", "web-card-meta", meta.filter(Boolean).join(" · ")));
  const text = typeof data.text === "string" ? data.text.trim() : "";
  if (text) {
    const body = make("div", "web-card-text");
    const shown = text.length > 1800 && !(typeof data.truncated === "boolean" ? data.truncated : false)
      ? `${text.slice(0, 1800).replace(/\s+\S*$/, "")}…` : text;
    for (const paragraph of shown.split(/\n{2,}/).slice(0, 12)) body.append(make("p", "", paragraph.trim()));
    card.append(body);
    if (shown.length < text.length) card.append(make("p", "web-card-meta", `Showing the first ${shown.length.toLocaleString()} characters in this card.`));
  } else card.append(make("p", "muted", "The page returned no readable text."));
  return card;
}

export function renderTaskChecklistWidget(tasks: ChecklistItem[], title?: string): HTMLElement {
  const card = make("div", "tracker-widget");
  const head = make("div", "tracker-header");
  const doneCount = tasks.filter(t => t.status === "done").length;
  head.append(
    icon("list"),
    make("span", "tracker-title-head", title || "Task Checklist"),
    make("span", "tracker-badge", `${doneCount}/${tasks.length} done`)
  );
  card.append(head);
  const list = make("ol", "tracker-list");
  for (const task of tasks) {
    const item = make("li", `tracker-item tracker-status-${task.status}`);
    const iconWrap = make("span", "tracker-status-icon");
    if (task.status === "done") {
      iconWrap.append(icon("check"));
    } else if (task.status === "in_progress") {
      iconWrap.append(icon("spiral"));
    }
    const text = make("span", "tracker-title", task.title);
    const pill = make("span", `tracker-pill pill-${task.status}`, task.status === "done" ? "Done" : task.status === "in_progress" ? "In progress" : "To do");
    item.append(iconWrap, text, pill);
    list.append(item);
  }
  card.append(list);
  return card;
}

export function renderTestChecksWidget(checks: Array<{ kind?: string; status?: string; output?: string }>): HTMLElement {
  const card = make("div", "test-checks-card");
  const head = make("div", "test-checks-head");
  const allPassed = checks.every(c => c.status === "passed");
  const unavailable = checks.some(c => c.status === "not_run") && checks.every(c => c.status === "passed" || c.status === "not_run");
  head.append(icon(allPassed ? "check" : "alert"), make("span", "card-title", allPassed ? "All checks passed" : unavailable ? "Automated validation unavailable" : "Checks reported issues"));
  card.append(head);
  const list = make("div", "test-checks-list");
  for (const c of checks) {
    const row = make("div", `test-check-row check-${c.status || "unknown"}`);
    const badge = make("span", `check-status-badge status-${c.status || "unknown"}`, c.status || "check");
    const kind = make("span", "check-kind", c.kind || "test");
    row.append(badge, kind);
    if (c.output) {
      const out = make("pre", "check-output", c.output);
      row.append(out);
    }
    list.append(row);
  }
  card.append(list);
  return card;
}

export function renderPropertiesUI(obj: Record<string, unknown>, options: { title?: string } = {}): HTMLElement {
  const card = make("div", "prop-card");
  if (options.title) {
    const head = make("div", "prop-head");
    head.append(make("strong", "card-title", options.title));
    card.append(head);
  }
  const table = make("div", "prop-table");
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    const row = make("div", "prop-row");
    const k = make("span", "prop-key", key.replace(/_/g, " "));
    const v = make("div", "prop-val");
    if (typeof value === "boolean") {
      v.append(make("span", `val-badge bool-${value}`, value ? "True" : "False"));
    } else if (typeof value === "number") {
      v.append(make("span", "val-badge val-number", String(value)));
    } else if (typeof value === "string") {
      if (value.startsWith("http://") || value.startsWith("https://")) {
        const link = safePageLink(value);
        if (link) v.append(link); else v.append(make("span", "", value));
      } else if (value.includes("\n") || value.length > 80) {
        v.append(codeBlock(value, "", { wrap: true }));
      } else {
        v.append(make("span", "prop-text", value));
      }
    } else if (Array.isArray(value)) {
      if (value.every(item => typeof item === "string" || typeof item === "number")) {
        const chips = make("div", "step-chips");
        for (const item of value) chips.append(make("span", "val-badge val-tag", String(item)));
        v.append(chips);
      } else {
        v.append(renderStructuredUI(value));
      }
    } else if (typeof value === "object") {
      v.append(renderPropertiesUI(value as Record<string, unknown>));
    }
    row.append(k, v);
    table.append(row);
  }
  card.append(table);
  return card;
}

export function renderStructuredUI(data: unknown, options: { title?: string } = {}): HTMLElement {
  if (data === null || data === undefined) return make("div");
  if (typeof data === "string") {
    const trimmed = data.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        const parsed = JSON.parse(trimmed) as unknown;
        return renderStructuredUI(parsed, options);
      } catch {
        // Not JSON
      }
    }
    return make("p", "step-text", data);
  }
  if (typeof data !== "object") {
    return make("span", "val-badge", String(data));
  }
  if (Array.isArray(data)) {
    if (data.length > 0 && data.every(item => typeof item === "object" && item !== null && "title" in item && "status" in item)) {
      return renderTaskChecklistWidget(data as ChecklistItem[], options.title);
    }
    if (data.every(item => typeof item === "string" || typeof item === "number")) {
      const container = make("div", "step-chips");
      for (const item of data) {
        container.append(make("span", "val-badge val-tag", String(item)));
      }
      return container;
    }
    const card = make("div", "step-ui-card");
    if (options.title) card.append(make("strong", "card-title", options.title));
    for (const item of data) {
      card.append(renderStructuredUI(item));
    }
    return card;
  }

  const obj = data as Record<string, unknown>;
  if (Array.isArray(obj.tasks) && obj.tasks.length > 0 && obj.tasks.every(item => typeof item === "object" && item !== null && "title" in item)) {
    return renderTaskChecklistWidget(obj.tasks as ChecklistItem[], options.title);
  }
  if (Array.isArray(obj.checks) && obj.checks.length > 0 && obj.checks.every(c => typeof c === "object" && c !== null && "status" in c)) {
    return renderTestChecksWidget(obj.checks as Array<{ kind?: string; status?: string; output?: string }>);
  }
  if (typeof obj.content === "string" && (obj.path === "PLAN.md" || options.title?.includes("Plan"))) {
    return structuredRichText(obj.content);
  }
  if (typeof obj.url === "string" && !obj.command) {
    const link = safePageLink(obj.url);
    if (link) return link;
  }
  if (typeof obj.command === "string") {
    const card = make("div", "step-ui-card");
    card.append(codeBlock(obj.command, "bash", { wrap: true }));
    const rest = { ...obj };
    delete rest.command;
    if (Object.keys(rest).length) card.append(renderPropertiesUI(rest));
    return card;
  }

  return renderPropertiesUI(obj, options);
}

export function renderOutputUI(text: string, tool: string, path = ""): HTMLElement {
  if (tool === "read_file") {
    return codeBlock(text, languageFor(path), path ? { filename: path } : {});
  }
  if (tool === "run_command") {
    return codeBlock(text, "bash", { wrap: true });
  }
  const trimmed = text.trim();
  if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      return renderStructuredUI(parsed);
    } catch {
      // Not JSON
    }
  }
  if (looksLikeDiff(text)) {
    return codeBlock(text, "diff");
  }
  return codeBlock(text, "", { wrap: true });
}

// --- The following functions need app-level deps (richText, fileBadge, api, message) ---
// They are created by the factory below so they can close over those deps.

let structuredRichText: (text: string) => HTMLElement = text => make("p", "step-text", text);

export function createChatWidgets(deps: ChatWidgetDeps) {
  structuredRichText = deps.richText;

  function renderEvidenceCard(details: unknown): HTMLElement {
    if (typeof details !== "object" || details === null) {
      return make("p", "turn-note", "No evidence recorded.");
    }
    const d = details as Record<string, unknown>;
    const card = make("div", "evidence-ui-card");

    const grid = make("div", "evidence-grid");

    if (d.tests_verified !== undefined) {
      const stat = make("div", "evidence-stat");
      stat.append(
        make("span", "stat-label", "Tests verification"),
        make("span", `verify-badge ${d.tests_verified ? "verified" : ""}`, d.tests_verified ? "Tests passed" : "Not test-verified")
      );
      grid.append(stat);
    }
    const gate = d.release_gate as { decision?: unknown; gates?: unknown } | undefined;
    if (gate && typeof gate.decision === "string") {
      const stat = make("div", "evidence-stat");
      stat.append(
        make("span", "stat-label", "Release gate"),
        make("span", `verify-badge ${gate.decision === "APPROVED" ? "verified" : ""}`, gate.decision.replaceAll("_", " ").toLowerCase())
      );
      grid.append(stat);
    }
    if (d.direct !== undefined) {
      const stat = make("div", "evidence-stat");
      stat.append(
        make("span", "stat-label", "Execution mode"),
        make("span", "stat-value", d.direct ? "Direct workspace edits" : "Proposal for review")
      );
      grid.append(stat);
    }
    if (d.checkpoint) {
      const stat = make("div", "evidence-stat");
      stat.append(
        make("span", "stat-label", "Undo checkpoint"),
        make("span", "stat-value font-mono", String(d.checkpoint))
      );
      grid.append(stat);
    }
    if (Array.isArray(d.changed_files)) {
      const stat = make("div", "evidence-stat");
      stat.append(
        make("span", "stat-label", "Files changed"),
        make("span", "stat-value", `${d.changed_files.length} file${d.changed_files.length === 1 ? "" : "s"}`)
      );
      grid.append(stat);
    }
    if (d.tokens || d.usage) {
      const usage = (d.usage ?? d.tokens) as Record<string, unknown>;
      if (usage && typeof usage === "object") {
        const stat = make("div", "evidence-stat");
        const total = usage.total_tokens ?? usage.tokens;
        stat.append(
          make("span", "stat-label", "Tokens used"),
          make("span", "stat-value", total ? Number(total).toLocaleString() : "–")
        );
        grid.append(stat);
      }
    }

    if (grid.children.length) card.append(grid);

    if (Array.isArray(d.checks) && d.checks.length) {
      card.append(renderTestChecksWidget(d.checks as Array<{ kind?: string; status?: string; output?: string }>));
    }

    if (gate && Array.isArray(gate.gates) && gate.gates.length) {
      const wrap = make("div", "evidence-files");
      wrap.append(make("strong", "card-title", "Release gates"));
      const list = make("ul", "gate-list");
      for (const entry of gate.gates as Array<{ gate?: unknown; status?: unknown; detail?: unknown; views?: unknown }>) {
        const item = make("li", "", `${String(entry.gate ?? "")}: ${String(entry.status ?? "")} — ${String(entry.detail ?? "")}`);
        if (entry.gate === "visual" && Array.isArray(entry.views)) {
          for (const view of entry.views) {
            if (typeof view !== "string") continue;
            const accept = make("button", "small") as HTMLButtonElement;
            accept.type = "button";
            accept.textContent = `Accept ${view} as baseline`;
            accept.onclick = async () => {
              accept.disabled = true;
              try {
                await deps.api("/api/visual-baselines/accept", { view });
                accept.textContent = `Accepted ${view}`;
              } catch (error) { accept.disabled = false; deps.message(error instanceof Error ? error.message : "Could not accept the baseline", true); }
            };
            item.append(" ", accept);
          }
        }
        list.append(item);
      }
      wrap.append(list);
      card.append(wrap);
    }

    if (Array.isArray(d.changed_files) && d.changed_files.length) {
      const filesWrap = make("div", "evidence-files");
      filesWrap.append(make("strong", "card-title", "Changed files"));
      const fileList = make("div", "step-chips");
      for (const f of d.changed_files) {
        if (typeof f === "string") {
          const chip = make("span", "val-badge val-tag");
          chip.append(deps.fileBadge(f), make("span", "", f));
          fileList.append(chip);
        }
      }
      filesWrap.append(fileList);
      card.append(filesWrap);
    }

    const handled = new Set(["diff", "diff_truncated", "changed_files", "file_changes", "tests_verified", "checks", "direct", "source_modified", "checkpoint", "tokens", "usage", "release_gate"]);
    const remaining: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(d)) {
      if (!handled.has(k) && v !== undefined && v !== null && v !== "") {
        remaining[k] = v;
      }
    }
    if (Object.keys(remaining).length) {
      card.append(renderPropertiesUI(remaining, { title: "Details" }));
    }

    return card;
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
      blocks.push(codeBlock(detailField(details, "content"), languageFor(path), { filename: path }));
      return blocks;
    }
    if (msg.kind === "agent_tool_call" && toolName(msg) === "task_tracker" && typeof details === "object" && details !== null && "tasks" in details) {
      blocks.push(renderTaskChecklistWidget((details as Record<string, unknown>).tasks as ChecklistItem[]));
      return blocks;
    }
    if (msg.kind === "agent_tool_call" && toolName(msg) === "write_plan" && detailField(details, "content")) {
      blocks.push(deps.richText(detailField(details, "content")));
      return blocks;
    }
    if (msg.kind === "agent_tool_call" && toolName(msg) === "browse_url" && detailField(details, "url")) {
      const link = safePageLink(detailField(details, "url"));
      if (link) blocks.push(link);
      return blocks;
    }
    if (msg.kind === "agent_tool_result" && toolName(msg) === "web_fetch" && typeof details === "object" && details !== null) {
      blocks.push(webPageCard(details as Record<string, unknown>));
      return blocks;
    }
    if (msg.kind === "agent_tool_result" && toolName(msg) === "web_search" && typeof details === "object" && details !== null && "results" in details && Array.isArray((details as Record<string, unknown>).results)) {
      const sources = (details as { results: unknown[] }).results.slice(0, 5);
      const note = make("p", "muted sources-note", "Web sources · untrusted content");
      blocks.push(note);
      for (const source of sources) {
        if (typeof source !== "object" || source === null) continue;
        const item = source as Record<string, unknown>, link = safePageLink(item.url);
        if (!link) continue;
        const card = make("article", "provider-detail");
        const head = make("div", "source-head");
        head.append(make("strong", "", typeof item.title === "string" && item.title.trim() ? item.title.slice(0, 300) : "Source"), link);
        card.append(head, make("p", "", typeof item.content === "string" ? item.content.slice(0, 600) : ""));
        if (typeof item.url === "string") card.append(siteChip(item.url));
        blocks.push(card);
      }
      if (!sources.length) blocks.push(make("p", "muted", "No matching sources."));
      return blocks;
    }
    if (msg.body) {
      if (msg.role === "system") blocks.push(make("p", "system-notice-text", msg.body));
      else blocks.push(renderOutputUI(msg.body, toolName(msg), path));
    }
    const shown = new Set(["path", "query", "text", "pattern"]);
    if (msg.kind === "agent_tool_call" && typeof details === "object" && details !== null && Object.keys(details).every(key => shown.has(key))) return blocks;
    if (details !== undefined && details !== null && !(typeof details === "object" && Object.keys(details).length === 0)) {
      blocks.push(renderStructuredUI(details));
    }
    return blocks;
  }

  return { renderEvidenceCard, evidenceBlocks };
}