(() => {
  // dikdik/web/contracts.ts
  function fileStatusLabel(status) {
    return { added: "Added", modified: "Modified", deleted: "Deleted" }[status];
  }
  function gitPath(path, quoteUnicode) {
    let escaped = "";
    for (const byte of new TextEncoder().encode(path)) {
      if (byte === 34 || byte === 92)
        escaped += "\\" + String.fromCharCode(byte);
      else if (byte === 9)
        escaped += "\\t";
      else if (byte === 10)
        escaped += "\\n";
      else if (byte === 13)
        escaped += "\\r";
      else if (byte < 32 || byte === 127 || quoteUnicode && byte >= 128)
        escaped += "\\" + byte.toString(8).padStart(3, "0");
      else
        escaped += String.fromCharCode(byte);
    }
    if (!quoteUnicode)
      escaped = new TextDecoder().decode(Uint8Array.from(escaped, (char) => char.charCodeAt(0)));
    return escaped === path ? path : `"${escaped}"`;
  }
  function candidateGitPath(path) {
    const bytes = new TextEncoder().encode(path);
    if (bytes.every((byte) => byte >= 33 && byte < 127 && byte !== 34 && byte !== 92))
      return path;
    return '"' + Array.from(bytes, (byte) => byte >= 32 && byte < 127 && byte !== 34 && byte !== 92 ? String.fromCharCode(byte) : "\\" + byte.toString(8).padStart(3, "0")).join("") + '"';
  }
  function supportedGitPaths(path) {
    const paths = [candidateGitPath(path)];
    if (!/\s/.test(path))
      paths.push(gitPath(path, true), gitPath(path, false));
    return paths;
  }
  function validatesTextPatch(chunk, path, status) {
    const hunkStart = chunk.findIndex((line) => line.startsWith("@@"));
    const headers = chunk.slice(1, hunkStart < 0 ? chunk.length : hunkStart);
    if (headers.some((line) => !["new file mode ", "deleted file mode ", "old mode ", "new mode ", "index ", "--- ", "+++ "].some((prefix) => line.startsWith(prefix))))
      return false;
    const oldPaths = headers.filter((line) => line.startsWith("--- ")).map((line) => line.slice(4));
    const newPaths = headers.filter((line) => line.startsWith("+++ ")).map((line) => line.slice(4));
    if (hunkStart < 0)
      return oldPaths.length === 0 && newPaths.length === 0;
    const expectedOld = status === "added" ? ["/dev/null"] : supportedGitPaths("a/" + path);
    const expectedNew = status === "deleted" ? ["/dev/null"] : supportedGitPaths("b/" + path);
    if (oldPaths.length !== 1 || newPaths.length !== 1 || !expectedOld.includes(oldPaths[0]) || !expectedNew.includes(newPaths[0]))
      return false;
    let before = 0, after = 0, lastData = false;
    for (const line of chunk.slice(hunkStart)) {
      if (line.startsWith("@@")) {
        if (before || after)
          return false;
        const match = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$/.exec(line);
        if (!match)
          return false;
        before = Number(match[2] ?? 1);
        after = Number(match[4] ?? 1);
        lastData = false;
        if (!Number.isSafeInteger(before) || !Number.isSafeInteger(after) || before > chunk.length || after > chunk.length)
          return false;
      } else if (line === "\\ No newline at end of file") {
        if (!lastData)
          return false;
        lastData = false;
      } else {
        const prefix = line[0];
        if (prefix === " ") {
          if (!before || !after)
            return false;
          before--;
          after--;
        } else if (prefix === "-") {
          if (!before)
            return false;
          before--;
        } else if (prefix === "+") {
          if (!after)
            return false;
          after--;
        } else
          return false;
        lastData = true;
      }
    }
    return before === 0 && after === 0;
  }
  function reviewDiff(result) {
    const record = typeof result === "object" && result !== null ? result : {};
    const diff = typeof record.diff === "string" ? record.diff : "";
    const fallback = (metadata) => ({ diff, files: [], metadata });
    if (record.file_changes === undefined)
      return fallback("legacy");
    if (!Array.isArray(record.file_changes))
      return fallback("invalid");
    if (!diff)
      return record.file_changes.length ? fallback("invalid") : { diff, files: [], metadata: "available" };
    if (!diff.endsWith(`
`))
      return fallback("invalid");
    const lines = diff.split(`
`), files = [], paths = new Set;
    let end = 0;
    for (const entry of record.file_changes) {
      if (typeof entry !== "object" || entry === null)
        return fallback("invalid");
      const { path, status, start_line, end_line } = entry;
      if (typeof path !== "string" || !path || /[\r\n\0]/.test(path) || new TextDecoder().decode(new TextEncoder().encode(path)) !== path || paths.has(path) || status !== "added" && status !== "modified" && status !== "deleted" || typeof start_line !== "number" || typeof end_line !== "number" || !Number.isSafeInteger(start_line) || !Number.isSafeInteger(end_line) || start_line !== end || end_line <= start_line || end_line > lines.length - 1)
        return fallback("invalid");
      const chunk = lines.slice(start_line, end_line);
      const oldPaths = supportedGitPaths("a/" + path), newPaths = supportedGitPaths("b/" + path);
      const headers = oldPaths.flatMap((before) => newPaths.map((after) => `diff --git ${before} ${after}`));
      if (!headers.includes(chunk[0] || "") || chunk.slice(1).some((line) => line.startsWith("diff --git ")))
        return fallback("invalid");
      const added = chunk.some((line) => line.startsWith("new file mode "));
      const deleted = chunk.some((line) => line.startsWith("deleted file mode "));
      if (added && deleted || status !== (added ? "added" : deleted ? "deleted" : "modified"))
        return fallback("invalid");
      if (!validatesTextPatch(chunk, path, status))
        return fallback("invalid");
      files.push({ path, status, start_line, end_line, diff: chunk.join(`
`) + `
` });
      paths.add(path);
      end = end_line;
    }
    return end === lines.length - 1 ? { diff, files, metadata: "available" } : fallback("invalid");
  }
  function isTerminal(state) {
    return ["completed", "failed", "blocked", "cancelled", "interrupted", "draft", "paused"].includes(state);
  }
  function isResumable(state) {
    return ["paused", "failed", "cancelled", "interrupted", "blocked"].includes(state);
  }
  function modePolicy(mode, verification) {
    return {
      showEdits: mode === "offline",
      requiresExternal: mode !== "offline",
      verification: mode === "plan" ? "syntax" : verification,
      locksVerification: mode === "plan",
      requiresExecution: mode !== "plan" && verification === "docker"
    };
  }
  function parseMode(value) {
    if (value === "offline" || value === "model" || value === "agent" || value === "plan")
      return value;
    throw new Error("Select a supported proposal source.");
  }
  function parseVerification(value) {
    if (value === "syntax" || value === "docker")
      return value;
    throw new Error("Select a supported verification boundary.");
  }
  function buildTaskRequest(input) {
    const policy = modePolicy(input.mode, input.verification);
    if (!input.objective.trim())
      throw new Error("Enter an objective.");
    if (policy.requiresExternal && !input.approveExternal)
      throw new Error("Approve sending code and the objective to the external provider.");
    if (policy.requiresExecution && !input.approveExecution)
      throw new Error("Approve execution inside Docker before starting this task.");
    const browsing = input.mode === "agent" && input.approveBrowser === true;
    if (browsing && !input.approveExecution)
      throw new Error("Approve Docker execution to use the browser.");
    let edits = [];
    if (policy.showEdits) {
      const parsed = JSON.parse(input.edits);
      if (!Array.isArray(parsed) || !parsed.every((edit) => typeof edit === "object" && edit !== null && ("path" in edit) && typeof edit.path === "string" && ("before" in edit) && typeof edit.before === "string" && ("after" in edit) && typeof edit.after === "string"))
        throw new Error("Exact replacements must be an array of path, before, and after strings.");
      edits = parsed;
    }
    return {
      objective: input.objective,
      mode: input.mode,
      edits,
      verification: policy.verification,
      approve_execution: (policy.requiresExecution || browsing) && input.approveExecution,
      approve_external: policy.requiresExternal && input.approveExternal,
      ...browsing ? { approve_browser: true } : {},
      ...["agent", "plan"].includes(input.mode) && input.agentProfile ? { agent_profile: input.agentProfile } : {},
      ...input.mode !== "offline" && input.modelProfile ? { model_profile: input.modelProfile } : {},
      ...input.attachments?.length ? { attachments: input.attachments } : {},
      ...input.mode === "agent" && input.approveSecrets && input.approveExecution && (policy.requiresExecution || browsing) ? { approve_secrets: true } : {},
      ...input.goalMaxTurns ? goalTurns(input.mode, input.goalMaxTurns) : {}
    };
  }
  function goalTurns(mode, turns) {
    if (mode !== "agent")
      throw new Error("A goal needs Agent mode.");
    if (!Number.isInteger(turns) || turns < 1 || turns > 20)
      throw new Error("A goal can run 1–20 turns.");
    return { goal_max_turns: turns };
  }
  var SLASH_COMMANDS = [
    { name: "plan", hint: "[request]", description: "Switch to the read-only planner" },
    { name: "code", hint: "[request]", description: "Back to the coding agent" },
    { name: "goal", hint: "[--max N] goal", description: "Keep working, turn after turn, until a judge says the goal is met" },
    { name: "btw", hint: "question", description: "Ask a side question; the answer is not added to the conversation" },
    { name: "model", hint: "[name]", description: "Use another model profile for the next messages" },
    { name: "condense", hint: "", description: "Summarise older turns to free up context" },
    { name: "fork", hint: "", description: "Copy this conversation into a new one" }
  ];
  function parseSlash(text) {
    const match = /^\/([a-z][a-z0-9_-]{0,63})(?:\s+([\s\S]*))?$/i.exec(text.trim());
    return match ? { name: (match[1] ?? "").toLowerCase(), arg: (match[2] ?? "").trim() } : null;
  }
  function parseGoal(arg) {
    const match = /^--max\s+(\d{1,2})\s*([\s\S]*)$/.exec(arg.trim());
    const turns = match ? Number(match[1]) : 5;
    const goal = (match ? match[2] ?? "" : arg).trim();
    if (!goal)
      throw new Error("Say what the goal is, for example /goal --max 5 make the tests pass.");
    if (turns < 1 || turns > 20)
      throw new Error("A goal can run 1–20 turns.");
    return { turns, goal };
  }
  function slashMatches(text, skills = []) {
    const typed = /^\/([a-z0-9_-]*)$/i.exec(text);
    if (!typed)
      return [];
    const all = [...SLASH_COMMANDS, ...skills.map((name) => ({ name, hint: "[request]", description: "Use this skill" }))];
    const prefix = (typed[1] ?? "").toLowerCase();
    return all.filter((command) => command.name.startsWith(prefix)).slice(0, 12);
  }
  function contextUsage(events) {
    for (let i = events.length - 1;i >= 0; i--) {
      const event = events[i];
      if (!event)
        continue;
      if (event.kind === "condensed")
        return null;
      const bytes = numOrUndef(event.data?.context_bytes), limit = numOrUndef(event.data?.context_limit);
      if (event.kind === "agent_model" && bytes !== undefined && limit !== undefined && limit > 0)
        return { bytes, limit, ratio: Math.min(1, bytes / limit) };
    }
    return null;
  }
  function reportedUsage(value) {
    if (typeof value !== "object" || value === null)
      return {};
    const data = value, out = {};
    const fields = [
      ["inputTokens", "input_tokens"],
      ["outputTokens", "output_tokens"],
      ["cacheReadTokens", "cache_read_tokens"],
      ["cacheWriteTokens", "cache_write_tokens"],
      ["totalTokens", "total_tokens"],
      ["reportedCost", "reported_cost"]
    ];
    for (const [target, source] of fields) {
      const number = numOrUndef(data[source]);
      if (number !== undefined && number >= 0)
        out[target] = number;
    }
    return out;
  }
  function addUsage(target, usage) {
    for (const key of ["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "totalTokens", "reportedCost"]) {
      const value = usage[key];
      if (value !== undefined)
        target[key] = (target[key] ?? 0) + value;
    }
  }
  function usageMetrics(events) {
    const task = {}, turns = [];
    let current = null, cumulative = 0;
    const ensureTurn = () => {
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
        turns.push(current);
        cumulative = 0;
        continue;
      }
      if (event.kind === "agent_started") {
        const model = typeof data.model === "object" && data.model !== null ? data.model : {};
        ensureTurn().profile = textOf(model.profile, 120) || textOf(data.profile, 120);
        continue;
      }
      if (event.kind !== "agent_model" || data.phase !== "end")
        continue;
      const usage = reportedUsage(data.usage);
      const reportedTotal = numOrUndef(data.usage_tokens);
      if (reportedTotal !== undefined && reportedTotal >= 0) {
        if (usage.totalTokens === undefined && reportedTotal > cumulative)
          usage.totalTokens = reportedTotal - cumulative;
        cumulative = reportedTotal;
      }
      if (!Object.keys(usage).length)
        continue;
      const turn = ensureTurn();
      turn.calls += 1;
      addUsage(turn, usage);
      addUsage(task, usage);
    }
    return { context: contextUsage(events), task, turns: turns.filter((turn) => turn.calls > 0) };
  }
  function goalState(events) {
    for (let i = events.length - 1;i >= 0; i--) {
      const event = events[i];
      if (!event || event.kind !== "goal_status")
        continue;
      const d = event.data ?? {};
      return { status: textOf(d.status, 20), turns: numOrUndef(d.turns) ?? 0, maxTurns: numOrUndef(d.max_turns) ?? 0, objective: textOf(d.objective, 2000), reason: textOf(d.reason, 2000) };
    }
    return null;
  }
  function openConfirmations(events) {
    const open = new Set;
    for (const event of events) {
      const id = textOf(event.data?.id, 200);
      if (event.kind === "confirmation_requested" && id)
        open.add(id);
      else if (event.kind === "confirmation_resolved" && id)
        open.delete(id);
      else if (isTerminal(event.kind))
        open.clear();
    }
    return [...open];
  }
  function attachmentList(value) {
    if (!Array.isArray(value))
      return [];
    return value.flatMap((item) => {
      if (typeof item !== "object" || item === null)
        return [];
      const r = item;
      const id = textOf(r.id, 64), name = textOf(r.name, 255), type = textOf(r.type, 100);
      return /^[0-9a-f]{32}$/.test(id) && name ? [{ id, name, type, size: numOrUndef(r.size) ?? 0 }] : [];
    });
  }
  var CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
  function textOf(value, max = 8000) {
    if (typeof value !== "string" || !value || CONTROL.test(value))
      return "";
    return value.length > max ? value.slice(0, max) + `
…(truncated)` : value;
  }
  function numOrUndef(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
  }
  function eventToMessages(event, objective) {
    const { seq, time, kind, data } = event;
    const d = data ?? {};
    const base = { time, kind, id: `tool-${seq}` };
    switch (kind) {
      case "queued":
        return [{ id: `user-${seq}`, role: "user", time, kind, title: "Objective", body: objective }];
      case "user_message": {
        const text = textOf(d.text, 4000);
        if (d.goal === true && text)
          return [{ ...base, role: "system", kind: "goal_turn", title: "Next goal turn", body: text }];
        return text ? [{ id: `user-${seq}`, role: "user", time, kind, title: d.queued === true ? "Queued" : "Message", body: text }] : [];
      }
      case "agent_tool": {
        const name = textOf(d.name, 120) || "tool";
        if (d.phase === "start") {
          const args = d.arguments;
          return [{ ...base, role: "tool", kind: "agent_tool_call", title: `Tool call: ${name}`, details: typeof args === "object" && args !== null ? args : undefined }];
        }
        if (d.phase === "end") {
          return [{ ...base, role: "tool", kind: "agent_tool_result", title: `Tool result: ${name}`, body: observationText(name, d.observation), ...d.failed === true ? { state: "tool_failed" } : {} }];
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
        const servers = Array.isArray(d.servers) ? d.servers.filter((n) => typeof n === "string") : [];
        const errors = Array.isArray(d.errors) ? d.errors.map((e) => typeof e === "object" && e !== null ? `${String(e.server)}: ${String(e.error)}` : "").filter(Boolean) : [];
        return servers.length ? [{ ...base, role: "system", title: "Connected MCP servers", body: servers.join(`
`), details: { tools: d.tools, errors } }] : [];
      }
      case "skills": {
        const names = Array.isArray(d.names) ? d.names.filter((n) => typeof n === "string") : [];
        return names.length ? [{ ...base, role: "system", title: "Using skills", body: names.join(`
`) }] : [];
      }
      case "applied": {
        const files = Array.isArray(d.files) ? d.files.filter((f) => typeof f === "string") : [];
        return [{ ...base, role: "system", title: files.length ? "Applied to workspace" : "Already applied", body: files.join(`
`), details: d }];
      }
      case "cancellation_requested":
        return [{ ...base, role: "system", title: "Cancellation requested" }];
      case "interrupt_requested":
        return [{ ...base, role: "system", title: "Stopping the current action" }];
      case "recovery_cleanup":
        return [{ ...base, role: "system", title: "Recovered after restart", details: { warning: textOf(d.warning) } }];
      case "terminal_command":
      case "terminal_output":
        return [];
      case "browser_started":
        return [{ ...base, role: "tool", title: "Opening public web page", body: textOf(d.url, 4096) }];
      case "browser_result":
        return [{ ...base, role: "tool", title: "Browser capture", details: d }];
      case "plan_updated":
        return [{ ...base, role: "assistant", title: "PLAN.md", body: textOf(d.content, 32768), details: d }];
      case "planner_initialized":
        return [{ ...base, role: "system", title: "Planning agent initialized" }];
      case "task_tracker":
      case "plan_handoff":
        return [];
      case "draft":
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
        return d.phase === "end" ? [{ ...base, role: "tool", title: `${textOf(d.agent, 64) || "Subagent"} reported back`, body: textOf(d.findings, 4000), details: { agent: textOf(d.agent, 64), state: textOf(d.state, 40), tool_calls: numOrUndef(d.tool_calls) } }] : [{ ...base, role: "tool", title: `Asked ${textOf(d.agent, 64) || "a subagent"}`, body: textOf(d.task, 500), details: { agent: textOf(d.agent, 64) } }];
      case "condensed":
        return [{ ...base, role: "system", title: "Condensed the earlier conversation", body: textOf(d.summary, 8000) }];
      case "resumed":
        return [{ ...base, role: "system", title: "Resumed", details: { from_state: textOf(d.from_state, 40) } }];
      case "pause_requested":
        return [{ ...base, role: "system", title: "Pausing after this step" }];
      case "forked":
        return [{ ...base, role: "system", title: "Forked from another conversation", details: { from_task: textOf(d.from_task, 64), from_seq: numOrUndef(d.from_seq) } }];
      case "model_routed":
        return [{ ...base, role: "system", title: `Routed to ${textOf(d.profile, 64) || "a model"}`, body: textOf(d.reason, 200), details: { router: textOf(d.router, 64) } }];
      case "critic":
        return [{
          ...base,
          role: "system",
          title: d.error ? "Critic unavailable" : d.achieved === true ? "Critic: looks done" : "Critic: not done yet",
          body: textOf(d.error ?? [d.reason, d.next].filter((item) => typeof item === "string" && item).join(`
`), 2000)
        }];
      case "sandbox_secrets": {
        const names = Array.isArray(d.names) ? d.names.filter((n) => typeof n === "string") : [];
        return [{ ...base, role: "system", title: "Sandbox secrets available", body: names.join(", ") }];
      }
      case "goal_status": {
        const turns = numOrUndef(d.turns) ?? 0, max = numOrUndef(d.max_turns) ?? 0;
        const titles = { active: "Goal in progress", achieved: "Goal achieved", capped: "Goal stopped at its turn limit", stopped: "Goal stopped", error: "Goal check failed" };
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
        return [];
    }
  }
  function terminalTranscript(events) {
    const commands = new Map;
    const order = [];
    let total = 0;
    for (const event of events) {
      if (event.kind !== "terminal_command" && event.kind !== "terminal_output")
        continue;
      const data = event.data ?? {};
      const id = textOf(data.command_id, 80);
      if (!id)
        continue;
      if (event.kind === "terminal_command") {
        const command = textOf(data.command, 4000);
        if (!command || commands.has(id))
          continue;
        commands.set(id, { id, command, output: "", running: true });
        order.push(id);
        continue;
      }
      const row = commands.get(id);
      if (!row)
        continue;
      const chunk = textOf(data.output, 1024 * 1024);
      if (chunk && total < 1024 * 1024) {
        const kept = chunk.slice(0, 1024 * 1024 - total);
        row.output += kept;
        total += kept.length;
      }
      if (data.final === true) {
        row.running = false;
        if (data.exit_code === null || typeof data.exit_code === "number")
          row.exitCode = data.exit_code;
        const status = textOf(data.status, 80);
        if (status)
          row.status = status;
      }
    }
    const list = order.map((id) => commands.get(id)).filter(Boolean);
    const text = list.map((command) => {
      const ending = command.running ? `
[running]` : command.status && !["passed", "failed"].includes(command.status) ? `
[${command.status}]` : command.exitCode !== undefined && command.exitCode !== null && command.exitCode !== 0 ? `
[exit ${command.exitCode}]` : command.status === "failed" ? `
[failed]` : "";
      return `$ ${command.command}
${command.output}${ending}`;
    }).join(`

`);
    return { commands: list, text };
  }
  function parsePlan(value) {
    if (typeof value !== "object" || value === null)
      return null;
    const row = value;
    if (typeof row.task_id !== "string" || !/^[a-f0-9]{32}$/.test(row.task_id) || row.planner_id !== null && (typeof row.planner_id !== "string" || !/^[a-f0-9]{32}$/.test(row.planner_id)) || typeof row.content !== "string" || row.content.length > 32768 || typeof row.revision !== "number" || !Number.isSafeInteger(row.revision) || row.revision < 0 || typeof row.built_revision !== "number" || !Number.isSafeInteger(row.built_revision) || row.built_revision < 0 || row.built_revision > row.revision)
      return null;
    return { task_id: row.task_id, planner_id: row.planner_id, content: row.content, revision: row.revision, built_revision: row.built_revision };
  }
  function taskChecklist(events) {
    let tasks = [], used = false;
    for (const event of events) {
      if (event.kind !== "task_tracker")
        continue;
      const items = event.data?.tasks;
      if (!Array.isArray(items) || items.length > 50)
        continue;
      const next = [];
      for (const item of items) {
        if (typeof item !== "object" || item === null)
          continue;
        const row = item;
        if (typeof row.id === "string" && typeof row.title === "string" && row.title.length <= 300 && ["todo", "in_progress", "done"].includes(String(row.status)))
          next.push({ id: row.id, title: textOf(row.title, 300), status: row.status });
      }
      if (next.length === items.length) {
        used = true;
        tasks = next;
      }
    }
    return { used, tasks };
  }
  function browserView(events) {
    const view = { url: "", title: "", text: "", screenshot: "", status: "", error: "" };
    for (const event of events) {
      const data = event.data ?? {};
      if (event.kind === "browser_started") {
        view.status = "running";
        view.error = "";
        continue;
      }
      if (event.kind !== "browser_result")
        continue;
      view.status = textOf(data.status, 80);
      view.error = textOf(data.error, 1000);
      const screenshot = textOf(data.screenshot, 200);
      let url = "";
      try {
        const parsed = new URL(textOf(data.url, 4096));
        if (["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password)
          url = parsed.href;
      } catch {}
      if (view.status === "passed" && url && /^\/api\/tasks\/[a-f0-9]{32}\/browser\/screenshots\/[a-f0-9]{32}$/.test(screenshot)) {
        view.url = url;
        view.screenshot = screenshot;
        view.title = textOf(data.title, 256);
        view.text = textOf(data.text, 12000);
      }
    }
    return view;
  }
  function observationText(tool, observation) {
    if (typeof observation !== "object" || observation === null)
      return textOf(observation, 8000);
    const o = observation;
    if (typeof o.error === "string" && tool !== "run_command")
      return `Error: ${textOf(o.error, 2000)}`;
    if (o.truncated === true && typeof o.preview === "string")
      return textOf(o.preview, 8000) + `
…(truncated)`;
    const strings = (value) => Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
    switch (tool) {
      case "read_file":
        return textOf(strings(o.lines).join(`
`), 8000);
      case "list_files":
        return textOf(strings(o.files).join(`
`), 8000) || "(no files)";
      case "search_text": {
        const hits = Array.isArray(o.matches) ? o.matches : [];
        const lines = hits.map((hit) => typeof hit === "object" && hit !== null ? `${String(hit.path)}:${String(hit.line)}: ${String(hit.text)}` : "");
        return lines.length ? textOf(lines.join(`
`) + (o.truncated === true ? `
…(more matches)` : ""), 8000) : "(no matches)";
      }
      case "edit_file":
        return typeof o.edited === "string" ? `Edited ${o.edited}` : textOf(JSON.stringify(o, null, 2), 8000);
      case "create_file":
        return typeof o.created === "string" ? `Created ${o.created}` : textOf(JSON.stringify(o, null, 2), 8000);
      case "run_tests": {
        const checks = Array.isArray(o.checks) ? o.checks : [];
        return checks.map((check) => {
          const c = typeof check === "object" && check !== null ? check : {};
          const output = typeof c.output === "string" && c.output ? `
${c.output}` : "";
          return `${String(c.kind)}: ${String(c.status)}${output}`;
        }).join(`

`) || textOf(JSON.stringify(o, null, 2), 8000);
      }
      case "run_command": {
        if (o.status === undefined)
          return `Error: ${textOf(o.error, 2000)}`;
        const lines = [`exit ${o.exit_code === null || o.exit_code === undefined ? "–" : String(o.exit_code)} · ${String(o.status)}`];
        const changed = strings(o.changed_files), rejected = strings(o.rejected_changes);
        if (changed.length)
          lines.push(`Kept changes: ${changed.join(", ")}`);
        if (rejected.length)
          lines.push(`Rejected changes: ${rejected.join(", ")}`);
        if (typeof o.note === "string")
          lines.push(o.note);
        if (typeof o.error === "string")
          lines.push(o.error);
        if (typeof o.output === "string" && o.output)
          lines.push("", o.output);
        return textOf(lines.join(`
`), 12000);
      }
      case "finish":
        return "Finished";
      default:
        return textOf(JSON.stringify(o, null, 2), 8000);
    }
  }
  function eventsToChat(events, objective) {
    const messages = [];
    for (const event of events)
      messages.push(...eventToMessages(event, objective));
    return messages;
  }
  function shortLabel(value, max) {
    if (typeof value !== "string" || CONTROL.test(value))
      return "";
    const text = value.replace(/\s+/g, " ").trim();
    if (text.length <= max)
      return text;
    const cut = text.slice(0, max);
    const space = cut.lastIndexOf(" ");
    return (space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd() + "…";
  }
  function conversationLabel(task) {
    return shortLabel(task.title, 200) || shortLabel(task.objective, 60) || task.id.slice(0, 8);
  }
  function transcriptMarkdown(title, messages, diff) {
    const out = [`# ${title}`, ""];
    for (const msg of messages) {
      const when = new Date(msg.time * 1000).toISOString();
      if (msg.role === "user")
        out.push(`## You · ${when}`, "", msg.body ?? "", "");
      else if (msg.role === "assistant" && msg.body)
        out.push(`## Dikdik · ${when}`, "", msg.body, "");
      else if (msg.state && msg.kind !== "agent_tool_result")
        out.push(`### ${msg.title}`, "", "```json", JSON.stringify(msg.details ?? {}, null, 2), "```", "");
      else {
        out.push(`- **${msg.title}**`);
        if (msg.body)
          out.push("", "  ```", ...msg.body.split(`
`).map((line) => "  " + line), "  ```");
      }
    }
    if (diff)
      out.push("", "## Changes", "", "```diff", diff.trimEnd(), "```", "");
    return out.join(`
`);
  }
  var M4_TEXT = {
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
    unsafeUrlSegment: "This identifier cannot be used in a request URL (empty or contains control characters)."
  };
  var CONTROL_CHARS = /[\u0000-\u001f\u007f]/;
  function safeUrlSegment(value, label = "identifier") {
    if (typeof value !== "string" || !value || CONTROL_CHARS.test(value)) {
      throw new Error(`${M4_TEXT.unsafeUrlSegment} (${label})`);
    }
    return encodeURIComponent(value);
  }
  function apiUrl(base, ...segments) {
    safeUrlSegment(base, "endpoint base");
    return `${base}/${segments.map((segment) => safeUrlSegment(segment)).join("/")}`;
  }
  function buildAutomation(input) {
    const name = input.name.trim();
    if (!name)
      throw new Error("Enter an automation name.");
    const objective = input.objective.trim();
    if (!objective)
      throw new Error("Enter an objective.");
    let schedule;
    if (input.scheduleKind === "daily") {
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(input.dailyAt ?? ""))
        throw new Error("Pick a time of day.");
      schedule = { daily_at: input.dailyAt };
    } else {
      const minutes = Number(String(input.intervalMinutes ?? "").trim());
      if (!Number.isInteger(minutes) || minutes < 1)
        throw new Error(M4_TEXT.intervalRule);
      schedule = { interval_seconds: minutes * 60 };
    }
    if (input.mode !== "model" && input.mode !== "agent")
      throw new Error("Scheduled runs use agent or single-proposal mode.");
    const verification = parseVerification(input.verification);
    if (!input.approveExternal)
      throw new Error("Approve sending code to your model provider for these runs.");
    if (verification === "docker" && !input.approveExecution)
      throw new Error("Approve running tests in Docker for these runs.");
    return {
      name,
      schedule,
      objective,
      mode: input.mode,
      verification,
      approve_external: true,
      approve_execution: verification === "docker",
      ...input.mode === "agent" && input.agentProfile ? { agent_profile: input.agentProfile } : {}
    };
  }
  function describeSchedule(schedule) {
    if ("daily_at" in schedule)
      return `daily at ${schedule.daily_at}`;
    const minutes = Math.round(schedule.interval_seconds / 60);
    if (minutes % 1440 === 0)
      return minutes === 1440 ? "every day" : `every ${minutes / 1440} days`;
    if (minutes % 60 === 0)
      return minutes === 60 ? "every hour" : `every ${minutes / 60} hours`;
    return minutes === 1 ? "every minute" : `every ${minutes} minutes`;
  }
  var buildSkillToggle = (enabled) => ({ enabled });
  function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }
  var safeText = (value) => typeof value === "string" && value !== "" && !CONTROL_CHARS.test(value) ? value : "";
  var SKILL_SCOPES = ["personal", "project"];
  function parseSkills(value) {
    if (!Array.isArray(value))
      throw new Error("Skill list response did not match the expected shape.");
    const skills = [];
    for (const item of value) {
      if (!isRecord(item))
        continue;
      const id = safeText(item.id), name = safeText(item.name), path = item.path, enabled = item.enabled;
      if (!id || !name || enabled !== true && enabled !== false || typeof path !== "string" || CONTROL_CHARS.test(path))
        continue;
      if (!SKILL_SCOPES.includes(item.scope))
        continue;
      const description = safeText(item.description);
      skills.push({ id, name, scope: item.scope, enabled, path, ...description ? { description } : {} });
    }
    return skills;
  }
  function parseAutomations(value) {
    if (!Array.isArray(value))
      throw new Error("Automation list response did not match the expected shape.");
    const automations = [];
    for (const item of value) {
      if (!isRecord(item))
        continue;
      const id = safeText(item.id), name = safeText(item.name), objective = item.objective, workspacePath = item.workspace_path;
      const { enabled, last_state: lastState, next_run: nextRun, failures } = item;
      const raw = isRecord(item.schedule) ? item.schedule : {};
      const schedule = typeof raw.daily_at === "string" ? { daily_at: raw.daily_at } : typeof raw.interval_seconds === "number" && Number.isInteger(raw.interval_seconds) ? { interval_seconds: raw.interval_seconds } : null;
      if (!id || !name || typeof objective !== "string" || objective === "" || typeof workspacePath !== "string" || enabled !== true && enabled !== false || lastState !== null && typeof lastState !== "string" || nextRun !== null && typeof nextRun !== "number" && typeof nextRun !== "string" || typeof failures !== "number" || !Number.isInteger(failures) || schedule === null)
        continue;
      const run = isRecord(item.run) && (item.run.mode === "model" || item.run.mode === "agent") && (item.run.verification === "syntax" || item.run.verification === "docker") ? {
        mode: item.run.mode,
        verification: item.run.verification,
        approve_external: item.run.approve_external === true,
        approve_execution: item.run.approve_execution === true,
        ...typeof item.run.agent_profile === "string" ? { agent_profile: item.run.agent_profile } : {}
      } : null;
      automations.push({
        id,
        name,
        schedule,
        workspace_path: workspacePath,
        objective,
        enabled,
        last_state: lastState,
        next_run: nextRun,
        failures,
        run,
        last_task_id: safeText(item.last_task_id) || null
      });
    }
    return automations;
  }
  function parseRunSummaries(value) {
    if (!Array.isArray(value))
      throw new Error("Runs response did not match the expected shape.");
    const runs = [];
    for (const item of value) {
      if (!isRecord(item))
        continue;
      const id = safeText(item.task_id), state = safeText(item.state) || "deleted";
      if (!id)
        continue;
      runs.push({ id, state, created: typeof item.created === "number" ? item.created : null });
    }
    return runs;
  }
  function m4el(create, tag, className = "", text = "") {
    const node = create(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }
  function m4button(create, container, labelText, type) {
    const button = m4el(create, "button", type === "submit" ? "" : "secondary", labelText);
    button.type = type;
    container.append(button);
    return button;
  }
  function m4toggler(create, container, label, checked) {
    const box = m4el(create, "input");
    box.type = "checkbox";
    box.setAttribute("aria-label", label);
    box.checked = checked;
    container.append(box);
    return box;
  }
  function skillsPanel({ root, create, request }) {
    let skills = [];
    let loading = false;
    const note = m4el(create, "p", "hint", M4_TEXT.skillsNote);
    const actions = m4el(create, "p", "row");
    const refreshButton = m4button(create, actions, M4_TEXT.refreshSkills, "button");
    const statusLine = m4el(create, "p", "hint");
    statusLine.setAttribute("role", "status");
    statusLine.setAttribute("aria-live", "polite");
    const errorLine = m4el(create, "p", "error");
    errorLine.setAttribute("role", "alert");
    const list = m4el(create, "ol", "m4-list");
    root.append(note, actions, statusLine, errorLine, list);
    const failMessage = (error) => error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
    const showStatus = (message) => {
      statusLine.textContent = message;
      statusLine.hidden = false;
    };
    const showError = (message) => {
      errorLine.textContent = message;
      errorLine.hidden = false;
    };
    const clearFeedback = () => {
      statusLine.hidden = true;
      errorLine.hidden = true;
    };
    const refresh = async () => {
      if (loading)
        return;
      loading = true;
      refreshButton.disabled = true;
      clearFeedback();
      try {
        skills = parseSkills(await request("/api/skills", "GET"));
        renderList();
        if (!skills.length)
          showStatus(M4_TEXT.emptySkills);
      } catch (error) {
        showError(failMessage(error));
      } finally {
        loading = false;
        refreshButton.disabled = false;
      }
    };
    refreshButton.onclick = () => void refresh();
    const removeSkill = async (id) => {
      clearFeedback();
      try {
        await request(apiUrl("/api/skills", id), "DELETE");
        showStatus("Skill deleted.");
        await refresh();
      } catch (error) {
        showError(failMessage(error));
      }
    };
    const toggle = async (id, enabled) => {
      clearFeedback();
      try {
        await request(apiUrl("/api/skills", id), "PUT", buildSkillToggle(enabled));
        showStatus(M4_TEXT.skillToggleSaved);
      } catch (error) {
        const skill = skills.find((item) => item.id === id);
        if (skill) {
          skill.enabled = !enabled;
          renderList();
        }
        showError(failMessage(error));
      }
    };
    function renderList() {
      const rows = [];
      for (const skill of skills) {
        const row = m4el(create, "li", "m4-row");
        const head = m4el(create, "p", "m4-title");
        head.append(m4el(create, "span", "m4-strong", skill.name), m4el(create, "span", "m4-badge", skill.scope));
        row.append(head);
        if (skill.description)
          row.append(m4el(create, "p", "m4-objective", skill.description));
        row.append(m4el(create, "span", "m4-meta m4-mono", skill.path));
        const actionsRow = m4el(create, "span", "m4-actions");
        const toggleBox = m4toggler(create, actionsRow, `Enable skill ${skill.name}`, skill.enabled);
        toggleBox.onchange = () => {
          skill.enabled = toggleBox.checked;
          renderList();
          toggle(skill.id, toggleBox.checked);
        };
        if (!skill.path.endsWith("SKILL.md")) {
          const remove = m4el(create, "button", "secondary", M4_TEXT.delete);
          remove.type = "button";
          remove.onclick = () => {
            removeSkill(skill.id);
          };
          actionsRow.append(remove);
        }
        row.append(actionsRow);
        rows.push(row);
      }
      list.replaceChildren(...rows);
    }
    return { refresh };
  }
  function resultStatus(state, result) {
    if (result && typeof result.error === "string" && result.error)
      return result.error;
    if (state === "paused")
      return "Paused. Resume to continue from the exact step where it stopped.";
    if (result?.tests_verified === true)
      return "Container tests passed. Review the diff; this is not proof of every acceptance criterion.";
    if (result) {
      const checks = (Array.isArray(result.checks) ? result.checks : []).filter((check) => check && typeof check.kind === "string" && typeof check.status === "string").map((check) => `${check.kind}: ${check.status}`).join(" · ");
      return `NOT TEST-VERIFIED. ${checks ? checks + ". " : ""}Source repository unchanged. Inspect the check evidence above.`;
    }
    return isTerminal(state) ? "Task stopped without a result. Inspect the persisted events above." : "Work is running. Events are persisted backend activity.";
  }

  // dikdik/web/drafts.ts
  function parseDraft(value) {
    if (typeof value !== "object" || value === null)
      return null;
    const row = value;
    if (typeof row.scope !== "string" || !/^(home|[a-f0-9]{32})$/.test(row.scope) || typeof row.content !== "string" || row.content.length > 8000 || row.content.includes("\x00") || typeof row.revision !== "number" || !Number.isSafeInteger(row.revision) || row.revision < 0 || typeof row.updated !== "number" || !Number.isFinite(row.updated))
      return null;
    return { scope: row.scope, content: row.content, revision: row.revision, updated: row.updated };
  }

  class ComposerDrafts {
    request;
    render;
    scope = "home";
    entries = new Map;
    constructor(request, render) {
      this.request = request;
      this.render = render;
    }
    entry(scope) {
      let entry = this.entries.get(scope);
      if (!entry) {
        entry = { scope, content: "", revision: null, dirty: false, sequence: 0, conflict: null, error: false, loading: null, saving: null };
        this.entries.set(scope, entry);
      }
      return entry;
    }
    paint(entry, apply = false) {
      if (entry.scope !== this.scope)
        return;
      const state = entry.conflict ? "conflict" : entry.error ? "unsaved" : entry.saving || entry.dirty ? "saving" : entry.loading ? "loading" : "saved";
      this.render(apply ? entry.content : null, { state, dirty: entry.dirty || !!entry.conflict, ...entry.conflict ? { remote: entry.conflict.content } : {} });
    }
    display() {
      this.paint(this.entry(this.scope), true);
    }
    hasUnsaved() {
      return [...this.entries.values()].some((entry) => entry.dirty || entry.conflict);
    }
    async select(scope) {
      const previous = this.scope;
      if (previous !== scope)
        this.flush(previous);
      this.scope = scope;
      const entry = this.entry(scope);
      this.paint(entry, true);
      await this.load(entry);
    }
    async load(entry) {
      if (entry.loading)
        return entry.loading;
      entry.loading = (async () => {
        await Promise.resolve();
        if (entry.saving)
          await entry.saving;
        try {
          const row = parseDraft(await this.request(`/api/drafts/${entry.scope}`, "GET"));
          if (!row || row.scope !== entry.scope)
            throw new Error("Invalid draft response");
          if (entry.dirty && row.content !== entry.content && (entry.revision === null ? !!row.content : entry.revision !== row.revision))
            entry.conflict = row;
          else {
            entry.revision = row.revision;
            if (!entry.dirty)
              entry.content = row.content;
            else if (row.content === entry.content)
              entry.dirty = false;
          }
          entry.error = false;
        } catch {
          entry.error = true;
        } finally {
          entry.loading = null;
          this.paint(entry, true);
        }
      })();
      this.paint(entry);
      return entry.loading;
    }
    update(content) {
      const entry = this.entry(this.scope);
      entry.content = content;
      entry.dirty = true;
      entry.sequence++;
      entry.error = false;
      this.paint(entry);
      this.schedule(entry);
    }
    schedule(entry) {
      if (entry.timer !== undefined)
        clearTimeout(entry.timer);
      entry.timer = setTimeout(() => {
        entry.timer = undefined;
        this.flush(entry.scope);
      }, 300);
    }
    async flush(scope = this.scope) {
      const entry = this.entry(scope);
      if (entry.timer !== undefined) {
        clearTimeout(entry.timer);
        entry.timer = undefined;
      }
      if (!entry.dirty || entry.conflict)
        return;
      if (entry.saving)
        return entry.saving;
      if (entry.revision === null)
        await this.load(entry);
      if (entry.saving)
        return entry.saving;
      if (!entry.dirty)
        return;
      if (entry.revision === null || entry.conflict || entry.error) {
        this.paint(entry);
        return;
      }
      entry.saving = (async () => {
        await Promise.resolve();
        try {
          for (let attempt = 0;attempt < 3 && entry.dirty && !entry.conflict; attempt++) {
            const { content, sequence, revision } = entry;
            const row = parseDraft(await this.request(`/api/drafts/${scope}`, "PATCH", { content, revision }));
            if (!row || row.scope !== scope || row.revision <= revision)
              throw new Error("Invalid draft response");
            entry.revision = row.revision;
            entry.error = false;
            if (entry.sequence === sequence && entry.content === content)
              entry.dirty = false;
          }
        } catch (error) {
          const failure = error;
          const current = failure?.status === 409 ? parseDraft(failure.data?.current) : null;
          if (current && current.scope === scope)
            entry.conflict = current;
          else
            entry.error = true;
        } finally {
          entry.saving = null;
          this.paint(entry);
          if (entry.dirty && !entry.conflict && !entry.error)
            this.schedule(entry);
        }
      })();
      this.paint(entry);
      return entry.saving;
    }
    async resolve(keep) {
      const entry = this.entry(this.scope), current = entry.conflict;
      if (!current) {
        entry.error = false;
        await this.flush();
        return;
      }
      entry.revision = current.revision;
      entry.conflict = null;
      entry.error = false;
      entry.sequence++;
      if (keep === "server") {
        entry.content = current.content;
        entry.dirty = false;
        this.paint(entry, true);
      } else {
        entry.dirty = true;
        await this.flush();
      }
    }
    async sent(scope, content) {
      const entry = this.entry(scope);
      if (entry.content !== content || entry.conflict)
        return;
      entry.content = "";
      entry.dirty = true;
      entry.sequence++;
      this.paint(entry, true);
      await this.flush(scope);
    }
  }

  // dikdik/web/models-pane.ts
  var PROVIDERS = ["anthropic", "openai", "azure", "local"];
  function optionalNumber(value, label, integer = false) {
    const text = value.trim();
    if (!text)
      return null;
    const number = Number(text);
    if (!Number.isFinite(number) || integer && !Number.isInteger(number))
      throw new Error(`${label} must be ${integer ? "a whole number" : "a number"}.`);
    return number;
  }
  function profileBody(fields) {
    if (!fields.name.trim())
      throw new Error("Give the profile a name.");
    if (!fields.model.trim())
      throw new Error("Choose or type a model.");
    const temperature = optionalNumber(fields.temperature, "Temperature"), topP = optionalNumber(fields.topP, "Top P");
    if (temperature !== null && (temperature < 0 || temperature > 2))
      throw new Error("Temperature must be 0–2.");
    if (topP !== null && (topP < 0 || topP > 1))
      throw new Error("Top P must be 0–1.");
    const maxTokens = optionalNumber(fields.maxTokens, "Max output tokens", true);
    if (maxTokens !== null && (maxTokens < 1 || maxTokens > 32000))
      throw new Error("Max output tokens must be 1–32000.");
    const timeout = optionalNumber(fields.timeout, "Timeout", true);
    return {
      name: fields.name.trim(),
      provider: fields.provider,
      model: fields.model.trim(),
      endpoint: fields.endpoint.trim() || null,
      ...timeout !== null ? { timeout_seconds: timeout } : {},
      connection_id: fields.connection || null,
      credential_secret_ref: fields.connection ? null : fields.keyRef || null,
      temperature,
      top_p: topP,
      max_output_tokens: maxTokens
    };
  }
  function when(seconds) {
    if (!seconds)
      return "";
    return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  function modelsPane(root, deps) {
    const { request, make, message } = deps;
    let connections = [], profiles = [], routers = [], keys = [];
    let strategies = {}, catalog = { default_provider: "anthropic", providers: {} }, activeRouter = "";
    let editingConnection = null, editingProfile = null, confirming = "";
    const tests = new Map;
    const fail = (error) => message(error instanceof Error ? error.message : "Something went wrong", true);
    const button = (label, className = "secondary", onclick) => {
      const node = make("button", className, label);
      node.type = "button";
      if (onclick)
        node.onclick = onclick;
      return node;
    };
    const labelled = (grid, text, control, id) => {
      const label = make("label", "", text);
      label.htmlFor = id;
      control.id = id;
      grid.append(label, control);
      return control;
    };
    const input = (value = "", placeholder = "", type = "text") => {
      const node = make("input");
      node.type = type;
      node.value = value;
      node.placeholder = placeholder;
      node.autocomplete = "off";
      return node;
    };
    const choice = (options, value) => {
      const node = make("select");
      for (const [key, text] of options) {
        const option = make("option", "", text);
        option.value = key;
        node.append(option);
      }
      node.value = value;
      return node;
    };
    const section = (title, hint, action) => {
      const box = make("section", "pane-block");
      const head = make("div", "block-head");
      head.append(make("h4", "", title));
      if (action)
        head.append(action);
      box.append(head);
      if (hint)
        box.append(make("p", "hint", hint));
      return box;
    };
    async function refresh() {
      try {
        const [c, p, r, k, cat, settings] = await Promise.all([
          request("/api/connections", "GET"),
          request("/api/profiles", "GET"),
          request("/api/routers", "GET"),
          request("/api/secrets", "GET").catch(() => []),
          request("/api/model-catalog", "GET"),
          request("/api/settings/app", "GET")
        ]);
        connections = c;
        profiles = p;
        routers = r.routers;
        strategies = r.strategies;
        keys = k;
        catalog = cat;
        activeRouter = settings.router ?? "";
        render();
      } catch (error) {
        root.replaceChildren(make("p", "error-text", `Could not load model settings: ${error instanceof Error ? error.message : "unknown error"}`));
      }
    }
    async function act(work, done) {
      try {
        await work();
        if (done)
          message(done);
        confirming = "";
        await refresh();
        deps.changed?.();
      } catch (error) {
        fail(error);
      }
    }
    function connectionForm(current) {
      const form = make("form", "skill-form");
      const grid = make("div", "skill-fields");
      const name = labelled(grid, "Name", input(current?.name ?? "", "Work Anthropic account"), "conn-name");
      const provider = labelled(grid, "Provider", choice(PROVIDERS.map((p) => [p, catalog.providers[p]?.name ?? p]), current?.provider ?? catalog.default_provider), "conn-provider");
      provider.disabled = current !== null;
      const key = labelled(grid, "API key", input("", current ? "Leave blank to keep the current key" : "Paste the key", "password"), "conn-key");
      const endpoint = labelled(grid, "Endpoint", input(current?.endpoint ?? "", "Optional: a local server, e.g. http://127.0.0.1:8080/v1"), "conn-endpoint");
      const help = make("p", "hint");
      const syncHelp = () => {
        const url = catalog.providers[provider.value]?.key_help;
        help.replaceChildren();
        if (url) {
          const link = make("a", "", "Where do I find my API key?");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          help.append(link);
        }
      };
      provider.onchange = syncHelp;
      syncHelp();
      const row = make("div", "row");
      row.append(button(current ? "Save connection" : "Add connection", "primary small"), button("Cancel", "ghost small", () => {
        editingConnection = null;
        render();
      }));
      row.firstChild.type = "submit";
      form.append(grid, help, row);
      form.onsubmit = (event) => {
        event.preventDefault();
        const body = current ? { name: name.value, endpoint: endpoint.value.trim() || null, ...key.value ? { api_key: key.value } : {} } : { name: name.value, provider: provider.value, api_key: key.value, endpoint: endpoint.value.trim() || null };
        act(async () => {
          await request(current ? `/api/connections/${current.id}` : "/api/connections", current ? "PATCH" : "POST", body);
          editingConnection = null;
        }, current ? key.value ? "Connection saved and key rotated." : "Connection saved." : `Connection “${name.value.trim()}” created.`);
      };
      setTimeout(() => name.focus(), 0);
      return form;
    }
    function connectionsBlock() {
      const box = section("Provider connections", "Share one API key across several model profiles. Keys are encrypted on this machine and never shown again.", button("Add connection", "small", () => {
        editingConnection = "new";
        render();
      }));
      if (editingConnection === "new")
        box.append(connectionForm(null));
      const list = make("ul", "m4-list");
      for (const item of connections) {
        const row = make("li", "m4-row");
        const head = make("p", "m4-title");
        head.append(make("span", "m4-strong", item.name), make("span", item.key_set ? "m4-badge" : "m4-badge warn-badge", item.key_set ? "Key set" : "No key"));
        row.append(head, make("span", "m4-meta", `${catalog.providers[item.provider]?.name ?? item.provider} · ${item.models} model${item.models === 1 ? "" : "s"}${item.endpoint ? ` · ${item.endpoint}` : ""}`));
        const actions = make("span", "m4-actions");
        actions.append(button("Edit", "secondary", () => {
          editingConnection = item.id;
          render();
        }));
        const confirmKey = `conn:${item.id}`;
        actions.append(button(confirming === confirmKey ? "Delete for good" : "Delete", confirming === confirmKey ? "danger" : "secondary", () => {
          if (confirming !== confirmKey) {
            confirming = confirmKey;
            render();
            return;
          }
          act(() => request(`/api/connections/${item.id}`, "DELETE"), "Connection deleted.");
        }));
        row.append(actions);
        if (editingConnection === item.id)
          row.append(connectionForm(item));
        list.append(row);
      }
      if (!connections.length && editingConnection !== "new")
        list.append(make("li", "hint", "No connections yet. Profiles can also use a key from API keys directly."));
      box.append(list);
      return box;
    }
    function profileForm(current) {
      const form = make("form", "skill-form");
      const grid = make("div", "skill-fields");
      const provider = choice(PROVIDERS.map((p) => [p, catalog.providers[p]?.name ?? p]), current?.provider ?? catalog.default_provider);
      const firstModel = () => catalog.providers[provider.value]?.models[0] ?? "";
      const name = labelled(grid, "Name", input(current?.name ?? "", "Letters, digits, . _ - (up to 64)"), "prof-name");
      labelled(grid, "Provider", provider, "prof-provider");
      const list = make("datalist");
      list.id = "prof-models";
      const model = labelled(grid, "Model", input(current?.model ?? firstModel(), "Pick a suggestion or type any model name"), "prof-model");
      model.setAttribute("list", "prof-models");
      const connection = labelled(grid, "Connection", choice([["", "None — use an API key below"], ...connections.map((c) => [c.id, `${c.name} (${catalog.providers[c.provider]?.name ?? c.provider})`])], current?.connection_id ?? ""), "prof-connection");
      const keyRef = labelled(grid, "API key", choice([["", provider.value === "local" ? "None (keyless local server)" : "Choose a stored key"], ...keys.map((k) => [k.ref, k.ref])], current?.key_ref ?? ""), "prof-key");
      const endpoint = labelled(grid, "Endpoint", input(current?.endpoint ?? "", "Optional override"), "prof-endpoint");
      const advanced = make("details", "advanced");
      advanced.append(make("summary", "", "Advanced"));
      const more = make("div", "skill-fields");
      const timeout = labelled(more, "Timeout (s)", input(current ? String(current.timeout_seconds) : "", "60", "number"), "prof-timeout");
      const temperature = labelled(more, "Temperature", input(current?.temperature?.toString() ?? "", "Provider default (0–2)", "number"), "prof-temperature");
      temperature.step = "0.1";
      const topP = labelled(more, "Top P", input(current?.top_p?.toString() ?? "", "Provider default (0–1)", "number"), "prof-top-p");
      topP.step = "0.05";
      const maxTokens = labelled(more, "Max output tokens", input(current?.max_output_tokens?.toString() ?? "", "Task default", "number"), "prof-max-tokens");
      advanced.append(more);
      if (current && (current.temperature !== null || current.top_p !== null || current.max_output_tokens !== null))
        advanced.open = true;
      const help = make("p", "hint");
      const sync = (providerChanged) => {
        list.replaceChildren(...(catalog.providers[provider.value]?.models ?? []).map((m) => {
          const option = make("option");
          option.value = m;
          return option;
        }));
        if (providerChanged && !current)
          model.value = firstModel();
        keyRef.disabled = connection.value !== "";
        const url = catalog.providers[provider.value]?.key_help;
        help.replaceChildren();
        if (url && !connection.value) {
          const link = make("a", "", "Don’t know your API key? Here is where to find it.");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          help.append(link);
        }
      };
      provider.onchange = () => sync(true);
      connection.onchange = () => {
        const linked = connections.find((c) => c.id === connection.value);
        if (linked)
          provider.value = linked.provider;
        sync(true);
      };
      sync(false);
      const row = make("div", "row");
      const save = button(current ? "Save profile" : "Add profile", "primary small");
      save.type = "submit";
      row.append(save, button("Cancel", "ghost small", () => {
        editingProfile = null;
        render();
      }));
      form.append(grid, list, advanced, help, row);
      form.onsubmit = (event) => {
        event.preventDefault();
        let body;
        try {
          body = profileBody({ name: name.value, provider: provider.value, model: model.value, endpoint: endpoint.value, timeout: timeout.value, connection: connection.value, keyRef: keyRef.value, temperature: temperature.value, topP: topP.value, maxTokens: maxTokens.value });
        } catch (error) {
          fail(error);
          return;
        }
        if (!current) {
          for (const key of ["temperature", "top_p", "max_output_tokens", "connection_id", "credential_secret_ref", "endpoint"])
            if (body[key] === null)
              delete body[key];
        }
        act(async () => {
          await request(current ? `/api/profiles/${current.id}` : "/api/profiles", current ? "PATCH" : "POST", body);
          editingProfile = null;
        }, current ? "Profile saved." : `Profile “${name.value.trim()}” added.`);
      };
      setTimeout(() => name.focus(), 0);
      return form;
    }
    function profilesBlock() {
      const box = section("Model profiles", "Each profile is a model with its key. The default is used unless a conversation, agent profile or router picks another.", button("Add profile", "small", () => {
        editingProfile = "new";
        render();
      }));
      if (editingProfile === "new")
        box.append(profileForm(null));
      const list = make("ul", "m4-list");
      for (const item of profiles) {
        const row = make("li", "m4-row");
        const head = make("p", "m4-title");
        head.append(make("span", "m4-strong", item.name));
        if (item.is_default)
          head.append(make("span", "m4-badge", "Default"));
        const link = connections.find((c) => c.id === item.connection_id);
        if (item.connection_id && !link)
          head.append(make("span", "m4-badge warn-badge", "Broken link"));
        row.append(head);
        const via = link ? `via ${link.name}` : item.key_ref ? `key ${item.key_ref}` : item.provider === "local" ? "no key" : "no key set";
        const extras = [item.temperature !== null ? `temperature ${item.temperature}` : "", item.top_p !== null ? `top P ${item.top_p}` : "", item.max_output_tokens ? `≤${item.max_output_tokens} tokens` : ""].filter(Boolean).join(" · ");
        row.append(make("span", "m4-meta", `${catalog.providers[item.provider]?.name ?? item.provider} · ${item.model} · ${via}${extras ? " · " + extras : ""}`));
        if (tests.has(item.id))
          row.append(make("span", "m4-meta test-result", tests.get(item.id) ?? ""));
        const actions = make("span", "m4-actions");
        const test = button("Test", "secondary", () => {
          test.disabled = true;
          test.textContent = "Testing…";
          request(`/api/profiles/${item.id}/test`, "POST", {}).then((result) => tests.set(item.id, `✓ ${result.model} answered in ${result.seconds}s: “${result.reply.trim().slice(0, 60)}”`)).catch((error) => tests.set(item.id, `✗ ${error instanceof Error ? error.message : "Test failed"}`)).finally(render);
        });
        test.title = "Sends one tiny request (“Reply with OK”) with this profile’s key. Nothing from your workspace is sent.";
        actions.append(test, button("Edit", "secondary", () => {
          editingProfile = item.id;
          render();
        }), button("Duplicate", "secondary", () => {
          act(() => request(`/api/profiles/${item.id}/duplicate`, "POST", {}), "Profile duplicated.");
        }));
        if (!item.is_default)
          actions.append(button("Set as default", "secondary", () => {
            act(() => request(`/api/profiles/${item.id}`, "PATCH", { is_default: true }), `${item.name} is now the default.`);
          }));
        const confirmKey = `prof:${item.id}`;
        actions.append(button(confirming === confirmKey ? "Delete for good" : "Delete", confirming === confirmKey ? "danger" : "secondary", () => {
          if (confirming !== confirmKey) {
            confirming = confirmKey;
            render();
            return;
          }
          act(() => request(`/api/profiles/${item.id}${item.is_default ? "?confirm_default=1" : ""}`, "DELETE"), "Profile deleted.");
        }));
        row.append(actions);
        if (editingProfile === item.id)
          row.append(profileForm(item));
        list.append(row);
      }
      if (!profiles.length && editingProfile !== "new")
        list.append(make("li", "hint", "No profiles yet. Without one, the server's DIKDIK_MODEL_* environment variables are used if set."));
      box.append(list);
      return box;
    }
    function routingBlock() {
      const box = section("Model routing", "A router picks a model per message when nothing else chose one. Each turn records the model it used, so you can compare; nothing here claims one model is better.");
      const activeRow = make("div", "skill-fields");
      const active = labelled(activeRow, "Active router", choice([["", "Off — use the default profile"], ...routers.map((r) => [r.id, r.name])], activeRouter), "router-active");
      active.onchange = () => {
        act(() => request("/api/settings/app", "PATCH", { router: active.value }), active.value ? "Router on." : "Routing off.");
      };
      box.append(activeRow);
      const list = make("ul", "m4-list");
      for (const item of routers) {
        const row = make("li", "m4-row");
        const head = make("p", "m4-title");
        head.append(make("span", "m4-strong", item.name));
        if (item.id === activeRouter)
          head.append(make("span", "m4-badge", "Active"));
        if (item.broken)
          head.append(make("span", "m4-badge warn-badge", "Profile missing"));
        row.append(head, make("span", "m4-meta", `${strategies[item.strategy] ?? item.strategy}${item.threshold ? ` (${item.threshold})` : ""}. Strong: ${item.strong_name || "missing"} · Fast: ${item.fast_name || "missing"}`));
        const confirmKey = `router:${item.id}`;
        const actions = make("span", "m4-actions");
        actions.append(button(confirming === confirmKey ? "Delete for good" : "Delete", confirming === confirmKey ? "danger" : "secondary", () => {
          if (confirming !== confirmKey) {
            confirming = confirmKey;
            render();
            return;
          }
          act(() => request(`/api/routers/${item.id}`, "DELETE"), "Router deleted.");
        }));
        row.append(actions);
        list.append(row);
      }
      box.append(list);
      if (profiles.length >= 2) {
        const form = make("form", "skill-form");
        const grid = make("div", "skill-fields");
        const name = labelled(grid, "Name", input("", "Strong first, fast after"), "router-name");
        const strategy = labelled(grid, "Rule", choice(Object.entries(strategies), "first_turn"), "router-strategy");
        const options = profiles.map((p) => [p.id, `${p.name} (${p.model})`]);
        const strong = labelled(grid, "Strong model", choice(options, profiles[0].id), "router-strong");
        const fast = labelled(grid, "Fast model", choice(options, profiles[1].id), "router-fast");
        const threshold = labelled(grid, "Length threshold", input("400", "characters", "number"), "router-threshold");
        const sync = () => {
          threshold.disabled = strategy.value !== "long_message";
        };
        strategy.onchange = sync;
        sync();
        const save = button("Add router", "small");
        save.type = "submit";
        form.append(grid, save);
        form.onsubmit = (event) => {
          event.preventDefault();
          const body = { name: name.value, strategy: strategy.value, strong_profile_id: strong.value, fast_profile_id: fast.value, ...strategy.value === "long_message" ? { threshold: Number(threshold.value) } : {} };
          act(() => request("/api/routers", "POST", body), `Router “${name.value.trim()}” added. Turn it on above.`);
        };
        box.append(form);
      } else
        box.append(make("p", "hint", "Add at least two model profiles to build a router."));
      return box;
    }
    function render() {
      root.replaceChildren(connectionsBlock(), profilesBlock(), routingBlock());
    }
    return refresh;
  }
  function keysPane(root, deps) {
    const { request, make, message } = deps;
    let keys = [], confirming = "", renaming = "";
    const button = (label, className = "secondary", onclick) => {
      const node = make("button", className, label);
      node.type = "button";
      if (onclick)
        node.onclick = onclick;
      return node;
    };
    async function act(work, done) {
      try {
        await work();
        message(done);
        confirming = "";
        renaming = "";
        await refresh();
        deps.changed?.();
      } catch (error) {
        message(error instanceof Error ? error.message : "Something went wrong", true);
      }
    }
    async function refresh() {
      try {
        keys = await request("/api/secrets", "GET");
        render();
      } catch (error) {
        root.replaceChildren(make("p", "error-text", `Could not open the key store: ${error instanceof Error ? error.message : "unknown error"}`));
      }
    }
    function render() {
      const form = make("form", "skill-form");
      const grid = make("div", "skill-fields");
      const nameLabel = make("label", "", "Name");
      nameLabel.htmlFor = "key-name";
      const name = make("input");
      name.id = "key-name";
      name.placeholder = "anthropic-work";
      name.autocomplete = "off";
      name.maxLength = 64;
      const valueLabel = make("label", "", "Value");
      valueLabel.htmlFor = "key-value";
      const value = make("input");
      value.id = "key-value";
      value.type = "password";
      value.placeholder = "Paste the key or token";
      value.autocomplete = "off";
      grid.append(nameLabel, name, valueLabel, value);
      const save = button("Save key", "small");
      save.type = "submit";
      form.append(grid, make("p", "hint", "Saving under an existing name replaces its value; the old value is kept once so you can undo."), save);
      form.onsubmit = (event) => {
        event.preventDefault();
        if (!value.value.trim()) {
          message("A key cannot be empty; delete it instead.", true);
          return;
        }
        act(() => request("/api/secrets", "POST", { ref: name.value.trim(), plaintext: value.value }), `Saved “${name.value.trim()}”.`);
      };
      const list = make("ul", "m4-list");
      for (const item of keys) {
        const row = make("li", "m4-row");
        const head = make("p", "m4-title");
        head.append(make("span", "m4-strong m4-mono", item.ref));
        if (item.env)
          head.append(make("span", "m4-badge", `Sandbox: ${item.env}`));
        row.append(head, make("span", "m4-meta", `Saved ${when(item.set_at)}${item.previous_set_at ? ` · previous value from ${when(item.previous_set_at)} kept` : ""}`));
        const actions = make("span", "m4-actions");
        if (item.previous_set_at)
          actions.append(button("Undo last change", "secondary", () => {
            act(() => request(`/api/secrets/${encodeURIComponent(item.ref)}/restore`, "POST", {}), "Previous value restored.");
          }));
        actions.append(button("Rename", "secondary", () => {
          renaming = item.ref;
          render();
        }));
        const env = make("input");
        env.className = "env-input";
        env.value = item.env;
        env.placeholder = "VARIABLE_NAME";
        env.setAttribute("aria-label", `Sandbox variable for ${item.ref}`);
        const envSave = button(item.env ? "Update variable" : "Expose to sandbox", "secondary", () => {
          act(() => request(`/api/secrets/${encodeURIComponent(item.ref)}`, "PATCH", { env: env.value.trim() || null }), env.value.trim() ? `Tasks that allow it will see ${env.value.trim()}.` : "No longer exposed to the sandbox.");
        });
        envSave.title = "Agent commands in Docker can read this as an environment variable, only in tasks where you allow sandbox secrets. Output is redacted.";
        actions.append(env, envSave);
        const confirmKey = `key:${item.ref}`;
        actions.append(button(confirming === confirmKey ? "Delete for good" : "Delete", confirming === confirmKey ? "danger" : "secondary", () => {
          if (confirming !== confirmKey) {
            confirming = confirmKey;
            render();
            return;
          }
          act(() => request(`/api/secrets/${encodeURIComponent(item.ref)}`, "DELETE"), "Key deleted.");
        }));
        row.append(actions);
        if (renaming === item.ref) {
          const rename = make("form", "m4-actions");
          const to = make("input");
          to.value = item.ref;
          to.setAttribute("aria-label", "New name");
          to.maxLength = 64;
          const go = button("Save name", "small");
          go.type = "submit";
          rename.append(to, go, button("Cancel", "ghost small", () => {
            renaming = "";
            render();
          }));
          rename.onsubmit = (event) => {
            event.preventDefault();
            act(() => request(`/api/secrets/${encodeURIComponent(item.ref)}/rename`, "POST", { to: to.value.trim() }), "Renamed. Profiles and MCP servers that used it now use the new name.");
          };
          row.append(rename);
          setTimeout(() => to.focus(), 0);
        }
        list.append(row);
      }
      if (!keys.length)
        list.append(make("li", "hint", "No keys stored yet."));
      root.replaceChildren(form, list);
    }
    return refresh;
  }

  // dikdik/web/organise.ts
  function taskTitle(task) {
    return (task.title || task.objective || "").trim();
  }
  function organiseTasks(tasks, view) {
    const query = view.query.trim().toLowerCase();
    const cutoff = view.sinceDays > 0 ? view.now - view.sinceDays * 86400 : 0;
    let older = 0;
    const visible = tasks.filter((task) => {
      if (task.id === view.selected)
        return true;
      if (!view.showArchived && task.archived)
        return false;
      if (view.tag && !(task.tags ?? []).includes(view.tag))
        return false;
      if (query && !`${taskTitle(task)} ${task.folder ?? ""} ${(task.tags ?? []).join(" ")}`.toLowerCase().includes(query))
        return false;
      if (cutoff && (task.updated ?? task.created ?? 0) < cutoff && !task.pinned) {
        older++;
        return false;
      }
      return true;
    });
    const order = (a, b) => view.sort === "title" ? taskTitle(a).localeCompare(taskTitle(b), undefined, { sensitivity: "base" }) : view.sort === "created" ? (b.created ?? 0) - (a.created ?? 0) : (b.updated ?? b.created ?? 0) - (a.updated ?? a.created ?? 0);
    visible.sort(order);
    const pinned = visible.filter((task) => task.pinned);
    const filed = new Map;
    const recent = [];
    for (const task of visible) {
      if (task.pinned)
        continue;
      if (task.folder)
        filed.set(task.folder, [...filed.get(task.folder) ?? [], task]);
      else
        recent.push(task);
    }
    const folders = [...filed].sort(([a], [b]) => a.localeCompare(b, undefined, { sensitivity: "base" })).map(([name, items]) => ({ name, tasks: items }));
    const tags = [...new Set(tasks.flatMap((task) => task.tags ?? []))].sort();
    const folderNames = [...new Set(tasks.map((task) => task.folder ?? "").filter(Boolean))].sort();
    return { pinned, folders, recent, tags, folderNames, older, archived: tasks.filter((task) => task.archived).length };
  }
  function parseTags(text) {
    const tags = [...new Set(text.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
    if (tags.length > 10)
      throw new Error("Use at most 10 tags.");
    for (const tag of tags)
      if (!/^[\p{L}\p{N}_][\p{L}\p{N}_ -]{0,31}$/u.test(tag))
        throw new Error(`“${tag}” is not a valid tag: use letters, digits, spaces, - or _ (up to 32).`);
    return tags.sort();
  }
  function fuzzyScore(query, text) {
    const q = query.trim().toLowerCase(), t = text.toLowerCase();
    if (!q)
      return 0;
    if (t.startsWith(q))
      return 1000 - t.length;
    const word = t.indexOf(" " + q);
    if (word >= 0)
      return 800 - word;
    const inside = t.indexOf(q);
    if (inside >= 0)
      return 600 - inside;
    let score = 300, at = 0;
    for (const char of q) {
      const found = t.indexOf(char, at);
      if (found < 0)
        return -1;
      score -= found - at;
      at = found + 1;
    }
    return Math.max(1, score);
  }
  function rankCommands(query, items, limit = 50) {
    if (!query.trim())
      return items.slice(0, limit);
    return items.map((item) => ({ item, score: Math.max(fuzzyScore(query, item.label), item.keywords ? fuzzyScore(query, item.keywords) - 50 : -1) })).filter((entry) => entry.score >= 0).sort((a, b) => b.score - a.score).slice(0, limit).map((entry) => entry.item);
  }
  function fileTree(paths, expanded) {
    const root = { dirs: new Map, files: [] };
    for (const path of paths) {
      const parts = path.split("/").filter(Boolean);
      let node = root;
      for (const part of parts.slice(0, -1)) {
        if (!node.dirs.has(part))
          node.dirs.set(part, { dirs: new Map, files: [] });
        node = node.dirs.get(part);
      }
      if (parts.length)
        node.files.push(parts[parts.length - 1]);
    }
    const rows = [];
    const walk = (node, prefix, depth) => {
      for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
        const path = prefix + name, open = expanded.has(path);
        rows.push({ path, name, depth, dir: true, open });
        if (open)
          walk(node.dirs.get(name), path + "/", depth + 1);
      }
      for (const name of [...node.files].sort((a, b) => a.localeCompare(b)))
        rows.push({ path: prefix + name, name, depth, dir: false, open: false });
    };
    walk(root, "", 0);
    return rows;
  }
  function parentFolders(path) {
    const parts = path.split("/");
    return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
  }
  function alertKind(line) {
    const match = /^\s*>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i.exec(line);
    return match ? match[1].toLowerCase() : null;
  }
  function cells(line) {
    const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
  }
  function parseTable(lines, start) {
    const head = lines[start], rule = lines[start + 1];
    if (!head?.includes("|") || !rule || !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(rule))
      return null;
    const header = cells(head);
    const align = cells(rule).map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : cell.startsWith(":") ? "left" : "");
    if (align.length !== header.length)
      return null;
    const rows = [];
    let end = start + 2;
    for (;end < lines.length && lines[end].includes("|") && lines[end].trim(); end++) {
      const row = cells(lines[end]);
      rows.push(header.map((_, index) => row[index] ?? ""));
    }
    return { header, align, rows, end };
  }
  function workspacePath(token, files) {
    const match = /^\.?\/?([\w.@\/-]+?)(?::(\d+)(?::\d+)?)?$/.exec(token.trim());
    if (!match || !files.has(match[1]))
      return null;
    return match[2] ? { path: match[1], line: Number(match[2]) } : { path: match[1] };
  }

  // dikdik/web/composer-size.ts
  function composerMaximum(viewport, controls) {
    return Math.max(52, Math.min(520, viewport - controls - 180));
  }

  class ComposerSize {
    manual = null;
    previous = "";
    reset() {
      this.manual = null;
    }
    resize(height, maximum) {
      this.manual = Math.max(52, Math.min(maximum, height));
    }
    fit(content, natural, maximum) {
      if (!content && this.previous)
        this.reset();
      this.previous = content;
      return Math.max(52, Math.min(maximum, this.manual ?? natural));
    }
  }

  // dikdik/web/app.ts
  function element(id, kind) {
    const found = document.getElementById(id);
    if (!(found instanceof kind))
      throw new Error(`Missing or invalid UI element: ${id}`);
    return found;
  }
  var el = (id) => document.getElementById(id);
  var input = (id) => element(id, HTMLInputElement);
  var button = (id) => element(id, HTMLButtonElement);
  var select = (id) => element(id, HTMLSelectElement);
  var textarea = (id) => element(id, HTMLTextAreaElement);
  var token = "";
  var selected = null;
  var polling = false;
  var watching = false;
  var connected = false;
  var review = reviewDiff(null);
  var reviewTask = null;
  var reviewKey = "";
  var selectedFile = null;
  var renderedDiff = "";
  var projectName = "";
  var canvasAutoOpened = null;
  var selectedState = "";
  var showArchived = false;
  var lastMessages = [];
  var agentProfiles = [];
  var currentPlan = null;
  var parentTask = null;
  var observedTaskMode = "";
  var viewEpoch = 0;
  var observeQueued = false;
  var submitting = false;
  var attached = [];
  var modelOverride = null;
  var modelProfiles = [];
  var skillNames = [];
  var slashItems = [];
  var slashIndex = 0;
  var slashModels = false;
  var taskRequest = null;
  var confirmCards = new Map;
  var confirmDecisions = new Map;
  var modelReady = true;
  var activeTag = "";
  var listLimit = 40;
  var folderOptions = [];
  var appSettings = null;
  var stepGroup = null;
  var showAllMessages = false;
  var projectFileSet = new Set;
  var expandedDirs = new Set;
  var openTabs = [];
  var fileText = "";
  var filePreview = false;
  var exposedSecrets = 0;
  var composerSize = new ComposerSize;
  function make(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className)
      node.className = className;
    if (text)
      node.textContent = text;
    return node;
  }
  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "i");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }
  function setClass(node, name, on) {
    const names = new Set((node.className || "").split(/\s+/).filter(Boolean));
    if (on)
      names.add(name);
    else
      names.delete(name);
    node.className = [...names].join(" ");
  }
  function storageGet(key) {
    try {
      return globalThis.localStorage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }
  function storageSet(key, value) {
    try {
      globalThis.localStorage?.setItem(key, value);
    } catch {}
  }
  function wide(query) {
    return typeof globalThis.matchMedia === "function" && globalThis.matchMedia(query).matches;
  }
  function clock(seconds) {
    return new Date(seconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  async function copyText(text, control) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = make("textarea");
      area.value = text;
      area.className = "sr-only";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    const label = control.querySelector("span");
    control.replaceChildren(icon("check"), make("span", "", "Copied"));
    setClass(control, "copied", true);
    setTimeout(() => {
      control.replaceChildren(icon("copy"), label ?? make("span", "", "Copy"));
      setClass(control, "copied", false);
    }, 1600);
  }
  function copyButton(getText, label = "Copy") {
    const control = make("button", "copy");
    control.type = "button";
    control.setAttribute("aria-label", "Copy to clipboard");
    control.append(icon("copy"), make("span", "", label));
    control.onclick = () => {
      copyText(getText(), control);
    };
    return control;
  }
  var LANGUAGES = { py: "python", ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx", json: "json", md: "markdown", rs: "rust", toml: "toml", yml: "yaml", yaml: "yaml", sh: "bash", css: "css", html: "html", go: "go", java: "java", c: "c", cpp: "cpp", rb: "ruby" };
  function languageFor(path) {
    if (typeof path !== "string")
      return "";
    const ext = path.split(".").pop()?.toLowerCase() ?? "";
    return LANGUAGES[ext] ?? "";
  }
  function looksLikeDiff(text) {
    return /^(diff --git |--- |@@ )/m.test(text) && /^[+-]/m.test(text);
  }
  function diffLines(text) {
    const lines = text.split(`
`);
    return lines.map((line, index) => {
      const kind = line.startsWith("@@") ? "hunk" : /^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(line) ? "meta" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
      return make("span", `dl dl-${kind}`, line + (index < lines.length - 1 ? `
` : ""));
    });
  }
  function codeBlock(text, lang = "", options = {}) {
    const block = make("div", options.wrap ? "code-block wrap" : "code-block");
    const head = make("div", "code-head");
    head.append(make("span", "code-lang", lang || "text"), copyButton(() => text));
    const pre = make("pre"), code = make("code");
    if (lang === "diff" || !lang && looksLikeDiff(text))
      code.append(...diffLines(text));
    else
      code.textContent = text;
    pre.append(code);
    block.append(head, pre);
    return block;
  }
  function inline(text) {
    const nodes = [];
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      const index = match.index ?? 0;
      if (index > last)
        nodes.push(document.createTextNode(text.slice(last, index)));
      const token2 = match[0];
      if (token2.startsWith("[")) {
        const parts = /^\[([^\]]+)\]\((.+)\)$/.exec(token2);
        try {
          const url = new URL(parts?.[2] ?? "https://invalid.invalid");
          if (!url.username && !url.password) {
            const link = make("a", "", parts?.[1] ?? "Link");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            nodes.push(link);
          } else
            nodes.push(document.createTextNode(token2));
        } catch {
          nodes.push(document.createTextNode(token2));
        }
      } else if (token2.startsWith("`")) {
        const target = workspacePath(token2.slice(1, -1), projectFileSet);
        if (target) {
          const link = make("button", "path-link");
          link.type = "button";
          link.title = `Open ${target.path} in Files`;
          link.append(make("code", "", token2.slice(1, -1)));
          link.onclick = () => {
            openFileFromChat(target.path, target.line);
          };
          nodes.push(link);
        } else
          nodes.push(make("code", "", token2.slice(1, -1)));
      } else
        nodes.push(make("strong", "", token2.slice(2, -2)));
      last = index + token2.length;
    }
    if (last < text.length)
      nodes.push(document.createTextNode(text.slice(last)));
    return nodes;
  }
  function richText(text) {
    const root = make("div", "prose");
    const lines = text.replace(/\r\n/g, `
`).split(`
`);
    let paragraph = [];
    let list = null;
    const flushParagraph = () => {
      if (!paragraph.length)
        return;
      const p = make("p");
      p.append(...inline(paragraph.join(`
`)));
      root.append(p);
      paragraph = [];
    };
    const flushList = () => {
      list = null;
    };
    for (let i = 0;i < lines.length; i++) {
      const line = lines[i] ?? "";
      const fence = /^\s*```\s*([\w.+-]*)\s*$/.exec(line);
      if (fence) {
        flushParagraph();
        flushList();
        const body = [];
        for (i++;i < lines.length && !/^\s*```\s*$/.test(lines[i] ?? ""); i++)
          body.push(lines[i] ?? "");
        root.append(codeBlock(body.join(`
`), (fence[1] ?? "").toLowerCase()));
        continue;
      }
      const table = parseTable(lines, i);
      if (table) {
        flushParagraph();
        flushList();
        root.append(markdownTable(table));
        i = table.end - 1;
        continue;
      }
      const alert = alertKind(line);
      if (alert || /^\s*>/.test(line)) {
        flushParagraph();
        flushList();
        const body = [];
        for (i += alert ? 1 : 0;i < lines.length && /^\s*>/.test(lines[i] ?? ""); i++)
          body.push((lines[i] ?? "").replace(/^\s*>\s?/, ""));
        i--;
        if (alert) {
          const box = make("div", `md-alert md-alert-${alert}`);
          box.append(make("p", "md-alert-title", alert[0].toUpperCase() + alert.slice(1)), richText(body.join(`
`)));
          root.append(box);
        } else {
          const quote = make("blockquote");
          quote.append(...inline(body.join(`
`)));
          root.append(quote);
        }
        continue;
      }
      const heading = /^#{1,4}\s+(.*)$/.exec(line);
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (heading) {
        flushParagraph();
        flushList();
        const h = make("h4");
        h.append(...inline(heading[1] ?? ""));
        root.append(h);
      } else if (bullet || numbered) {
        flushParagraph();
        const tag = bullet ? "UL" : "OL";
        if (!list || list.tagName !== tag) {
          list = make(bullet ? "ul" : "ol");
          root.append(list);
        }
        const li = make("li");
        li.append(...inline((bullet ?? numbered)?.[1] ?? ""));
        list.append(li);
      } else if (!line.trim()) {
        flushParagraph();
        flushList();
      } else {
        flushList();
        paragraph.push(line);
      }
    }
    flushParagraph();
    return root;
  }
  function markdownTable(table) {
    const scroller = make("div", "table-scroll");
    const grid = make("table"), head = make("thead"), body = make("tbody"), headRow = make("tr");
    table.header.forEach((cell, index) => {
      const th = make("th");
      th.append(...inline(cell));
      if (table.align[index])
        th.style.textAlign = table.align[index];
      headRow.append(th);
    });
    head.append(headRow);
    for (const row of table.rows) {
      const tr = make("tr");
      row.forEach((cell, index) => {
        const td = make("td");
        td.append(...inline(cell));
        if (table.align[index])
          td.style.textAlign = table.align[index];
        tr.append(td);
      });
      body.append(tr);
    }
    grid.append(head, body);
    scroller.append(grid);
    return scroller;
  }
  var TOOL_LABELS = {
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
    browse_url: ["globe", "Opened", "url"]
  };
  function detailField(details, key) {
    if (typeof details !== "object" || details === null || !(key in details))
      return "";
    const value = details[key];
    return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
  }
  function toolName(msg) {
    return (msg.title ?? "").replace(/^Tool (call|result): /, "");
  }
  function stepLabel(msg) {
    const details = msg.details;
    switch (msg.kind) {
      case "agent_tool_call": {
        const name = toolName(msg);
        if (name.startsWith("mcp__")) {
          const [, server = "", tool = ""] = name.split("__");
          return { icon: "plug", title: `${server} · ${tool}`, arg: "" };
        }
        const known = TOOL_LABELS[name];
        if (!known)
          return { icon: "terminal", title: name || "Tool", arg: "" };
        const arg = known[2] ? detailField(details, known[2]) || detailField(details, "query") || detailField(details, "pattern") : "";
        return { icon: known[0], title: known[1], arg };
      }
      case "agent_tool_result":
        return { icon: msg.state === "tool_failed" ? "alert" : "terminal", title: `${msg.state === "tool_failed" ? "Agent error" : "Output"} · ${toolName(msg)}`, arg: (msg.body ?? "").split(`
`)[0] ?? "" };
      case "inspected":
        return { icon: "folder", title: "Inspected repository", arg: detailField(details, "file_count") ? `${detailField(details, "file_count")} files` : "" };
      case "execution":
        return { icon: "terminal", title: "Running tests in Docker", arg: detailField(details, "command") };
      case "syntax":
        return { icon: detailField(details, "status") === "passed" ? "check" : "alert", title: msg.title ?? "Syntax check", arg: detailField(details, "status") };
      case "agent_repair":
        return { icon: "refresh", title: "Repair attempt", arg: detailField(details, "reason") };
      case "agent_started":
        return { icon: "sparkle", title: "Started agent", arg: "" };
      case "agent_finished":
        return { icon: "flag", title: "Agent finished", arg: detailField(details, "state") };
      case "proposal":
        return { icon: "pencil", title: "Proposed edits", arg: "" };
      case "applied":
        return { icon: "check", title: msg.title ?? "Applied", arg: (msg.body ?? "").split(`
`).join(", ") };
      case "skills":
        return { icon: "book", title: "Using skills", arg: (msg.body ?? "").split(`
`).join(", ") };
      case "mcp":
        return { icon: "plug", title: "Connected MCP servers", arg: `${(msg.body ?? "").split(`
`).join(", ")} · ${detailField(details, "tools") || 0} tools` };
      case "cancellation_requested":
        return { icon: "stop", title: "Cancellation requested", arg: "" };
      case "interrupt_requested":
        return { icon: "stop", title: "Stopping the current action", arg: "" };
      case "recovery_cleanup":
        return { icon: "alert", title: "Recovered after restart", arg: detailField(details, "warning") };
      case "hook_blocked":
        return { icon: "shield", title: "Blocked by a project hook", arg: msg.body ?? "" };
      case "agent_thinking":
        return { icon: "sparkle", title: "Thinking", arg: "" };
      case "model_routed":
        return { icon: "cpu", title: msg.title ?? "Routed", arg: msg.body ?? "" };
      case "critic":
        return { icon: msg.title === "Critic: looks done" ? "check" : "alert", title: msg.title ?? "Critic", arg: (msg.body ?? "").split(`
`)[0] ?? "" };
      case "sandbox_secrets":
        return { icon: "key", title: "Sandbox secrets available", arg: msg.body ?? "" };
      case "confirmation_resolved":
        return { icon: msg.title === "Approved" ? "check" : "x", title: `${msg.title} · ${detailField(details, "id") ? "action" : ""}`.replace(/ · $/, ""), arg: detailField(details, "reason") };
      case "subagent":
        return { icon: "sparkle", title: msg.title ?? "Subagent", arg: (msg.body ?? "").split(`
`)[0] ?? "" };
      case "condensed":
        return { icon: "list", title: msg.title ?? "Condensed", arg: "" };
      case "resumed":
        return { icon: "play", title: "Resumed", arg: detailField(details, "from_state") ? `from ${detailField(details, "from_state")}` : "" };
      case "pause_requested":
        return { icon: "pause", title: "Pausing after this step", arg: "" };
      case "forked":
        return { icon: "branch", title: "Forked from another conversation", arg: "" };
      case "goal_status":
        return { icon: "target", title: msg.title ?? "Goal", arg: (msg.body ?? "").split(`
`)[0] ?? "" };
      case "goal_turn":
        return { icon: "target", title: "Next goal turn", arg: (msg.body ?? "").split(`
`)[0] ?? "" };
      default:
        return { icon: "terminal", title: msg.title ?? "Step", arg: "" };
    }
  }
  function evidenceBlocks(msg, path = "") {
    const blocks = [];
    const details = msg.details;
    if (msg.kind === "agent_tool_call" && toolName(msg) === "edit_file" && detailField(details, "before")) {
      const minus = detailField(details, "before").split(`
`).map((line) => "-" + line);
      const plus = detailField(details, "after").split(`
`).map((line) => "+" + line);
      blocks.push(codeBlock([`--- ${path}`, `+++ ${path}`, ...minus, ...plus].join(`
`), "diff"));
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
    if (msg.body)
      blocks.push(codeBlock(msg.body, msg.kind === "agent_tool_result" ? languageFor(path) : "", { wrap: msg.kind !== "agent_tool_result" }));
    const shown = new Set(["path", "query", "text", "pattern"]);
    if (msg.kind === "agent_tool_call" && typeof details === "object" && details !== null && Object.keys(details).every((key) => shown.has(key)))
      return blocks;
    if (details !== undefined && details !== null && !(typeof details === "object" && Object.keys(details).length === 0)) {
      blocks.push(codeBlock(typeof details === "string" ? details : JSON.stringify(details, null, 2), typeof details === "string" ? "" : "json", { wrap: true }));
    }
    return blocks;
  }
  function stepRow(msg) {
    const li = make("li", `msg msg-${msg.role === "system" ? "system" : "tool"} msg-${msg.kind}`);
    li.id = msg.id;
    li.dataset.step = "1";
    const label = stepLabel(msg);
    const details = make("details", "step");
    const summary = make("summary");
    const badge = make("span", "step-icon");
    badge.append(icon(label.icon));
    summary.append(badge, make("span", "step-title", label.title));
    if (label.arg)
      summary.append(make("span", "step-arg", label.arg));
    const chevron = icon("chevron");
    chevron.setAttribute("class", "i step-chevron");
    summary.append(chevron);
    const body = make("div", "step-body");
    const blocks = evidenceBlocks(msg, label.arg);
    body.append(...blocks);
    details.append(summary, body);
    if (!blocks.length)
      setClass(details, "step-static", true);
    summary.addEventListener("click", (event) => {
      if (details.className.includes("step-static"))
        event.preventDefault();
    });
    li.append(details);
    return { li, body };
  }
  function resultCard(msg) {
    const li = make("li", `msg msg-system msg-result`);
    li.id = msg.id;
    const state = msg.state ?? "completed";
    const result = msg.details;
    if (state === "completed" && result?.turn_changed === false) {
      li.className = "msg msg-system msg-turn-note";
      const note = make("p", "turn-note", `No files changed in this turn.${usageText(msg.details) ? " " + usageText(msg.details) : ""}`);
      const seq2 = Number(msg.id.replace("terminal-", ""));
      if (selected && Number.isInteger(seq2)) {
        const branch = make("button", "ghost small turn-branch");
        branch.type = "button";
        branch.append(icon("branch"), make("span", "", "Branch"));
        branch.title = "Start a new conversation with the history up to this point";
        branch.onclick = () => {
          if (selected)
            forkTask(selected, isLatestTurn(msg) ? undefined : seq2).catch((error) => message(error instanceof Error ? error.message : "Fork failed", true));
        };
        note.append(branch);
      }
      li.append(note);
      return li;
    }
    const card = make("div", `result-card result-${state}`);
    const head = make("div", "result-head");
    const badge = make("span", "result-icon");
    badge.append(icon(state === "completed" ? "check" : state === "cancelled" ? "stop" : state === "paused" ? "pause" : "alert"));
    const titles = { completed: "Candidate ready for review", failed: "Task failed", blocked: "Task blocked", cancelled: "Task cancelled", interrupted: "Task interrupted", paused: "Stopped" };
    head.append(badge, make("span", "result-title", titles[state] ?? `Task ${state}`));
    if (state === "completed") {
      const verified = result?.tests_verified === true;
      head.append(make("span", verified ? "verify-badge verified" : "verify-badge", verified ? "Tests passed" : "Not test-verified"));
    }
    card.append(head, make("p", "result-text", resultStatus(state, result)));
    if (usageText(msg.details))
      card.append(make("p", "result-usage", usageText(msg.details)));
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
      resume.onclick = () => {
        exact ? resumeTask() : sendMessage("Continue where you left off. Finish the original request.");
      };
      actions.append(resume);
    }
    const seq = Number(msg.id.replace("terminal-", ""));
    if (selected && Number.isInteger(seq)) {
      const branch = make("button", "small ghost");
      branch.type = "button";
      branch.append(icon("branch"), make("span", "", "Branch from here"));
      branch.title = "Start a new conversation with the history and changes up to this point";
      branch.onclick = () => {
        if (selected)
          forkTask(selected, latest ? undefined : seq).catch((error) => message(error instanceof Error ? error.message : "Fork failed", true));
      };
      actions.append(branch);
    }
    const evidence = make("details", "step");
    const summary = make("summary");
    const chevron = icon("chevron");
    chevron.setAttribute("class", "i step-chevron");
    summary.append(make("span", "step-title", "Evidence"), chevron);
    evidence.append(summary, codeBlock(JSON.stringify(msg.details ?? {}, null, 2), "json", { wrap: true }));
    card.append(actions, evidence);
    li.append(card);
    return li;
  }
  function assistantMessage(msg, showHeader) {
    const li = make("li", `msg msg-assistant msg-${msg.kind}`);
    li.id = msg.id;
    if (showHeader) {
      const avatar = make("img", "avatar");
      avatar.src = "/icon.svg";
      avatar.alt = "";
      li.append(avatar);
    } else
      li.append(make("span", "avatar-gap"));
    const content = make("div");
    if (showHeader) {
      const label = make("div", "msg-label", "Dikdik");
      const time = make("time", "", clock(msg.time));
      time.dateTime = new Date(msg.time * 1000).toISOString();
      label.append(time);
      content.append(label);
    }
    if (msg.kind === "plan") {
      const steps = Array.isArray(msg.details) ? msg.details : null;
      if (steps && steps.every((step) => typeof step === "string")) {
        const prose = make("div", "prose");
        prose.append(make("p", "", "Here’s the plan:"));
        const list = make("ol", "plan");
        for (const step of steps)
          list.append(make("li", "", step));
        prose.append(list);
        content.append(prose);
      } else
        content.append(...evidenceBlocks(msg));
    } else if (msg.body) {
      content.append(richText(msg.body));
      const tools = make("div", "msg-actions");
      tools.append(copyButton(() => msg.body ?? "", "Copy"));
      if (msg.kind === "agent_message")
        tools.append(branchButton(msg, "Start a new conversation that includes everything up to and including this reply"));
      content.append(tools);
    }
    li.append(content);
    return li;
  }
  function messageSeq(msg) {
    const seq = Number(msg.id.slice(msg.id.lastIndexOf("-") + 1));
    return Number.isInteger(seq) && seq > 0 ? seq : null;
  }
  function branchButton(msg, title) {
    const control = make("button", "copy");
    control.type = "button";
    control.title = title;
    control.append(icon("branch"), make("span", "", "Branch"));
    control.onclick = () => {
      const seq = messageSeq(msg);
      if (!selected || seq === null)
        return;
      branchFromMessage(selected, seq).catch((error) => message(error instanceof Error ? error.message : "Branch failed", true));
    };
    return control;
  }
  function usageText(details) {
    const agent = typeof details === "object" && details !== null ? details.agent : undefined;
    if (!agent)
      return "";
    const count = (key) => typeof agent[key] === "number" ? agent[key] : 0;
    const parts = [`${count("model_calls")} model call${count("model_calls") === 1 ? "" : "s"}`, `${count("tool_calls")} tool call${count("tool_calls") === 1 ? "" : "s"}`];
    if (count("reported_usage_tokens"))
      parts.push(`${count("reported_usage_tokens").toLocaleString()} tokens reported`);
    const model = details.model;
    const name = typeof model?.profile === "string" && model.profile ? model.profile : typeof model?.model === "string" ? model.model : "";
    if (name)
      parts.push(name);
    return parts.join(" · ");
  }
  function isLatestTurn(msg) {
    const last = [...liveEvents].reverse().find((event) => isTerminal(event.kind));
    return !last || `terminal-${last.seq}` === msg.id;
  }
  function attachmentCard(msg) {
    const li = make("li", "msg msg-user msg-attachments");
    li.id = msg.id;
    const grid = make("div", "attachment-grid");
    for (const file of attachmentList(msg.details)) {
      if (file.type.startsWith("image/")) {
        const open = make("button", "attachment-open");
        open.type = "button";
        open.setAttribute("aria-label", `Open ${file.name}`);
        const image = make("img", "attachment-image");
        image.src = `/api/attachments/${file.id}`;
        image.alt = file.name;
        image.loading = "lazy";
        image.title = file.name;
        open.append(image);
        open.onclick = () => openLightbox(image.src, file.name);
        grid.append(open);
      } else {
        const chip = make("span", "attachment-chip");
        chip.append(icon("file"), make("span", "", file.name));
        grid.append(chip);
      }
    }
    li.append(grid);
    return li;
  }
  function confirmationCard(msg) {
    const d = msg.details ?? {};
    const li = make("li", "msg msg-system msg-confirm");
    li.id = msg.id;
    const card = make("div", `confirm-card risk-${d.risk || "low"}`);
    const head = make("div", "result-head");
    const badge = make("span", "result-icon");
    badge.append(icon("shield"));
    head.append(badge, make("span", "result-title", `Allow ${d.tool || "this action"}?`), make("span", "verify-badge", d.risk === "high" ? "High risk" : d.risk === "medium" ? "Needs review" : "Low risk"));
    card.append(head);
    if (d.reason)
      card.append(make("p", "result-text", d.reason));
    const args = typeof d.arguments === "object" && d.arguments !== null ? d.arguments : {};
    const shown = typeof args.command === "string" ? codeBlock(args.command, "bash", { wrap: true }) : codeBlock(JSON.stringify(args, null, 2), "json", { wrap: true });
    card.append(shown);
    const actions = make("div", "result-actions");
    const allow = make("button", "small primary", "Allow");
    allow.type = "button";
    allow.title = "Allow (Y, Enter or Ctrl+Enter)";
    const reject = make("button", "small", "Reject");
    reject.type = "button";
    reject.title = "Reject (N, Esc or Shift+Ctrl+Backspace)";
    const status = make("span", "confirm-status");
    actions.append(allow, reject, status);
    card.append(actions);
    li.append(card);
    const id = d.id ?? "";
    const decide = async (accept) => {
      if (!selected || allow.disabled)
        return;
      allow.disabled = reject.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(selected)}/confirm`, { id, accept });
        status.textContent = accept ? "Allowed" : "Rejected";
      } catch (error) {
        allow.disabled = reject.disabled = false;
        message(error instanceof Error ? error.message : "Could not send the decision", true);
      }
    };
    allow.onclick = () => {
      decide(true);
    };
    reject.onclick = () => {
      decide(false);
    };
    card.addEventListener("keydown", (event) => {
      if (event.ctrlKey || event.metaKey)
        return;
      const key = event.key.toLowerCase();
      if (key === "y" || key === "enter") {
        event.preventDefault();
        decide(true);
      } else if (key === "n" || key === "escape") {
        event.preventDefault();
        decide(false);
      }
    });
    card.tabIndex = -1;
    if (openConfirmations(liveEvents).includes(id)) {
      confirmCards.set(id, li);
      confirmDecisions.set(id, (accept) => {
        decide(accept);
      });
      if (!textarea("objective").value.trim())
        setTimeout(() => allow.focus?.(), 0);
    } else {
      allow.disabled = reject.disabled = true;
      status.textContent = "Decided";
    }
    return li;
  }
  var rendered = new Set;
  var pendingCalls = [];
  var lastRole = "";
  function renderMessage(msg) {
    if (msg.kind === "attachments") {
      lastRole = "user";
      return attachmentCard(msg);
    }
    if (msg.kind === "confirmation_requested") {
      lastRole = "system";
      return confirmationCard(msg);
    }
    if (msg.kind === "confirmation_resolved") {
      const id = detailField(msg.details, "id"), card = confirmCards.get(id);
      if (card) {
        for (const control of card.querySelectorAll("button"))
          control.disabled = true;
        const status = card.querySelector(".confirm-status");
        if (status)
          status.textContent = msg.title === "Approved" ? "Allowed" : "Rejected";
        confirmCards.delete(id);
        confirmDecisions.delete(id);
      }
    }
    if (msg.kind === "plan_updated") {
      const li2 = make("li", "msg msg-plan-preview");
      li2.id = msg.id;
      li2.append(make("h4", "", "PLAN.md"), richText((msg.body ?? "").slice(0, 800)));
      const actions = make("div", "row");
      for (const label of ["Read more", "View", "Build"]) {
        const control = make("button", "small", label);
        control.type = "button";
        control.onclick = () => {
          if (label === "Build") {
            const plan = msg.details;
            buildReviewedPlan(plan?.revision, plan?.planner_id);
          } else
            openCanvas("planner");
        };
        actions.append(control);
      }
      li2.append(actions);
      return li2;
    }
    if (msg.kind === "browser_result") {
      const view = browserView([{ seq: 1, task_id: selected ?? "", time: msg.time, kind: msg.kind, data: msg.details ?? {} }]);
      const li2 = make("li", "msg msg-browser");
      li2.id = msg.id;
      li2.append(make("p", "msg-label", view.screenshot ? view.title || "Browser capture" : `Browser: ${view.status || "unavailable"}`));
      if (view.screenshot) {
        const image = make("img", "browser-image");
        image.src = view.screenshot;
        image.alt = `Screenshot of ${view.url}`;
        image.loading = "lazy";
        li2.append(image, make("p", "browser-caption", view.url));
        const open = make("button", "small", "Open Browser tab");
        open.type = "button";
        open.onclick = () => openCanvas("browser");
        li2.append(open);
      } else
        li2.append(make("p", "browser-status", view.error || "No screenshot was captured."));
      return li2;
    }
    if (msg.role === "user") {
      const li2 = make("li", `msg msg-user msg-${msg.kind}`);
      li2.id = msg.id;
      const bubble = make("div", "bubble", msg.body ?? "");
      const tools = make("div", "msg-actions user-actions");
      tools.append(copyButton(() => msg.body ?? "", "Copy"));
      if (msg.kind === "user_message")
        tools.append(branchButton(msg, "Start a new conversation from just before this message, so you can write it differently"));
      li2.append(bubble, tools);
      lastRole = "user";
      return li2;
    }
    if (msg.role === "system" && msg.state) {
      lastRole = "system";
      return resultCard(msg);
    }
    if (msg.role === "assistant" && (msg.body || msg.kind === "plan")) {
      const li2 = assistantMessage(msg, lastRole !== "assistant");
      lastRole = "assistant";
      return li2;
    }
    if (msg.kind === "agent_tool_result") {
      const name = toolName(msg);
      const index = pendingCalls.findIndex((call2) => call2.name === name);
      const call = index >= 0 ? pendingCalls.splice(index, 1)[0] : undefined;
      if (call && msg.body) {
        const label = call.body.parentElement?.querySelector(".step-arg")?.textContent ?? "";
        call.body.append(codeBlock(msg.body, name === "read_file" ? languageFor(label) : ""));
        if (call.body.parentElement) {
          setClass(call.body.parentElement, "step-static", false);
          if (msg.state === "tool_failed")
            setClass(call.body.parentElement, "step-failed", true);
        }
        return null;
      }
    }
    const { li, body } = stepRow(msg);
    if (msg.kind === "agent_tool_call")
      pendingCalls.push({ name: toolName(msg), body });
    lastRole = "tool";
    return li;
  }
  function nearBottom(thread) {
    return !(thread.scrollHeight > 0) || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
  }
  var EARLIER_LIMIT = 150;
  function renderMessageStream(messages) {
    const stream = el("message-stream"), thread = el("thread");
    const stick = nearBottom(thread);
    if (!rendered.size && !showAllMessages && messages.length > EARLIER_LIMIT) {
      const hidden = messages.slice(0, messages.length - EARLIER_LIMIT);
      for (const msg of hidden)
        rendered.add(msg.id);
      const li = make("li", "msg show-earlier");
      const more = make("button", "ghost small", `Show ${hidden.length} earlier messages`);
      more.type = "button";
      more.onclick = () => {
        showAllMessages = true;
        rerenderConversation();
      };
      li.append(more);
      stream.append(li);
    }
    for (const msg of messages) {
      if (rendered.has(msg.id))
        continue;
      rendered.add(msg.id);
      const node = renderMessage(msg);
      if (node)
        addToStream(stream, node);
    }
    if (stick)
      thread.scrollTop = thread.scrollHeight;
  }
  function addToStream(stream, node) {
    if (node.dataset.step !== "1") {
      closeStepGroup();
      stream.append(node);
      return;
    }
    if (!stepGroup || stream.lastElementChild !== stepGroup.li) {
      closeStepGroup();
      const li = make("li", "msg step-group few"), details = make("details"), summary = make("summary"), list = make("ol", "step-list");
      details.open = true;
      summary.addEventListener("click", (event) => {
        if (li.className.includes("few"))
          event.preventDefault();
      });
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
  function closeStepGroup() {
    if (stepGroup && stepGroup.count >= 3)
      stepGroup.details.open = false;
    stepGroup = null;
  }
  function rerenderConversation() {
    rendered = new Set;
    pendingCalls = [];
    lastRole = "";
    stepGroup = null;
    confirmCards.clear();
    confirmDecisions.clear();
    el("message-stream").replaceChildren();
    renderMessageStream(eventsToChat(liveEvents, liveObjective));
  }
  function showReviewSelection() {
    const file = review.files.find((item) => item.path === selectedFile);
    const label = file ? `${fileStatusLabel(file.status)} · ${file.path}` : review.files.length ? "All changes" : "Combined diff";
    const sel = el("selected-file");
    sel.textContent = label;
    const content = file ? file.diff : review.diff;
    const diff = el("diff");
    if (renderedDiff !== content || diff.textContent !== content) {
      const code = diffLines(content);
      diff.replaceChildren(...content ? code : []);
      renderedDiff = content;
    }
    diff.hidden = !content;
    sel.hidden = !content;
    el("copy-diff").hidden = !content;
    for (const control of el("review-controls").querySelectorAll("button")) {
      const current = control.dataset.path === selectedFile || selectedFile === null && control.dataset.all === "true";
      control.setAttribute("aria-pressed", String(current));
    }
  }
  function renderReview(taskId, state, result) {
    review = reviewDiff(result);
    const key = JSON.stringify([taskId, review.files.map((file) => [file.path, file.status])]);
    if (key !== reviewKey) {
      if (reviewTask !== taskId || !review.files.some((file) => file.path === selectedFile))
        selectedFile = null;
      reviewTask = taskId;
      reviewKey = key;
      const controls = [];
      const addControl = (label, path, status) => {
        const control = document.createElement("button");
        control.type = "button";
        control.className = "secondary";
        control.textContent = label;
        control.setAttribute("aria-controls", "diff");
        control.title = label;
        if (path === null)
          control.dataset.all = "true";
        else
          control.dataset.path = path;
        if (status)
          control.dataset.status = status;
        control.onclick = () => {
          selectedFile = path;
          showReviewSelection();
        };
        controls.push(control);
      };
      if (review.files.length) {
        addControl("All changes", null);
        for (const file of review.files)
          addControl(`${fileStatusLabel(file.status)} · ${file.path}`, file.path, file.status);
      }
      el("review-controls").replaceChildren(...controls);
    }
    el("review-controls").hidden = !review.files.length;
    const note = el("review-note");
    note.textContent = !review.diff ? result ? "No candidate diff in this result." : isTerminal(state) ? "No candidate diff for this stopped task." : "No candidate diff yet." : review.metadata === "invalid" ? "Per-file details could not be validated. Showing the combined diff." : review.metadata === "legacy" ? "Per-file details unavailable. Showing the combined diff." : "";
    note.hidden = !note.textContent;
    showReviewSelection();
    const count = review.files.length || (review.diff ? 1 : 0);
    el("changes-count").textContent = count ? String(count) : "";
    el("canvas-count").textContent = review.files.length ? `${review.files.length} file${review.files.length === 1 ? "" : "s"}` : "";
    el("toggle-canvas").hidden = !review.diff;
    if (review.diff && canvasAutoOpened !== taskId && isTerminal(state)) {
      canvasAutoOpened = taskId;
      if (wide("(min-width: 1101px)"))
        openCanvas("changes");
    }
  }
  function setCanvas(open) {
    el("canvas").hidden = !open;
    el("toggle-canvas").setAttribute("aria-pressed", String(open && canvasTab === "changes"));
  }
  var canvasTab = "changes";
  var projectFiles = [];
  var openFile = null;
  var fileVersion = "workspace";
  var applyStep = "idle";
  var lastApplied = [];
  function openCanvas(tab) {
    canvasTab = tab;
    const canvas = el("canvas");
    if (typeof canvas.querySelectorAll === "function") {
      for (const node of canvas.querySelectorAll(".canvas-tab"))
        node.setAttribute("aria-selected", String(node.dataset.tab === tab));
      for (const pane of canvas.querySelectorAll(".canvas-body"))
        pane.hidden = pane.dataset.pane !== tab;
    }
    setCanvas(true);
    if (tab === "files")
      renderFileList();
    if (tab === "git")
      loadGit();
    if (tab === "terminal")
      renderTerminal();
    if (tab === "browser")
      renderBrowser();
    if (tab === "planner") {
      renderPlanner();
      if (selected)
        loadPlan(selected);
    }
    if (tab === "tasklist")
      renderChecklist();
    if (tab === "usage")
      renderUsage();
  }
  function changedPaths() {
    return new Set(review.files.map((file) => file.path));
  }
  function renderTerminal() {
    const view = terminalTranscript(liveEvents);
    const terminal = el("terminal-view");
    const empty = el("terminal-empty");
    terminal.textContent = view.text;
    terminal.hidden = !view.commands.length;
    empty.hidden = !!view.commands.length;
    empty.textContent = selectedState && (!isTerminal(selectedState) || selectedState === "failed") ? "Waiting for runtime to start..." : "No terminal output yet. Commands run by the agent will appear here.";
    if (view.commands.some((command) => command.running))
      terminal.scrollTop = terminal.scrollHeight;
  }
  function usageValue(value) {
    return value === undefined ? "Not reported" : value.toLocaleString();
  }
  function renderUsage() {
    const panel = el("usage-panel"), metrics = usageMetrics(liveEvents);
    if (!metrics.turns.length) {
      const empty = make("div", "canvas-empty usage-empty");
      const copy = make("div");
      copy.append(make("h3", "", "No metrics data available"), make("p", "muted", "Usage appears only when the configured provider reports it. Dikdik never estimates token cost."));
      empty.append(copy);
      panel.replaceChildren(empty);
      return;
    }
    const summary = make("section", "usage-card");
    summary.append(make("h3", "", "Task token usage"));
    const grid = make("dl", "usage-grid");
    for (const [label, value] of [
      ["Input", metrics.task.inputTokens],
      ["Cache hit", metrics.task.cacheReadTokens],
      ["Cache write", metrics.task.cacheWriteTokens],
      ["Output", metrics.task.outputTokens],
      ["Total", metrics.task.totalTokens]
    ]) {
      grid.append(make("dt", "", label), make("dd", "", usageValue(value)));
    }
    summary.append(grid);
    summary.append(make("p", "usage-cost", metrics.task.reportedCost === undefined ? "Provider cost not reported — no estimate shown." : `Provider-reported cost: ${metrics.task.reportedCost.toLocaleString(undefined, { maximumFractionDigits: 8 })}`));
    const context = make("section", "usage-card");
    context.append(make("h3", "", "Context"));
    if (metrics.context) {
      const percent = Math.round(metrics.context.ratio * 100);
      context.append(make("p", "usage-context", `${percent}% used (${100 - percent}% left)`), make("p", "muted", `${Math.round(metrics.context.bytes / 1024).toLocaleString()} of ${Math.round(metrics.context.limit / 1024).toLocaleString()} KiB in Dikdik’s bounded context.`));
    } else
      context.append(make("p", "muted", "No current context measurement."));
    const turns = make("section", "usage-turns");
    turns.append(make("h3", "", "By turn"));
    for (const turn of metrics.turns) {
      const card = make("article", "usage-turn");
      card.append(make("h4", "", `Turn ${turn.turn}${turn.profile ? ` · ${turn.profile}` : ""}`), make("p", "muted", `${turn.calls} provider report${turn.calls === 1 ? "" : "s"}`));
      const values = make("dl", "usage-grid compact");
      for (const [label, value] of [["Input", turn.inputTokens], ["Cache hit", turn.cacheReadTokens], ["Cache write", turn.cacheWriteTokens], ["Output", turn.outputTokens], ["Total", turn.totalTokens]]) {
        values.append(make("dt", "", label), make("dd", "", usageValue(value)));
      }
      card.append(values);
      if (turn.reportedCost !== undefined)
        card.append(make("p", "usage-cost", `Provider-reported cost: ${turn.reportedCost.toLocaleString(undefined, { maximumFractionDigits: 8 })}`));
      turns.append(card);
    }
    const note = make("p", "usage-note", "Only provider-reported values are shown. Cache fields remain ‘Not reported’ when the provider omits them; costs are never inferred from token counts.");
    panel.replaceChildren(context, summary, turns, note);
  }
  function renderBrowser() {
    const view = browserView(liveEvents);
    el("browser-current-url").textContent = view.url || "No URL loaded";
    const link = el("browser-external");
    link.hidden = !view.url;
    link.href = view.url || "#";
    button("browser-external-empty").hidden = !!view.url;
    el("browser-empty").hidden = !!view.screenshot;
    const mount = el("browser-snapshot");
    mount.hidden = !view.screenshot;
    if (mount.dataset.screenshot !== view.screenshot) {
      mount.dataset.screenshot = view.screenshot;
      mount.replaceChildren();
      if (view.screenshot) {
        const image = make("img", "browser-image");
        image.src = view.screenshot;
        image.alt = view.title || `Screenshot of ${view.url}`;
        image.addEventListener("error", () => {
          el("browser-status").textContent = "This screenshot is no longer available. Open the URL again to capture it.";
        });
        mount.append(image);
      }
    }
    el("browser-status").textContent = view.status === "running" ? "Opening page…" : view.error || (view.status === "passed" ? "Page captured. Web content is untrusted." : "");
    button("browser-open").disabled = !selected || view.status === "running";
    button("browser-stop").disabled = view.status !== "running";
  }
  async function loadPlan(task) {
    const epoch = viewEpoch;
    const plan = parsePlan(await api(`/api/tasks/${encodeURIComponent(task)}/planner`));
    if (task !== selected || epoch !== viewEpoch)
      return;
    currentPlan = plan;
    renderPlanner();
  }
  function renderPlanner() {
    const content = currentPlan?.content ?? "";
    el("planner-empty").hidden = !!content;
    const mount = el("planner-content");
    mount.hidden = !content;
    if (mount.dataset.content !== content) {
      mount.dataset.content = content;
      mount.replaceChildren(...content ? [richText(content)] : []);
    }
    button("planner-create").disabled = !selected || !!currentPlan?.planner_id || !isTerminal(selectedState);
    button("planner-build").disabled = !content || !isTerminal(selectedState) || currentPlan?.built_revision === currentPlan?.revision;
    el("planner-note").textContent = content ? `PLAN.md · revision ${currentPlan?.revision}. Review before Build. Files change only through Apply.` : "Plans are reviewed before building. Workspace files change only through Apply.";
  }
  function renderChecklist() {
    const view = taskChecklist(liveEvents);
    el("tasklist-tab").hidden = !view.used;
    const list = el("task-checklist");
    list.replaceChildren();
    const labels = { todo: "To do", in_progress: "In progress", done: "Done" };
    const symbols = { todo: "○", in_progress: "●", done: "✓" };
    for (const task of view.tasks) {
      const row = make("li", "checklist-step");
      row.dataset.active = String(task.status === "in_progress");
      const marker = make("span", "checklist-icon", symbols[task.status]);
      marker.setAttribute("aria-hidden", "true");
      row.append(marker, make("span", "checklist-status", labels[task.status]), make("span", "", task.title));
      list.append(row);
    }
    if (view.used && !view.tasks.length)
      list.append(make("li", "", "No checklist steps."));
  }
  async function enterPlanning() {
    if (!selected) {
      select("mode").value = "plan";
      syncMode();
      syncComposer();
      textarea("objective").focus();
      return;
    }
    if (!isTerminal(selectedState))
      throw new Error("Wait for the current turn before planning.");
    const plan = parsePlan(await api(`/api/tasks/${selected}/planner`, {}));
    if (!plan?.planner_id)
      throw new Error("Planner initialization failed.");
    select("mode").value = "plan";
    syncMode();
    selectTask(plan.planner_id);
    await loadHistory();
    await observe();
    message("Planning agent initialized");
  }
  async function enterCode() {
    const target = parentTask || currentPlan?.task_id;
    select("mode").value = "agent";
    syncMode();
    if (target && target !== selected) {
      selectTask(target);
      await loadHistory();
      await observe();
    }
    syncComposer();
  }
  async function buildReviewedPlan(expectedRevision, expectedPlanner) {
    if (!selected || !currentPlan?.content)
      return message("There is no plan to build.", true);
    if (expectedRevision !== undefined && expectedRevision !== currentPlan.revision)
      return message("The plan changed. Review its latest revision in the Planner tab.", true);
    if (expectedPlanner !== undefined && expectedPlanner !== currentPlan.planner_id)
      return message("The planner changed. Review the current plan in the Planner tab.", true);
    try {
      const task = selected, revision = currentPlan.revision, planner_id = currentPlan.planner_id;
      select("mode").value = "agent";
      syncMode();
      const request = composerRequest("Execute the plan based on the PLAN.md file.");
      const result = await api(`/api/tasks/${task}/planner/build`, { ...request, confirm: true, revision, planner_id });
      selectTask(result.id);
      await loadHistory();
      await observe();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not build this plan", true);
    }
  }
  el("planner-create").addEventListener("click", () => {
    enterPlanning().catch((error) => message(String(error), true));
  });
  el("planner-build").addEventListener("click", () => {
    buildReviewedPlan();
  });
  el("browser-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected)
      return message("Choose a conversation first.", true);
    if (!input("browser-consent").checked)
      return message("Approve Docker and public web access for this capture.", true);
    const task = selected;
    button("browser-open").disabled = true;
    button("browser-stop").disabled = false;
    el("browser-status").textContent = "Opening page…";
    try {
      await api(`/api/tasks/${task}/browser`, { url: input("browser-url").value, approve_browser: true, approve_execution: true });
      if (selected === task) {
        watching = true;
        await observe();
      }
    } catch (error) {
      message(error instanceof Error ? error.message : "Browser capture failed", true);
    } finally {
      input("browser-consent").checked = false;
      if (selected === task) {
        button("browser-open").disabled = false;
        button("browser-stop").disabled = true;
      }
    }
  });
  el("browser-stop").addEventListener("click", () => {
    if (selected)
      api(`/api/tasks/${selected}/browser/stop`, {}).catch((error) => message(String(error), true));
  });
  async function loadBrowserSettings() {
    const info = await api("/api/browser");
    input("browser-enabled").checked = info.enabled;
    el("browser-availability").textContent = info.availability === "image_ready" ? info.launch_verified ? "Browser image installed. A capture completed in this service session." : "Browser image installed. A successful launch has not been verified." : `Browser runtime unavailable. Build the local ${info.image} image and start Docker.`;
  }
  el("browser-enabled").addEventListener("change", async () => {
    try {
      await request("/api/browser", "PATCH", { enabled: input("browser-enabled").checked });
      message("Browser setting saved.");
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not save browser setting", true);
      await loadBrowserSettings();
    }
  });
  function renderFileList() {
    const query = (el("file-search").value ?? "").trim().toLowerCase();
    const changed = changedPaths();
    const all = [...new Set([...review.files.filter((f) => f.status === "added").map((f) => f.path), ...projectFiles])].sort();
    const matches = all.filter((path) => !query || path.toLowerCase().includes(query));
    if (!query) {
      el("file-list").replaceChildren(...treeRows(all, changed));
      if (!all.length)
        el("file-list").replaceChildren(make("p", "more", "No files"));
      return;
    }
    const nodes = matches.slice(0, 400).map((path) => {
      const item = make("button", "", path);
      item.type = "button";
      item.title = path;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(path === openFile));
      if (changed.has(path))
        item.dataset.changed = "true";
      item.onclick = () => {
        showFile(path, changed.has(path) && selected ? "candidate" : "workspace");
      };
      return item;
    });
    if (matches.length > 400)
      nodes.push(make("p", "more", `${matches.length - 400} more — refine the search`));
    if (!matches.length)
      nodes.push(make("p", "more", query ? "No matching files" : "No files"));
    el("file-list").replaceChildren(...nodes);
  }
  async function showFile(path, version) {
    try {
      const query = `/api/files/content?path=${encodeURIComponent(path)}${version === "candidate" && selected ? `&task=${encodeURIComponent(selected)}` : ""}`;
      const data = await api(query);
      openFile = path;
      fileVersion = data.version;
      fileText = data.text;
      openTabs = [...openTabs.filter((tab) => tab !== path), path].slice(-8);
      for (const folder of parentFolders(path))
        expandedDirs.add(folder);
      el("file-name").textContent = path;
      const markdown = /\.(md|markdown)$/i.test(path);
      el("file-preview").hidden = !markdown;
      if (!markdown)
        filePreview = false;
      showFilePreview();
      renderFileTabs();
      const lines = data.text.split(`
`);
      if (lines.length > 1 && lines[lines.length - 1] === "")
        lines.pop();
      el("file-view").replaceChildren(...lines.map((line, index) => {
        const span = make("span", "ln", line + `
`);
        span.dataset.n = String(index + 1);
        return span;
      }));
      el("copy-file").hidden = false;
      const changed = changedPaths().has(path) && !!selected;
      el("file-version").hidden = !changed;
      for (const node of el("file-version").querySelectorAll("button"))
        node.setAttribute("aria-pressed", String(node.dataset.version === data.version));
      renderFileList();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not open the file", true);
    }
  }
  async function loadGit() {
    const panel = el("git-panel");
    try {
      const info = await api("/api/git");
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
        box.type = "checkbox";
        box.value = entry.path;
        box.checked = lastApplied.includes(entry.path);
        row.append(box, make("span", "", entry.path), make("span", `tag tag-${entry.status}`, entry.status));
        list.append(row);
      }
      statusBox.append(list);
      const form = make("form", "git-commit");
      const field = make("textarea");
      field.placeholder = "Commit message";
      field.setAttribute("aria-label", "Commit message");
      field.value = lastApplied.length && selected ? conversationLabel(lastTasks.find((t) => t.id === selected) ?? { id: selected, state: "" }) : "";
      const commit = make("button", "primary small");
      commit.type = "submit";
      commit.append(icon("check"), make("span", "", "Commit selected"));
      form.append(field, commit);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const files = [...list.querySelectorAll("input:checked")].map((box) => box.value);
        try {
          const made = await request("/api/git/commit", "POST", { message: field.value, files });
          lastApplied = [];
          message(`Committed ${made.short}.`);
          await loadGit();
        } catch (error) {
          message(error instanceof Error ? error.message : "Commit failed", true);
        }
      });
      const logBox = make("section");
      logBox.append(make("h3", "", "Recent commits"));
      const log = make("ol", "git-log");
      for (const entry of info.commits ?? []) {
        const row = make("li");
        row.append(make("code", "", entry.short), make("span", "", entry.subject), make("small", "", `${entry.author} · ${new Date(entry.time * 1000).toLocaleString()}`));
        log.append(row);
      }
      if (!info.commits?.length)
        log.append(make("li", "muted", "No commits yet"));
      logBox.append(log);
      panel.replaceChildren(head, ...info.status?.length ? [statusBox, form] : [statusBox], logBox);
    } catch (error) {
      panel.replaceChildren(make("p", "error", error instanceof Error ? error.message : "Git status unavailable"));
    }
  }
  function syncApplyBar() {
    const bar = el("apply-bar");
    bar.hidden = !(selected && review.diff && isTerminal(selectedState));
    setClass(bar, "confirming", applyStep === "confirm");
    setClass(bar, "done", applyStep === "done");
    const count = review.files.length || 1;
    el("apply-note").textContent = applyStep === "confirm" ? `Write ${count} file${count === 1 ? "" : "s"} into ${projectName || "your workspace"}? Files you edited since are never overwritten.` : applyStep === "done" ? `Applied to ${projectName || "your workspace"}.` : "Changes live in an isolated copy until you apply them.";
    el("apply-label").textContent = applyStep === "confirm" ? "Confirm apply" : applyStep === "busy" ? "Applying…" : "Apply to workspace";
    el("apply").disabled = applyStep === "busy";
    el("apply").hidden = applyStep === "done";
    el("apply-cancel").hidden = applyStep !== "confirm";
    el("apply-commit").hidden = applyStep !== "done";
  }
  async function applyChanges() {
    if (!selected)
      return;
    if (applyStep !== "confirm") {
      applyStep = "confirm";
      syncApplyBar();
      return;
    }
    applyStep = "busy";
    syncApplyBar();
    try {
      const done = await request(`/api/tasks/${encodeURIComponent(selected)}/apply`, "POST", { confirm: true });
      lastApplied = done.applied;
      storageSet("dikdik.onboarding.applied", "1");
      applyStep = "done";
      message(done.applied.length ? `Applied ${done.applied.length} file${done.applied.length === 1 ? "" : "s"}.` : "Already applied; nothing to write.");
      watching = true;
      await Promise.all([project(), observe()]);
      loadGit();
    } catch (error) {
      applyStep = "idle";
      message(error instanceof Error ? error.message : "Apply failed", true);
    }
    syncApplyBar();
  }
  function resetConversation() {
    rendered = new Set;
    pendingCalls = [];
    lastRole = "";
    renderedDiff = "";
    applyStep = "idle";
    lastApplied = [];
    confirmCards.clear();
    confirmDecisions.clear();
    taskRequest = null;
    stepGroup = null;
    showAllMessages = false;
    el("goal-banner").hidden = true;
    el("context-meter").hidden = true;
    el("pause").hidden = true;
    el("side-answer").hidden = true;
    el("message-stream").replaceChildren();
    el("review-controls").replaceChildren();
    el("diff").replaceChildren();
    el("review-note").textContent = "";
    liveEvents = [];
    currentPlan = null;
    parentTask = null;
    observedTaskMode = "";
    input("browser-consent").checked = false;
    input("browser-approval").checked = false;
    input("browser-url").value = "";
    renderTerminal();
    renderBrowser();
    renderPlanner();
    renderChecklist();
    el("working").hidden = true;
    el("toggle-canvas").hidden = true;
    setCanvas(false);
    button("cancel").disabled = true;
  }
  function setUrl(task) {
    const loc = globalThis.location;
    if (!loc || typeof globalThis.history?.replaceState !== "function")
      return;
    globalThis.history.replaceState(null, "", task ? `${loc.pathname}?task=${encodeURIComponent(task)}` : loc.pathname);
  }
  function syncComposer() {
    textarea("objective").placeholder = select("mode").value === "plan" ? "Let’s work on a plan" : selected ? "Reply to Dikdik…" : "Ask Dikdik to build, fix or change something…";
    el("task-actions").hidden = !selected;
    syncSend();
  }
  function syncSend() {
    button("start").disabled = submitting || !textarea("objective").value.trim() && !attached.length || !el("model-banner").hidden;
    textarea("objective").required = !attached.length;
  }
  function selectTask(id) {
    composerSize.reset();
    stopStream();
    viewEpoch++;
    selected = id;
    watching = true;
    selectedState = "";
    resetConversation();
    el("chat-title").textContent = "Loading…";
    setState("");
    markCurrent();
    closeSidebarDrawer();
    setUrl(id);
    syncComposer();
    composerDrafts.select(id);
  }
  function newTask() {
    composerSize.reset();
    stopStream();
    viewEpoch++;
    selected = null;
    watching = false;
    reviewTask = null;
    reviewKey = "";
    selectedState = "";
    resetConversation();
    el("chat-title").textContent = "New task";
    setTitle("");
    setState("");
    markCurrent();
    closeSidebarDrawer();
    setUrl(null);
    syncComposer();
    composerDrafts.select("home");
    textarea("objective").focus?.();
  }
  function setTitle(title) {
    if (globalThis.document && "title" in document)
      document.title = title ? `${title} · Dikdik` : "Dikdik";
  }
  function setState(state) {
    const pill = el("task-state");
    pill.textContent = state;
    pill.className = state ? `state-pill state-${state}` : "state-pill";
  }
  var messageTimer;
  function message(value, error = false) {
    const m = el("message");
    if (messageTimer !== undefined)
      clearTimeout(messageTimer);
    m.textContent = value;
    m.className = error ? "error" : "";
    m.hidden = false;
    messageTimer = setTimeout(() => {
      m.hidden = true;
    }, error ? 7000 : 3500);
  }

  class RequestError extends Error {
    status;
    data;
    constructor(message2, status, data) {
      super(message2);
      this.status = status;
      this.data = data;
    }
  }
  async function request(path, method, body) {
    const headers = { "Content-Type": "application/json" };
    if (token)
      headers.Authorization = "Bearer " + token;
    const init = { method, headers, credentials: "same-origin" };
    if (body !== undefined)
      init.body = JSON.stringify(body);
    const response = await fetch(path, init);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof data === "object" && data !== null && "error" in data && typeof data.error === "string" ? data.error : "Request failed";
      throw new RequestError(detail, response.status, data);
    }
    return data;
  }
  var api = (path, body) => request(path, body === undefined ? "GET" : "POST", body);
  var apiM4 = (path, method, body) => request(path, method, body);
  var composerDrafts = new ComposerDrafts(request, (content, status) => {
    if (content !== null)
      textarea("objective").value = content;
    const labels = { loading: "Loading draft…", saving: "Saving draft…", saved: "Draft saved", unsaved: "Draft not saved. Keep this tab open.", conflict: "This draft changed in another tab. Choose which version to keep." };
    el("draft-status").textContent = labels[status.state];
    el("draft-status-row").hidden = status.state === "saved" && !textarea("objective").value;
    el("draft-use-saved").hidden = status.state !== "conflict";
    el("draft-keep-local").hidden = status.state !== "conflict";
    el("draft-retry").hidden = status.state !== "unsaved";
    el("draft-conflict").hidden = status.state !== "conflict";
    el("draft-remote").textContent = status.remote ?? "";
    autosize();
  });
  el("draft-use-saved").addEventListener("click", () => {
    composerDrafts.resolve("server");
  });
  el("draft-keep-local").addEventListener("click", () => {
    composerDrafts.resolve("local");
  });
  el("draft-retry").addEventListener("click", () => {
    composerDrafts.resolve("local");
  });
  globalThis.addEventListener?.("beforeunload", (event) => {
    if (composerDrafts.hasUnsaved()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  async function loadModel() {
    const info = await api("/api/model");
    const pill = el("model-pill");
    setClass(pill, "unset", !info.configured);
    modelReady = info.configured;
    el("model-label").textContent = modelOverride ? modelOverride.name : info.configured ? info.model ?? "Model" : "Set up a model";
    setClass(pill, "override", modelOverride !== null);
    pill.title = info.configured ? info.source === "profile" ? `${info.model} · ${info.provider_name} (profile “${info.profile}”). Change it in Settings → Models.` : `${info.model} from server environment variables` : info.problem ?? "No model is set up";
    syncModelBanner();
    renderOnboarding();
    syncSend();
  }
  async function project() {
    const data = await api("/api/project");
    projectName = data.name;
    projectFiles = data.files.map((file) => file.path);
    projectFileSet = new Set(projectFiles);
    const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
    const summary = `${plural(data.files.length, "file")} · ${plural(data.python_tests.length, "test")}`;
    el("project-name").textContent = data.name;
    el("project-summary").textContent = summary;
    el("settings-project").textContent = data.name;
    el("settings-indexer").textContent = data.indexer === "rust" ? "Rust (native, parallel)" : "Python (run scripts/build_native.py for the faster Rust indexer)";
    el("settings-inventory").textContent = `${data.files.length} text files · ${data.python_tests.length} potential Python tests · ${data.manifests.length} manifests`;
    el("welcome-sub").textContent = `Working in ${data.name}. Describe a bug, feature or change — Dikdik works on an isolated copy and shows you every diff.`;
  }
  var lastTasks = [];
  function markCurrent() {
    for (const item of el("history").children) {
      const control = item;
      control.setAttribute("aria-current", String(control.dataset?.id === selected));
    }
  }
  function taskRow(task) {
    const row = taskRowBase(task);
    const tags = task.tags ?? [];
    if (tags.length) {
      const chip = make("span", "conv-tag", tags[0] ?? "");
      if (tags.length > 1)
        chip.textContent += ` +${tags.length - 1}`;
      row.insertBefore(chip, row.querySelector(".conv-more"));
      row.title += ` · ${tags.join(", ")}`;
    }
    return row;
  }
  function taskRowBase(task) {
    const item = make("div", "conv-item");
    item.setAttribute("role", "button");
    item.tabIndex = 0;
    item.dataset.id = task.id;
    const name = conversationLabel(task);
    const label = make("span", "conv-obj", name), state = make("span", `conv-state state-${task.state}`, task.state);
    item.title = `${name} — ${task.state}`;
    item.append(state, label);
    if (task.pinned)
      item.append(make("span", "conv-pin", "Pinned"));
    const more = make("button", "ghost icon conv-more");
    more.type = "button";
    more.setAttribute("aria-label", `Actions for ${name}`);
    more.append(icon("more"));
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      openTaskMenu(task.id, more);
    });
    item.append(more);
    item.setAttribute("aria-current", String(task.id === selected));
    item.onclick = () => {
      selectTask(task.id);
      observe();
    };
    item.addEventListener("keydown", (event) => {
      if (event.target === item && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        item.click();
      }
    });
    return item;
  }
  function collapsedFolders() {
    try {
      return new Set(JSON.parse(storageGet("dikdik.folders.collapsed") ?? "[]"));
    } catch {
      return new Set;
    }
  }
  function renderHistory() {
    const query = (el("task-search").value ?? "").trim();
    const view = organiseTasks(lastTasks, {
      query,
      tag: activeTag,
      sort: select("task-sort").value || "recent",
      sinceDays: Number(select("task-since").value) || 0,
      showArchived,
      selected,
      now: Date.now() / 1000
    });
    if (activeTag && !view.tags.includes(activeTag))
      activeTag = "";
    folderOptions = view.folderNames;
    const closed = collapsedFolders();
    const nodes = [];
    if (view.pinned.length)
      nodes.push(make("p", "list-head", "Pinned"), ...view.pinned.map(taskRow));
    for (const folder of view.folders) {
      const shut = closed.has(folder.name) && !folder.tasks.some((task) => task.id === selected) && !query;
      const head = make("button", "folder-head");
      head.type = "button";
      head.setAttribute("aria-expanded", String(!shut));
      head.append(icon("chevron"), icon("folder"), make("span", "folder-name", folder.name), make("span", "folder-count", String(folder.tasks.length)));
      head.onclick = () => {
        const next = collapsedFolders();
        if (next.has(folder.name))
          next.delete(folder.name);
        else
          next.add(folder.name);
        storageSet("dikdik.folders.collapsed", JSON.stringify([...next]));
        renderHistory();
      };
      nodes.push(head);
      if (!shut)
        nodes.push(...folder.tasks.map(taskRow));
    }
    const rest = view.recent.slice(0, listLimit);
    if (rest.length && (view.pinned.length || view.folders.length))
      nodes.push(make("p", "list-head", showArchived ? "All" : "Recent"));
    nodes.push(...rest.map(taskRow));
    if (view.recent.length > listLimit) {
      const more = make("button", "ghost small show-archived", `Show more (${view.recent.length - listLimit})`);
      more.type = "button";
      more.onclick = () => {
        listLimit += 40;
        renderHistory();
      };
      nodes.push(more);
    }
    if (view.older) {
      const older = make("button", "ghost small show-archived", `${view.older} older hidden · show all`);
      older.type = "button";
      older.onclick = () => {
        select("task-since").value = "0";
        renderHistory();
      };
      nodes.push(older);
    }
    if (!nodes.length)
      nodes.push(make("div", "conv-empty", query || activeTag ? "No matching tasks" : "No tasks yet"));
    el("history").replaceChildren(...nodes);
    el("recent-head").hidden = view.pinned.length > 0 || view.folders.length > 0;
    const chips = el("tag-chips");
    chips.hidden = !view.tags.length;
    chips.replaceChildren(...view.tags.map((tag) => {
      const chip = make("button", "tag-chip", tag);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(tag === activeTag));
      chip.onclick = () => {
        activeTag = activeTag === tag ? "" : tag;
        renderHistory();
      };
      return chip;
    }));
    el("show-archived").hidden = view.archived === 0;
    el("show-archived").textContent = showArchived ? "Hide archived" : `Show archived (${view.archived})`;
    el("delete-archived").hidden = !showArchived || view.archived === 0;
    if (!deletingArchived)
      el("delete-archived").textContent = `Delete all archived (${view.archived})`;
  }
  var deletingArchived = false;
  async function loadHistory() {
    lastTasks = await api("/api/tasks");
    renderHistory();
    renderOnboarding();
  }
  var menuTask = null;
  var confirmingDelete = false;
  function closeTaskMenu() {
    el("task-menu").hidden = true;
    menuTask = null;
    confirmingDelete = false;
  }
  function openTaskMenu(task, anchor) {
    const info = lastTasks.find((item) => item.id === task);
    const menu = el("task-menu");
    menuTask = task;
    confirmingDelete = false;
    for (const node of menu.querySelectorAll("button")) {
      const action = node.dataset.action;
      const label = node.querySelector("span");
      if (label && action === "pin")
        label.textContent = info?.pinned ? "Unpin" : "Pin";
      if (label && action === "archive")
        label.textContent = info?.archived ? "Unarchive" : "Archive";
      if (label && action === "delete")
        label.textContent = "Delete";
    }
    const box = anchor.getBoundingClientRect();
    menu.hidden = false;
    const { offsetWidth: width, offsetHeight: height } = menu;
    menu.style.left = `${Math.max(8, Math.min(box.right - width, innerWidth - width - 8))}px`;
    menu.style.top = `${box.bottom + height + 8 > innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4}px`;
    menu.querySelector("button")?.focus();
  }
  async function patchTask(task, changes) {
    await request(`/api/tasks/${encodeURIComponent(task)}`, "PATCH", changes);
    await loadHistory();
    if (task === selected && "title" in changes)
      el("chat-title").textContent = conversationLabel(lastTasks.find((item) => item.id === task) ?? { id: task, state: "" });
  }
  function startRename(task) {
    const row = [...el("history").querySelectorAll(".conv-item")].find((node) => node.dataset.id === task);
    const info = lastTasks.find((item) => item.id === task);
    if (!row || !info)
      return;
    const field = make("input", "conv-rename");
    field.value = conversationLabel(info);
    field.setAttribute("aria-label", "Task name");
    row.replaceChildren(field);
    field.focus();
    field.select();
    let done = false;
    const finish = async (save) => {
      if (done)
        return;
      done = true;
      try {
        if (save && field.value.trim() !== conversationLabel(info))
          await patchTask(task, { title: field.value });
        else
          renderHistory();
      } catch (error) {
        message(error instanceof Error ? error.message : "Rename failed", true);
        renderHistory();
      }
    };
    field.addEventListener("click", (event) => event.stopPropagation());
    field.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter")
        finish(true);
      if (event.key === "Escape")
        finish(false);
    });
    field.addEventListener("blur", () => {
      finish(true);
    });
  }
  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = make("a");
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function exportTask(task) {
    const data = await api("/api/tasks/" + encodeURIComponent(task));
    const title = conversationLabel({ ...data, objective: data.request.objective, title: lastTasks.find((item) => item.id === task)?.title ?? "" });
    const diff = typeof data.result?.diff === "string" ? data.result.diff : "";
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || "task";
    download(`dikdik-${slug}.md`, transcriptMarkdown(title, eventsToChat(data.events, data.request.objective), diff), "text/markdown");
  }
  async function runMenuAction(action) {
    const task = menuTask;
    if (!task)
      return;
    const info = lastTasks.find((item) => item.id === task);
    try {
      if (action === "rename") {
        closeTaskMenu();
        startRename(task);
      } else if (action === "pin") {
        closeTaskMenu();
        await patchTask(task, { pinned: !info?.pinned });
      } else if (action === "archive") {
        closeTaskMenu();
        await patchTask(task, { archived: !info?.archived });
        message(info?.archived ? "Task restored." : "Task archived.");
      } else if (action === "export") {
        closeTaskMenu();
        await exportTask(task);
      } else if (action === "fork") {
        closeTaskMenu();
        await forkTask(task);
      } else if (action === "organise") {
        closeTaskMenu();
        openMeta(task);
      } else if (action === "download") {
        closeTaskMenu();
        await downloadTask(task);
      } else if (action === "info") {
        closeTaskMenu();
        await showAgentInfo();
      } else if (action === "usage") {
        closeTaskMenu();
        if (task !== selected) {
          selectTask(task);
          await observe();
        }
        openCanvas("usage");
      } else if (action === "delete") {
        if (!confirmingDelete) {
          confirmingDelete = true;
          const label = el("task-menu").querySelector("[data-action=delete] span");
          if (label)
            label.textContent = "Click again to delete";
          return;
        }
        closeTaskMenu();
        await request(`/api/tasks/${encodeURIComponent(task)}`, "DELETE");
        if (task === selected)
          newTask();
        await loadHistory();
        message("Task deleted.");
      }
    } catch (error) {
      message(error instanceof Error ? error.message : "Action failed", true);
    }
  }
  var live = null;
  var liveTask = null;
  var liveEvents = [];
  var liveObjective = "";
  var liveSeq = 0;
  function stopStream() {
    live?.close();
    live = null;
    liveTask = null;
    showLiveReply("");
  }
  function showLiveReply(text) {
    const existing = document.getElementById?.("live-reply");
    if (!text) {
      existing?.remove?.();
      if (existing)
        el("working-label").textContent = "Working";
      return;
    }
    const li = existing ?? make("li", "msg msg-assistant msg-live");
    if (!existing) {
      li.id = "live-reply";
      const avatar = make("img", "avatar");
      avatar.src = "/icon.svg";
      avatar.alt = "";
      li.append(avatar, make("div"));
    }
    const body = li.lastElementChild;
    body.replaceChildren(make("div", "msg-label", "Dikdik"), richText(text));
    el("message-stream").append(li);
    el("working-label").textContent = "Writing";
    const thread = el("thread");
    if (nearBottom(thread))
      thread.scrollTop = thread.scrollHeight;
  }
  function startStream(id) {
    if (typeof globalThis.EventSource !== "function" || token || liveTask === id)
      return;
    stopStream();
    liveTask = id;
    const source = new EventSource(`/api/tasks/${encodeURIComponent(id)}/stream?after=${liveSeq}`);
    live = source;
    source.addEventListener("task", (event) => {
      if (id !== selected)
        return stopStream();
      const record = JSON.parse(event.data);
      if (record.seq <= liveSeq)
        return;
      liveSeq = record.seq;
      liveEvents.push(record);
      if (record.kind === "agent_message" || record.kind === "agent_tool")
        showLiveReply("");
      renderMessageStream(eventsToChat(liveEvents, liveObjective));
      if (record.kind === "terminal_command" || record.kind === "terminal_output")
        renderTerminal();
      if (record.kind === "browser_started" || record.kind === "browser_result")
        renderBrowser();
      if (record.kind === "plan_updated")
        loadPlan(id);
      if (record.kind === "task_tracker")
        renderChecklist();
      if ((record.kind === "agent_model" || record.kind === "running") && canvasTab === "usage" && !el("canvas").hidden)
        renderUsage();
      if (record.kind === "running" || isTerminal(record.kind)) {
        setState(record.kind);
        selectedState = record.kind;
        renderPlanner();
        loadHistory();
        if (isTerminal(record.kind)) {
          chime();
          refreshFiles();
        }
      }
      if (record.kind === "running") {
        el("working").hidden = false;
        el("working-label").textContent = "Working";
        button("cancel").disabled = false;
      } else if (isTerminal(record.kind)) {
        el("working").hidden = true;
        button("cancel").disabled = true;
      }
      renderRunState();
    });
    source.addEventListener("delta", (event) => {
      if (id !== selected)
        return stopStream();
      showLiveReply(JSON.parse(event.data).text);
    });
    source.addEventListener("end", () => {
      stopStream();
      watching = true;
      observe();
    });
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED || liveTask !== id) {
        stopStream();
        watching = true;
      }
    };
  }
  async function observe(force = false) {
    if (force) {
      viewEpoch++;
      watching = true;
    }
    if (polling) {
      observeQueued = true;
      return;
    }
    if (!selected || !watching || liveTask === selected)
      return;
    polling = true;
    const id = selected, epoch = viewEpoch;
    try {
      const task = await api("/api/tasks/" + id);
      if (id !== selected || epoch !== viewEpoch)
        return;
      liveEvents = [...task.events];
      liveObjective = task.request.objective;
      liveSeq = liveEvents.reduce((max, event) => Math.max(max, event.seq ?? 0), 0);
      el("chat-title").textContent = conversationLabel({ ...task, objective: task.request.objective, title: lastTasks.find((item) => item.id === id)?.title ?? "" });
      setTitle(el("chat-title").textContent ?? "");
      setState(task.state);
      selectedState = task.state;
      parentTask = typeof task.request.parent_task === "string" ? task.request.parent_task : null;
      taskRequest = task.request;
      if (!observedTaskMode) {
        if (task.request.mode === "plan")
          select("mode").value = "plan";
        else if (select("mode").value === "plan")
          select("mode").value = "agent";
        observedTaskMode = task.request.mode || "unknown";
        syncMode();
        syncComposer();
      }
      renderTerminal();
      renderBrowser();
      if (canvasTab === "usage")
        renderUsage();
      renderChecklist();
      await loadPlan(id);
      if (id !== selected || epoch !== viewEpoch)
        return;
      button("cancel").disabled = isTerminal(task.state);
      const messages = eventsToChat(task.events, task.request.objective);
      lastMessages = messages;
      renderMessageStream(messages);
      el("working").hidden = isTerminal(task.state);
      el("working-label").textContent = task.state === "queued" ? "Queued" : "Working";
      renderRunState();
      renderReview(task.id, task.state, task.result);
      syncApplyBar();
      const known = lastTasks.find((item) => item.id === id);
      if (isTerminal(task.state) || known && known.state !== task.state)
        await loadHistory();
      if (id !== selected || epoch !== viewEpoch)
        return;
      if (isTerminal(task.state))
        watching = false;
      else
        startStream(id);
    } catch (error) {
      if (error instanceof Error)
        message(error.message, true);
    } finally {
      polling = false;
      if (observeQueued) {
        observeQueued = false;
        observe();
      }
    }
  }
  var m4Create = (tag) => document.createElement(tag);
  function mountPanel(containerId, panel) {
    const container = document.getElementById(containerId);
    if (!container)
      throw new Error(`Missing M4 panel container: ${containerId}`);
    container.replaceChildren();
    return panel({ root: container, create: m4Create, request: apiM4 }).refresh;
  }
  var refreshPanels = {};
  async function connect() {
    await project();
    await loadHistory();
    loadModel().catch(() => {
      return;
    });
    loadAgents().catch(() => {
      return;
    });
    el("boot").hidden = true;
    el("connect-view").hidden = true;
    el("workspace").hidden = false;
    if (!connected) {
      connected = true;
      refreshPanels = {
        models: modelsPane(el("panel-profiles"), paneDeps),
        secrets: keysPane(el("panel-secrets"), paneDeps),
        skills: mountPanel("panel-skills", skillsPanel)
      };
    }
    textarea("objective").focus?.();
    const linked = globalThis.location ? new URLSearchParams(globalThis.location.search).get("task") : null;
    if (linked && !selected && lastTasks.some((task) => task.id === linked))
      selectTask(linked);
    else
      await composerDrafts.select(selected ?? "home");
    observe();
    loadAppSettings().catch(() => {
      return;
    });
    loadExposedSecrets();
    routeSettings();
  }
  function showSignIn() {
    el("boot").hidden = true;
    el("workspace").hidden = true;
    el("connect-view").hidden = false;
  }
  async function startSession(value) {
    try {
      await request("/api/session", "POST", { token: value });
      return true;
    } catch (error) {
      message(error instanceof Error ? error.message : "Sign-in failed", true);
      return false;
    }
  }
  async function boot() {
    const loc = globalThis.location;
    const match = loc ? /^#token=([\w-]+)$/.exec(loc.hash) : null;
    if (match && loc) {
      globalThis.history?.replaceState(null, "", loc.pathname + loc.search);
      await startSession(match[1] ?? "");
    }
    try {
      await connect();
    } catch (error) {
      if (error instanceof RequestError && error.status === 401)
        showSignIn();
      else {
        showSignIn();
        message(error instanceof Error ? error.message : "Dikdik is not reachable", true);
      }
    }
  }
  el("connect-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = input("token").value.trim().replace(/^.*#token=/, "");
    token = value;
    try {
      await startSession(value);
      await connect();
      input("token").value = "";
      message("Signed in. This browser will stay signed in.");
    } catch (error) {
      token = "";
      message(error instanceof Error ? error.message : "Connection failed", true);
    }
  });
  function setSidebarCollapsed(collapsed) {
    setClass(el("workspace"), "sidebar-collapsed", collapsed);
    el("show-sidebar").hidden = !collapsed;
    storageSet("dikdik.sidebar", collapsed ? "collapsed" : "open");
  }
  function closeSidebarDrawer() {
    setClass(el("workspace"), "sidebar-open", false);
  }
  el("toggle-sidebar").addEventListener("click", () => {
    if (wide("(max-width: 760px)"))
      closeSidebarDrawer();
    else
      setSidebarCollapsed(true);
  });
  el("show-sidebar").addEventListener("click", () => setSidebarCollapsed(false));
  el("toggle-sidebar-mobile").addEventListener("click", () => setClass(el("workspace"), "sidebar-open", true));
  el("scrim").addEventListener("click", closeSidebarDrawer);
  if (storageGet("dikdik.sidebar") === "collapsed")
    setSidebarCollapsed(true);
  el("toggle-canvas").addEventListener("click", () => {
    if (el("canvas").hidden === true || canvasTab !== "changes")
      openCanvas("changes");
    else
      setCanvas(false);
  });
  el("open-files").addEventListener("click", () => {
    if (el("canvas").hidden === true || canvasTab === "changes")
      openCanvas("files");
    else
      setCanvas(false);
  });
  el("apply").addEventListener("click", () => {
    applyChanges();
  });
  el("apply-cancel").addEventListener("click", () => {
    applyStep = "idle";
    syncApplyBar();
  });
  el("apply-commit").addEventListener("click", () => openCanvas("git"));
  el("file-search").addEventListener("input", renderFileList);
  el("copy-file").addEventListener("click", () => {
    copyText(el("file-view").textContent ?? "", el("copy-file"));
  });
  if (typeof el("canvas").querySelectorAll === "function") {
    for (const tab of el("canvas").querySelectorAll(".canvas-tab"))
      tab.addEventListener("click", () => openCanvas(tab.dataset.tab ?? "changes"));
    for (const node of el("file-version").querySelectorAll("button"))
      node.addEventListener("click", () => {
        if (openFile)
          showFile(openFile, node.dataset.version ?? "workspace");
      });
  }
  el("close-canvas").addEventListener("click", () => setCanvas(false));
  el("copy-diff").addEventListener("click", () => {
    copyText(renderedDiff, el("copy-diff"));
  });
  el("new-task").addEventListener("click", newTask);
  el("task-search").addEventListener("input", renderHistory);
  el("show-archived").addEventListener("click", () => {
    showArchived = !showArchived;
    renderHistory();
  });
  el("task-actions").addEventListener("click", () => {
    if (selected)
      openTaskMenu(selected, el("task-actions"));
  });
  if (typeof el("task-menu").querySelectorAll === "function") {
    for (const node of el("task-menu").querySelectorAll("button"))
      node.addEventListener("click", () => {
        runMenuAction(node.dataset.action ?? "");
      });
  }
  if (typeof document.addEventListener === "function") {
    document.addEventListener("click", (event) => {
      const menu = el("task-menu");
      if (!menu.hidden && event.target instanceof Node && !menu.contains(event.target) && !event.target.closest?.(".conv-more, #task-actions"))
        closeTaskMenu();
    });
  }
  el("thread").addEventListener("scroll", () => {
    setClass(el("main"), "scrolled", el("thread").scrollTop > 4);
  });
  function applyTheme(theme) {
    const root = globalThis.document?.documentElement;
    if (!root)
      return;
    if (theme === "light" || theme === "dark")
      root.dataset.theme = theme;
    else
      delete root.dataset.theme;
  }
  var themeSelect = el("theme");
  var savedTheme = storageGet("dikdik.theme") ?? "system";
  applyTheme(savedTheme);
  themeSelect.value = savedTheme;
  themeSelect.addEventListener("change", () => {
    storageSet("dikdik.theme", themeSelect.value);
    applyTheme(themeSelect.value);
  });
  var PANE_TITLES = { general: "General", models: "Models", agents: "Agents", secrets: "API keys", skills: "Skills", mcp: "MCP servers", automations: "Automations" };
  var automations = [];
  function syncAutoForm() {
    const daily = el("auto-kind").value === "daily";
    el("auto-daily").hidden = !daily;
    el("auto-minutes").hidden = daily;
    const agent = el("auto-mode").value === "agent";
    const picker = el("auto-profile");
    picker.hidden = !agent || !agentProfiles.length;
    if (picker.options && picker.options.length !== agentProfiles.length + 1) {
      const first = make("option", "", "Default agent");
      first.value = "";
      picker.replaceChildren(first, ...agentProfiles.map((profile) => {
        const option = make("option", "", profile.name);
        option.value = profile.id;
        return option;
      }));
    }
    el("auto-execution-field").hidden = el("auto-verification").value !== "docker";
  }
  function fillAutoForm(auto) {
    const field = (id) => el(id);
    field("auto-id").value = auto?.id ?? "";
    field("auto-name").value = auto?.name ?? "";
    el("auto-objective").value = auto?.objective ?? "";
    const daily = !auto || "daily_at" in auto.schedule;
    el("auto-kind").value = daily ? "daily" : "interval";
    field("auto-daily").value = auto && "daily_at" in auto.schedule ? auto.schedule.daily_at : "07:00";
    field("auto-minutes").value = auto && "interval_seconds" in auto.schedule ? String(Math.round(auto.schedule.interval_seconds / 60)) : "60";
    el("auto-mode").value = auto?.run?.mode ?? "agent";
    el("auto-verification").value = auto?.run?.verification ?? "syntax";
    syncAutoForm();
    el("auto-profile").value = auto?.run?.agent_profile ?? "";
    field("auto-external").checked = auto?.run?.approve_external ?? false;
    field("auto-execution").checked = auto?.run?.approve_execution ?? false;
    el("auto-save").textContent = auto ? "Save changes" : "Add automation";
    el("auto-cancel").hidden = !auto;
  }
  async function loadAutomations() {
    try {
      automations = parseAutomations(await api("/api/automations"));
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not load automations", true);
      return;
    }
    syncAutoForm();
    el("auto-list").replaceChildren(...automations.map((auto) => {
      const row = make("li", "m4-row");
      const head = make("p", "m4-title");
      const state = auto.last_state ?? "";
      head.append(make("span", "m4-strong", auto.name));
      if (state)
        head.append(make("span", `auto-state state-${state}`, state === "needs_setup" ? "needs setup" : state));
      const next = typeof auto.next_run === "number" ? new Date(auto.next_run * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : "";
      const how = auto.run ? `${auto.run.mode === "agent" ? "Agent" : "Single proposal"} · ${auto.run.verification === "docker" ? "Docker tests" : "syntax check"}` : "Edit to choose a mode and approvals";
      row.append(head, make("span", "m4-meta", `${describeSchedule(auto.schedule)}${auto.enabled && next ? ` · next ${next}` : ""} · ${how}${auto.failures ? ` · ${auto.failures} failed in a row` : ""}`));
      row.append(make("p", "m4-objective", auto.objective));
      const actions = make("span", "m4-actions");
      const toggle = make("input");
      toggle.type = "checkbox";
      toggle.checked = auto.enabled;
      toggle.setAttribute("aria-label", `Enable ${auto.name}`);
      toggle.onchange = async () => {
        try {
          await request(`/api/automations/${auto.id}`, "PATCH", { enabled: toggle.checked });
          await loadAutomations();
        } catch (error) {
          toggle.checked = !toggle.checked;
          message(error instanceof Error ? error.message : "Could not update", true);
        }
      };
      const enabled = make("label", "check");
      enabled.append(toggle, make("span", "", "On"));
      const runNow = make("button", "secondary", "Run now");
      runNow.type = "button";
      runNow.onclick = async () => {
        try {
          const started = await request(`/api/automations/${auto.id}/run`, "POST", {});
          el("settings").close?.();
          await loadHistory();
          selectTask(started.task_id);
          observe();
        } catch (error) {
          message(error instanceof Error ? error.message : "Could not start a run", true);
        }
      };
      const edit = make("button", "secondary", "Edit");
      edit.type = "button";
      edit.onclick = () => fillAutoForm(auto);
      const runsButton = make("button", "secondary", "Runs");
      runsButton.type = "button";
      const runs = make("ol", "auto-runs");
      runsButton.onclick = async () => {
        if (runs.childElementCount) {
          runs.replaceChildren();
          return;
        }
        try {
          const list = parseRunSummaries(await api(`/api/automations/${auto.id}/runs`));
          runs.replaceChildren(...list.length ? list.slice(0, 20).map((run) => {
            const item = make("li");
            const open = make("button");
            open.type = "button";
            open.append(make("span", `auto-state state-${run.state}`, run.state), make("span", "", run.created ? new Date(run.created * 1000).toLocaleString() : run.id.slice(0, 8)));
            open.onclick = () => {
              el("settings").close?.();
              selectTask(run.id);
              observe();
            };
            item.append(open);
            return item;
          }) : [make("li", "hint", "No runs yet.")]);
        } catch (error) {
          message(error instanceof Error ? error.message : "Could not load runs", true);
        }
      };
      const remove = make("button", "secondary", "Delete");
      remove.type = "button";
      remove.onclick = async () => {
        try {
          await request(`/api/automations/${auto.id}`, "DELETE");
          await loadAutomations();
        } catch (error) {
          message(error instanceof Error ? error.message : "Delete failed", true);
        }
      };
      actions.append(enabled, runNow, edit, runsButton, remove);
      row.append(actions, runs);
      return row;
    }));
    if (!automations.length)
      el("auto-list").replaceChildren(make("li", "hint", "No automations yet."));
  }
  for (const id of ["auto-kind", "auto-mode", "auto-verification"])
    el(id).addEventListener("change", syncAutoForm);
  el("auto-cancel").addEventListener("click", () => fillAutoForm(null));
  el("auto-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = (id) => el(id);
    try {
      const body = buildAutomation({
        name: field("auto-name").value,
        objective: el("auto-objective").value,
        scheduleKind: el("auto-kind").value === "daily" ? "daily" : "interval",
        intervalMinutes: field("auto-minutes").value,
        dailyAt: field("auto-daily").value,
        mode: el("auto-mode").value,
        verification: el("auto-verification").value,
        approveExternal: field("auto-external").checked,
        approveExecution: field("auto-execution").checked,
        agentProfile: el("auto-profile").value
      });
      const id = field("auto-id").value;
      await request(id ? `/api/automations/${id}` : "/api/automations", id ? "PATCH" : "POST", body);
      message(id ? "Automation saved." : "Automation added.");
      fillAutoForm(null);
      await loadAutomations();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not save the automation", true);
    }
  });
  async function loadMcp() {
    try {
      const servers = await api("/api/mcp");
      el("mcp-list").replaceChildren(...servers.map((server) => {
        const row = make("li", "m4-row");
        const head = make("p", "m4-title");
        head.append(make("span", "m4-strong", server.name), make("span", "m4-badge", server.transport === "http" ? "remote" : "command"));
        const target = make("span", "m4-meta m4-mono", server.transport === "http" ? server.url : server.command);
        const result = make("p", "hint");
        const actions = make("span", "m4-actions");
        const toggle = make("input");
        toggle.type = "checkbox";
        toggle.checked = server.enabled;
        toggle.setAttribute("aria-label", `Enable ${server.name}`);
        toggle.onchange = async () => {
          try {
            await request(`/api/mcp/${server.id}`, "PATCH", { enabled: toggle.checked });
            message(toggle.checked ? `${server.name} enabled for agent tasks.` : `${server.name} disabled.`);
          } catch (error) {
            toggle.checked = !toggle.checked;
            message(error instanceof Error ? error.message : "Could not update", true);
          }
        };
        const enabled = make("label", "check");
        enabled.append(toggle, make("span", "", "Enabled"));
        const test = make("button", "secondary", "Test");
        test.type = "button";
        test.onclick = async () => {
          result.textContent = "Connecting…";
          result.className = "hint";
          try {
            const info = await request(`/api/mcp/${server.id}/test`, "POST", {});
            result.textContent = `Connected${info.server ? ` to ${info.server}` : ""}: ${info.tools.length} tool${info.tools.length === 1 ? "" : "s"} (${info.tools.map((t) => t.name).join(", ") || "none"})`;
          } catch (error) {
            result.textContent = error instanceof Error ? error.message : "Connection failed";
            result.className = "error";
          }
        };
        const remove = make("button", "secondary", "Delete");
        remove.type = "button";
        remove.onclick = async () => {
          try {
            await request(`/api/mcp/${server.id}`, "DELETE");
            await loadMcp();
          } catch (error) {
            message(error instanceof Error ? error.message : "Delete failed", true);
          }
        };
        actions.append(enabled, test, remove);
        row.append(head, target, actions, result);
        return row;
      }));
      if (!servers.length)
        el("mcp-list").replaceChildren(make("li", "hint", "No MCP servers yet."));
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not load MCP servers", true);
    }
  }
  function syncMcpForm() {
    const stdio = el("mcp-transport").value === "stdio";
    if (typeof el("mcp-form").querySelectorAll !== "function")
      return;
    for (const node of el("mcp-form").querySelectorAll(".mcp-http"))
      node.hidden = stdio;
    for (const node of el("mcp-form").querySelectorAll(".mcp-stdio"))
      node.hidden = !stdio;
  }
  el("mcp-transport").addEventListener("change", syncMcpForm);
  el("mcp-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = (id) => el(id).value.trim();
    const transport = value("mcp-transport");
    try {
      await request("/api/mcp", "POST", {
        name: value("mcp-name"),
        transport,
        ...transport === "http" ? { url: value("mcp-url") } : { command: value("mcp-command"), env_var: value("mcp-env") || undefined },
        secret_ref: value("mcp-secret") || undefined
      });
      for (const id of ["mcp-name", "mcp-url", "mcp-command", "mcp-secret", "mcp-env"])
        el(id).value = "";
      message("Server added. Test it, then enable it for agent tasks.");
      await loadMcp();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not add the server", true);
    }
  });
  function showPane(name) {
    if (name === "general")
      loadBrowserSettings().catch((error) => message(error instanceof Error ? error.message : "Could not load browser setting", true));
    const dialog = el("settings");
    for (const tab of dialog.querySelectorAll(".settings-tab"))
      tab.setAttribute("aria-selected", String(tab.dataset.pane === name));
    for (const pane of dialog.querySelectorAll(".settings-pane"))
      pane.hidden = pane.dataset.pane !== name;
    el("settings-pane-title").textContent = PANE_TITLES[name] ?? "Settings";
    settingsHash(name);
    if (name === "mcp")
      loadMcp();
    if (name === "automations") {
      loadAutomations();
      loadAgents().catch(() => {
        return;
      });
    }
    if (name === "agents") {
      loadAgents().catch(() => {
        return;
      });
      loadAgentModels().catch(() => {
        return;
      });
    }
    refreshPanels[name]?.();
  }
  function openSettings(pane) {
    const dialog = el("settings");
    if (typeof dialog.showModal === "function" && !dialog.open)
      dialog.showModal();
    showPane(pane);
    closeSidebarDrawer();
    loadAppSettings().catch(() => {
      return;
    });
  }
  el("open-settings").addEventListener("click", () => openSettings("general"));
  el("model-pill").addEventListener("click", (event) => {
    event.stopPropagation();
    openModelMenu();
  });
  el("settings").addEventListener("close", () => {
    settingsHash(null);
  });
  el("settings").addEventListener("close", () => {
    loadModel().catch(() => {
      return;
    });
    loadAgents().catch(() => {
      return;
    });
    ensureLists(true);
  });
  el("skill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await request("/api/skills", "POST", {
        name: el("skill-name").value,
        scope: el("skill-scope").value,
        description: el("skill-description").value,
        content: el("skill-content").value
      });
      for (const id of ["skill-name", "skill-description", "skill-content"])
        el(id).value = "";
      message("Skill added. The agent will read it from the next task.");
      await refreshPanels.skills?.();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not add the skill", true);
    }
  });
  el("close-settings").addEventListener("click", () => el("settings").close());
  if (typeof el("settings").querySelectorAll === "function") {
    for (const tab of el("settings").querySelectorAll(".settings-tab"))
      tab.addEventListener("click", () => showPane(tab.dataset.pane ?? "general"));
  }
  if (typeof document.addEventListener === "function") {
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented)
        return;
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && select("mode").value === "plan" && !textarea("objective").value.trim()) {
        event.preventDefault();
        buildReviewedPlan();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        togglePalette();
      } else if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "o") {
        event.preventDefault();
        newTask();
      } else if (event.key === "Escape" && !el("task-menu").hidden)
        closeTaskMenu();
      else if (event.key === "Escape" && !el("canvas").hidden && !el("settings").open)
        setCanvas(false);
    });
  }
  function autosize() {
    syncSend();
    const area = textarea("objective");
    if (!area.style)
      return;
    const maximum = composerMaximum(globalThis.innerHeight || 900, Math.max(0, (el("task-form").offsetHeight || 0) - (area.offsetHeight || 0)));
    area.style.maxHeight = `${maximum}px`;
    area.style.height = "auto";
    const height = composerSize.fit(area.value, area.scrollHeight, maximum);
    area.style.height = `${height}px`;
    el("composer-resize").setAttribute("aria-valuenow", String(Math.round(height)));
    el("composer-resize").setAttribute("aria-valuemax", String(Math.round(maximum)));
  }
  var composerDrag = null;
  var composerGrip = el("composer-resize");
  function resizeComposer(height) {
    const area = textarea("objective");
    const maximum = composerMaximum(globalThis.innerHeight || 900, Math.max(0, el("task-form").offsetHeight - area.offsetHeight));
    composerSize.resize(height, maximum);
    autosize();
  }
  composerGrip.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0 || textarea("objective").disabled)
      return;
    event.preventDefault();
    composerDrag = { pointer: event.pointerId, y: event.clientY, height: textarea("objective").getBoundingClientRect().height };
    composerGrip.setPointerCapture(event.pointerId);
    composerGrip.blur();
  });
  composerGrip.addEventListener("pointermove", (event) => {
    if (composerDrag?.pointer !== event.pointerId)
      return;
    resizeComposer(composerDrag.height + composerDrag.y - event.clientY);
  });
  function endComposerDrag(event) {
    if (composerDrag?.pointer !== event.pointerId)
      return;
    composerDrag = null;
    if (composerGrip.hasPointerCapture(event.pointerId))
      composerGrip.releasePointerCapture(event.pointerId);
  }
  composerGrip.addEventListener("pointerup", endComposerDrag);
  composerGrip.addEventListener("pointercancel", endComposerDrag);
  composerGrip.addEventListener("lostpointercapture", () => {
    composerDrag = null;
  });
  composerGrip.addEventListener("keydown", (event) => {
    if (textarea("objective").disabled)
      return;
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      resizeComposer(textarea("objective").getBoundingClientRect().height + (event.key === "ArrowUp" ? 24 : -24));
    } else if (event.key === "Home") {
      event.preventDefault();
      composerSize.reset();
      autosize();
    }
  });
  composerGrip.addEventListener("dblclick", () => {
    composerSize.reset();
    autosize();
  });
  globalThis.addEventListener?.("resize", autosize);
  el("draft-conflict").addEventListener("toggle", autosize);
  function setOptions(open) {
    el("options-panel").hidden = !open;
    el("options-toggle").setAttribute("aria-expanded", String(open));
    autosize();
  }
  function syncMode() {
    const mode = parseMode(select("mode").value);
    const policy = modePolicy(mode, parseVerification(select("verification").value));
    el("offline-fields").hidden = !policy.showEdits;
    textarea("edits").disabled = !policy.showEdits;
    el("model-fields").hidden = !policy.requiresExternal;
    el("model-hint").hidden = mode !== "model";
    el("agent-hint").hidden = mode !== "agent";
    if (mode === "plan") {
      el("agent-hint").hidden = false;
      el("agent-hint").textContent = "The planner can inspect files and write a private PLAN.md for review. It cannot execute tools or modify repository files.";
    }
    el("external-field").hidden = !policy.requiresExternal;
    input("external").disabled = !policy.requiresExternal;
    input("external").required = policy.requiresExternal;
    select("verification").value = policy.verification;
    select("verification").disabled = policy.locksVerification;
    el("browser-field").hidden = mode !== "agent";
    input("browser-approval").disabled = mode !== "agent";
    const execution = policy.requiresExecution || mode === "agent" && input("browser-approval").checked;
    el("secrets-field").hidden = !(mode === "agent" && execution && exposedSecrets > 0);
    input("secrets-approval").disabled = el("secrets-field").hidden === true;
    el("execution-field").hidden = !execution;
    input("execution").disabled = !execution;
    input("execution").required = execution;
    el("execution-field").title = "Runs approved tools and tests inside restricted Docker containers.";
    el("model-pill").hidden = !policy.requiresExternal;
    syncModelBanner();
    el("agent-profile-pill").hidden = !["agent", "plan"].includes(mode) || !agentProfiles.length;
    if (policy.showEdits)
      setOptions(true);
    syncComposer();
    autosize();
  }
  select("mode").addEventListener("change", syncMode);
  select("verification").addEventListener("change", syncMode);
  el("browser-approval").addEventListener("change", syncMode);
  el("options-toggle").addEventListener("click", () => setOptions(el("options-panel").hidden === true));
  syncMode();
  textarea("objective").addEventListener("input", () => {
    autosize();
    syncSend();
    composerDrafts.update(textarea("objective").value);
    renderSlash();
  });
  textarea("objective").addEventListener("keydown", (event) => {
    if (slashKey(event))
      return;
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && select("mode").value === "plan" && !textarea("objective").value.trim()) {
      event.preventDefault();
      buildReviewedPlan();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      const form = el("task-form");
      if (typeof form.requestSubmit === "function")
        form.requestSubmit();
      else
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    }
  });
  if (typeof document.querySelectorAll === "function") {
    for (const suggestion of document.querySelectorAll(".suggestion")) {
      suggestion.addEventListener("click", () => {
        textarea("objective").value = suggestion.dataset.prompt ?? "";
        composerDrafts.update(textarea("objective").value);
        autosize();
        textarea("objective").focus();
      });
    }
  }
  function composerRequest(text, goalMaxTurns) {
    return buildTaskRequest({
      modelProfile: modelOverride?.id,
      attachments: attached.map((file) => file.id),
      goalMaxTurns,
      objective: text,
      mode: parseMode(select("mode").value),
      edits: textarea("edits").value,
      verification: parseVerification(select("verification").value),
      approveExecution: input("execution").checked,
      approveExternal: input("external").checked,
      approveBrowser: input("browser-approval").checked,
      agentProfile: el("agent-profile").value ?? "",
      approveSecrets: input("secrets-approval").checked
    });
  }
  async function loadAgents() {
    agentProfiles = await api("/api/agent-profiles");
    const picker = el("agent-profile");
    const current = picker.value;
    picker.replaceChildren(make("option", "", "Default agent"), ...agentProfiles.map((profile) => {
      const option = make("option", "", profile.name);
      option.value = profile.id;
      return option;
    }));
    picker.firstChild?.setAttribute("value", "");
    picker.value = agentProfiles.some((profile) => profile.id === current) ? current : "";
    el("agent-profile-pill").hidden = parseMode(select("mode").value) !== "agent" || !agentProfiles.length;
    renderAgentList();
  }
  function renderAgentList() {
    const list = el("agent-list");
    if (typeof list.replaceChildren !== "function")
      return;
    list.replaceChildren(...agentProfiles.map((profile) => {
      const row = make("li", "m4-row");
      const head = make("p", "m4-title");
      head.append(make("span", "m4-strong", profile.name));
      const tools = [profile.allow_shell && "shell", profile.allow_browser && "browser", profile.use_mcp && "MCP", profile.use_skills && "skills"].filter(Boolean).join(", ") || "no extra tools";
      const limits = [profile.max_model_calls && `${profile.max_model_calls} model calls`, profile.deadline_seconds && `${profile.deadline_seconds}s`].filter(Boolean).join(" · ");
      row.append(head, make("span", "m4-meta", `${tools}${limits ? " · " + limits : ""}`));
      if (profile.instructions)
        row.append(make("p", "m4-objective", profile.instructions));
      const actions = make("span", "m4-actions");
      const edit = make("button", "secondary", "Edit");
      edit.type = "button";
      edit.onclick = () => fillAgentForm(profile);
      const remove = make("button", "secondary", "Delete");
      remove.type = "button";
      remove.onclick = async () => {
        try {
          await request(`/api/agent-profiles/${profile.id}`, "DELETE");
          await loadAgents();
        } catch (error) {
          message(error instanceof Error ? error.message : "Delete failed", true);
        }
      };
      actions.append(edit, remove);
      row.append(actions);
      return row;
    }));
    if (!agentProfiles.length)
      list.replaceChildren(make("li", "hint", "No agent profiles yet. The default agent uses your default model and every tool your approvals allow."));
  }
  function fillAgentForm(profile) {
    const field = (id) => el(id);
    field("agent-id").value = profile?.id ?? "";
    field("agent-name").value = profile?.name ?? "";
    el("agent-instructions").value = profile?.instructions ?? "";
    el("agent-model").value = profile?.model_profile_id ?? "";
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
  async function loadAgentModels() {
    const models = await api("/api/profiles");
    const picker = el("agent-model");
    const current = picker.value;
    const first = make("option", "", "Default model profile");
    first.value = "";
    picker.replaceChildren(first, ...models.map((model) => {
      const option = make("option", "", `${model.name} (${model.model})`);
      option.value = model.id;
      return option;
    }));
    picker.value = current;
  }
  el("agent-cancel").addEventListener("click", () => fillAgentForm(null));
  el("agent-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = (id2) => el(id2);
    const number = (id2) => field(id2).value.trim() ? Number(field(id2).value) : null;
    const body = {
      name: field("agent-name").value,
      instructions: el("agent-instructions").value,
      model_profile_id: el("agent-model").value || null,
      max_model_calls: number("agent-steps"),
      deadline_seconds: number("agent-minutes"),
      allow_shell: field("agent-shell").checked,
      allow_browser: field("agent-browser").checked,
      use_mcp: field("agent-mcp").checked,
      use_skills: field("agent-skills").checked,
      critic: field("agent-critic").checked,
      memory: field("agent-memory").checked,
      secret_refs: secretRefsField(field("agent-secrets").value)
    };
    const id = field("agent-id").value;
    try {
      await request(id ? `/api/agent-profiles/${id}` : "/api/agent-profiles", id ? "PATCH" : "POST", body);
      message(id ? "Agent profile saved." : "Agent profile added. Pick it in the composer.");
      fillAgentForm(null);
      await loadAgents();
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not save the agent profile", true);
    }
  });
  async function sendMessage(text, goalMaxTurns) {
    if (!selected)
      return false;
    const task = selected;
    try {
      const reply = await api(`/api/tasks/${encodeURIComponent(task)}/messages`, composerRequest(text, goalMaxTurns));
      clearAttachments();
      if (task === selected)
        watching = true;
      if (reply.queued)
        message("Queued. Dikdik will reply when the current step finishes.");
    } catch (error) {
      message(error instanceof Error ? error.message : "Message failed", true);
      return false;
    }
    try {
      await loadHistory();
      if (task === selected)
        await observe(true);
    } catch {
      message("Message sent. Refresh to load the latest conversation.", true);
    }
    return true;
  }
  el("task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !textarea("objective").value.trim() && !attached.length)
      return;
    hideSlash();
    submitting = true;
    syncSend();
    textarea("objective").disabled = true;
    const originalScope = composerDrafts.scope, originalText = textarea("objective").value;
    try {
      let text = textarea("objective").value;
      let goalTurns2;
      const slash = parseSlash(text);
      const command = slash && (slash.name === "plan" || slash.name === "code") ? slash : null;
      if (command) {
        if (command.name === "plan")
          await enterPlanning();
        else
          await enterCode();
        text = command.arg;
        textarea("objective").value = text;
        if (!text) {
          await composerDrafts.sent(originalScope, originalText);
          return;
        }
      } else if (slash) {
        const done = await runSlash(slash.name, slash.arg);
        if (done === true) {
          await composerDrafts.sent(originalScope, originalText);
          return;
        }
        if (done === false)
          return;
        ({ text, turns: goalTurns2 } = done);
      }
      if (!text.trim() && attached.length)
        text = attached.length === 1 ? `Take a look at the attached file “${attached[0]?.name ?? "file"}”.` : "Take a look at the attached files.";
      if (selected && select("mode").value === "plan" && observedTaskMode !== "plan" && command?.name !== "plan")
        await enterPlanning();
      if (selected) {
        composerRequest(text, goalTurns2);
        if (await sendMessage(text, goalTurns2))
          await composerDrafts.sent(originalScope, originalText);
        return;
      }
      const task = await api("/api/tasks", composerRequest(text, goalTurns2));
      clearAttachments();
      await composerDrafts.sent(originalScope, originalText);
      selectTask(task.id);
      await loadHistory();
      await observe();
    } catch (error) {
      message(error instanceof Error ? error.message : "Task failed", true);
    } finally {
      submitting = false;
      textarea("objective").disabled = false;
      composerDrafts.display();
      autosize();
      syncSend();
    }
  });
  async function ensureLists(force = false) {
    if (!force && (modelProfiles.length || skillNames.length))
      return;
    const [models, skills] = await Promise.all([
      api("/api/profiles").catch(() => []),
      request("/api/skills", "GET").catch(() => [])
    ]);
    modelProfiles = Array.isArray(models) ? models : [];
    skillNames = (Array.isArray(skills) ? skills : []).flatMap((item) => {
      const skill = item;
      return typeof skill.name === "string" && skill.enabled !== false && /^[a-z0-9_-]{1,64}$/i.test(skill.name) ? [skill.name] : [];
    });
  }
  function modelMatches(value) {
    const typed = (/^\/model\s+(.*)$/i.exec(value)?.[1] ?? "").toLowerCase();
    return [
      { name: "default", hint: "", description: "Your default model profile" },
      ...modelProfiles.map((profile) => ({ name: profile.name, hint: profile.model, description: "" }))
    ].filter((item) => item.name.toLowerCase().startsWith(typed)).slice(0, 12);
  }
  function hideSlash() {
    el("slash-menu").hidden = true;
    slashItems = [];
  }
  function renderSlash() {
    const value = textarea("objective").value;
    if (!value.startsWith("/")) {
      hideSlash();
      return;
    }
    ensureLists().then(() => {
      if (textarea("objective").value === value)
        drawSlash(value);
    });
    drawSlash(value);
  }
  function drawSlash(value) {
    slashModels = /^\/model\s/i.test(value) && !/\n/.test(value);
    slashItems = slashModels ? modelMatches(value) : slashMatches(value, skillNames);
    const menu = el("slash-menu");
    if (!slashItems.length) {
      hideSlash();
      return;
    }
    slashIndex = Math.min(slashIndex, slashItems.length - 1);
    menu.replaceChildren(...slashItems.map((item, index) => {
      const row = make("li", "slash-item");
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(index === slashIndex));
      row.append(make("span", "slash-name", slashModels ? item.name : `/${item.name}`));
      if (item.hint)
        row.append(make("span", "slash-hint", item.hint));
      if (item.description)
        row.append(make("span", "slash-desc", item.description));
      row.addEventListener("mousedown", (event) => {
        event.preventDefault();
        chooseSlash(index);
      });
      return row;
    }));
    menu.hidden = false;
  }
  function chooseSlash(index) {
    const item = slashItems[index];
    if (!item)
      return;
    const field = textarea("objective");
    hideSlash();
    if (slashModels || ["condense", "fork"].includes(item.name)) {
      field.value = slashModels ? `/model ${item.name}` : `/${item.name}`;
      const form = el("task-form");
      if (typeof form.requestSubmit === "function")
        form.requestSubmit();
      else
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      return;
    }
    field.value = `/${item.name} `;
    composerDrafts.update(field.value);
    field.focus?.();
    syncSend();
    if (item.name === "model")
      renderSlash();
  }
  function slashKey(event) {
    if (el("slash-menu").hidden || !slashItems.length)
      return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      slashIndex = (slashIndex + (event.key === "ArrowDown" ? 1 : slashItems.length - 1)) % slashItems.length;
      drawSlash(textarea("objective").value);
    } else if (event.key === "Enter" && !event.shiftKey || event.key === "Tab")
      chooseSlash(slashIndex);
    else if (event.key === "Escape")
      hideSlash();
    else
      return false;
    event.preventDefault();
    return true;
  }
  async function runSlash(name, arg) {
    if (name === "btw") {
      if (!arg) {
        message("Ask your question after /btw.", true);
        return false;
      }
      await askSide(arg);
      return true;
    }
    if (name === "goal") {
      const goal = parseGoal(arg);
      if (select("mode").value !== "agent") {
        select("mode").value = "agent";
        syncMode();
      }
      return { text: goal.goal, turns: goal.turns };
    }
    if (name === "model") {
      await ensureLists(true);
      if (!arg) {
        textarea("objective").value = "/model ";
        drawSlash("/model ");
        return false;
      }
      if (arg.toLowerCase() === "default")
        modelOverride = null;
      else {
        const wanted = arg.toLowerCase();
        const found = modelProfiles.find((profile) => profile.name.toLowerCase() === wanted) ?? modelProfiles.find((profile) => profile.name.toLowerCase().startsWith(wanted));
        if (!found) {
          message(`No model profile is called “${arg}”. Add one in Settings → Models.`, true);
          return false;
        }
        modelOverride = { id: found.id, name: found.name };
      }
      message(modelOverride ? `Using ${modelOverride.name} for your next messages in this tab.` : "Back to your default model.");
      await loadModel().catch(() => {
        return;
      });
      return true;
    }
    if (name === "condense") {
      await condenseNow();
      return true;
    }
    if (name === "fork") {
      if (!selected) {
        message("Open a conversation to fork it.", true);
        return false;
      }
      await forkTask(selected);
      return true;
    }
    if (skillNames.includes(name))
      return { text: `Use the “${name}” skill for this request.${arg ? `

` + arg : ""}` };
    message(`Unknown command /${name}. Type / to see the commands.`, true);
    return false;
  }
  function needsSharing(what) {
    if (!selected)
      throw new Error(`Open a conversation first; ${what} works from its history.`);
    if (!input("external").checked)
      throw new Error(`Turn on “Share code with model” to ${what}.`);
  }
  async function askSide(question) {
    needsSharing("ask a side question");
    const task = selected;
    el("side-answer").hidden = false;
    el("side-question").textContent = question;
    el("side-body").replaceChildren(make("p", "hint", "Thinking…"));
    try {
      const reply = await api(`/api/tasks/${encodeURIComponent(task)}/ask`, { question, approve_external: true });
      if (task === selected)
        el("side-body").replaceChildren(richText(reply.answer));
    } catch (error) {
      el("side-answer").hidden = true;
      throw error;
    }
  }
  async function condenseNow() {
    needsSharing("condense the conversation");
    const task = selected;
    message("Condensing older turns…");
    await api(`/api/tasks/${encodeURIComponent(task)}/condense`, { approve_external: true });
    message("Condensed. Older turns now travel as a summary.");
    if (task === selected)
      await observe(true);
  }
  async function branchFromMessage(task, seq) {
    const reply = await api(`/api/tasks/${encodeURIComponent(task)}/fork`, { message_seq: seq });
    await loadHistory();
    selectTask(reply.id);
    await observe();
    message("Branched into a new conversation. The original is unchanged.");
  }
  async function forkTask(task, upto) {
    const reply = await api(`/api/tasks/${encodeURIComponent(task)}/fork`, upto === undefined ? {} : { upto_seq: upto });
    await loadHistory();
    selectTask(reply.id);
    await observe();
    message("Forked into a new conversation. The original is unchanged.");
  }
  async function resumeTask() {
    if (!selected || !taskRequest)
      return;
    const r = taskRequest, task = selected;
    const chosen = modelOverride?.id ?? r.model_profile;
    const body = {
      verification: r.verification,
      approve_external: r.approve_external === true,
      approve_execution: r.approve_execution === true,
      ...r.approve_browser ? { approve_browser: true } : {},
      ...r.agent_profile ? { agent_profile: r.agent_profile } : {},
      ...r.confirmation ? { confirmation: r.confirmation } : {},
      ...chosen ? { model_profile: chosen } : {}
    };
    try {
      await api(`/api/tasks/${encodeURIComponent(task)}/resume`, body);
      watching = true;
      await observe(true);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Resume failed";
      if (/no saved agent state/i.test(text)) {
        await sendMessage("Continue where you left off. Finish the original request.");
        return;
      }
      message(text, true);
    }
  }
  var GOAL_LABELS = { achieved: "Goal achieved", capped: "Goal stopped at its turn limit", stopped: "Goal stopped", error: "Goal check failed" };
  function renderRunState() {
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
  async function readBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0;i < bytes.length; i += 32768)
      binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    return btoa(binary);
  }
  var TEXT_TYPES = { md: "text/markdown", py: "text/x-python", ts: "text/plain", tsx: "text/plain", rs: "text/plain", toml: "text/plain", yml: "text/plain", yaml: "text/plain", log: "text/plain", sh: "text/plain", sql: "text/plain", go: "text/plain" };
  async function uploadFiles(files) {
    for (const file of files) {
      if (attached.length >= 10) {
        message("Attach up to 10 files per message.", true);
        break;
      }
      if (file.size > 3 * 1024 * 1024) {
        message(`${file.name} is larger than 3 MB.`, true);
        continue;
      }
      const extension = (file.name.split(".").pop() ?? "").toLowerCase();
      const type = file.type || TEXT_TYPES[extension] || "text/plain";
      try {
        const data = await readBase64(file);
        const meta = await api("/api/attachments", { name: file.name || "pasted.png", type, data });
        attached.push({ ...meta, preview: meta.type.startsWith("image/") ? `data:${meta.type};base64,${data}` : "" });
      } catch (error) {
        message(error instanceof Error ? `${file.name}: ${error.message}` : "Upload failed", true);
      }
    }
    renderTray();
    syncSend();
    autosize();
  }
  function renderTray() {
    const tray = el("attachment-tray");
    tray.hidden = !attached.length;
    tray.replaceChildren(...attached.map((file) => {
      const chip = make("span", file.preview ? "tray-item tray-image" : "tray-item");
      if (file.preview) {
        const image = make("img");
        image.src = file.preview;
        image.alt = "";
        chip.append(image);
      } else
        chip.append(icon("file"));
      chip.append(make("span", "tray-name", file.name));
      const remove = make("button", "ghost icon tray-remove");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${file.name}`);
      remove.title = "Remove";
      remove.append(icon("x"));
      remove.onclick = () => {
        attached = attached.filter((item) => item.id !== file.id);
        renderTray();
        syncSend();
        autosize();
      };
      chip.append(remove);
      return chip;
    }));
  }
  function clearAttachments() {
    attached = [];
    renderTray();
    syncSend();
  }
  el("attach").addEventListener("click", () => input("attach-input").click());
  input("attach-input").addEventListener("change", () => {
    const files = [...input("attach-input").files ?? []];
    input("attach-input").value = "";
    uploadFiles(files);
  });
  textarea("objective").addEventListener("paste", (event) => {
    const files = [...event.clipboardData?.files ?? []];
    if (!files.length)
      return;
    event.preventDefault();
    uploadFiles(files);
  });
  textarea("objective").addEventListener("blur", () => setTimeout(hideSlash, 120));
  el("task-form").addEventListener("dragover", (event) => {
    if (!event.dataTransfer?.types?.includes("Files"))
      return;
    event.preventDefault();
    setClass(el("task-form"), "dropping", true);
  });
  el("task-form").addEventListener("dragleave", () => setClass(el("task-form"), "dropping", false));
  el("task-form").addEventListener("drop", (event) => {
    setClass(el("task-form"), "dropping", false);
    const files = [...event.dataTransfer?.files ?? []];
    if (!files.length)
      return;
    event.preventDefault();
    uploadFiles(files);
  });
  el("context-meter").addEventListener("click", () => {
    condenseNow().catch((error) => message(error instanceof Error ? error.message : "Condense failed", true));
  });
  el("side-close").addEventListener("click", () => {
    el("side-answer").hidden = true;
  });
  button("pause").onclick = async () => {
    if (!selected)
      return;
    try {
      const data = await api(`/api/tasks/${encodeURIComponent(selected)}/pause`, {});
      message(data.requested ? "Pausing after the current step…" : "This run has already stopped.");
    } catch (error) {
      message(error instanceof Error ? error.message : "Pause failed", true);
    }
  };
  for (const [id, action] of [["goal-stop", "stop"], ["goal-resume", "resume"]]) {
    el(id).addEventListener("click", async () => {
      if (!selected)
        return;
      try {
        await api(`/api/tasks/${encodeURIComponent(selected)}/goal/${action}`, {});
        await observe(true);
      } catch (error) {
        message(error instanceof Error ? error.message : "Goal update failed", true);
      }
    });
  }
  async function loadAppSettings() {
    const settings = await api("/api/settings/app");
    appSettings = settings;
    select("confirmation-policy").value = settings.confirmation;
    input("condenser").checked = settings.condenser;
    input("sounds").checked = settings.sounds === true;
    input("git-name").value = settings.git_name ?? "";
    input("git-email").value = settings.git_email ?? "";
  }
  async function saveAppSetting(change) {
    try {
      appSettings = await request("/api/settings/app", "PATCH", change);
      message("Saved.");
    } catch (error) {
      message(error instanceof Error ? error.message : "Could not save", true);
      loadAppSettings().catch(() => {
        return;
      });
    }
  }
  select("confirmation-policy").addEventListener("change", () => {
    saveAppSetting({ confirmation: select("confirmation-policy").value });
  });
  input("condenser").addEventListener("change", () => {
    saveAppSetting({ condenser: input("condenser").checked });
  });
  function treeRows(paths, changed) {
    return fileTree(paths, expandedDirs).map((row) => {
      const item = make("button", row.dir ? "tree-row tree-dir" : "tree-row");
      item.type = "button";
      item.title = row.path;
      item.style?.setProperty?.("--depth", String(row.depth));
      item.setAttribute("role", "treeitem");
      if (row.dir) {
        item.setAttribute("aria-expanded", String(row.open));
        const chevron = icon("chevron");
        chevron.setAttribute("class", "i tree-chevron");
        item.append(chevron, icon("folder"), make("span", "tree-name", row.name));
        item.onclick = () => {
          if (expandedDirs.has(row.path))
            expandedDirs.delete(row.path);
          else
            expandedDirs.add(row.path);
          renderFileList();
        };
      } else {
        item.setAttribute("aria-selected", String(row.path === openFile));
        item.append(icon("file"), make("span", "tree-name", row.name));
        if (changed.has(row.path))
          item.dataset.changed = "true";
        item.onclick = () => {
          showFile(row.path, changed.has(row.path) && selected ? "candidate" : "workspace");
        };
      }
      return item;
    });
  }
  function renderFileTabs() {
    const strip = el("file-tabs");
    strip.hidden = !openTabs.length;
    strip.replaceChildren(...openTabs.map((path) => {
      const tab = make("span", "file-tab");
      tab.setAttribute("aria-selected", String(path === openFile));
      const open = make("button", "file-tab-name", path.split("/").pop() ?? path);
      open.type = "button";
      open.title = path;
      open.setAttribute("role", "tab");
      open.onclick = () => {
        showFile(path, changedPaths().has(path) && selected ? "candidate" : "workspace");
      };
      const close = make("button", "ghost icon file-tab-close");
      close.type = "button";
      close.setAttribute("aria-label", `Close ${path}`);
      close.append(icon("x"));
      close.onclick = () => {
        openTabs = openTabs.filter((item) => item !== path);
        if (path === openFile) {
          const next = openTabs[openTabs.length - 1];
          if (next)
            showFile(next, changedPaths().has(next) && selected ? "candidate" : "workspace");
          else {
            openFile = null;
            fileText = "";
            el("file-name").textContent = "Choose a file";
            el("file-view").replaceChildren();
            el("copy-file").hidden = true;
            el("file-preview").hidden = true;
            filePreview = false;
            showFilePreview();
            renderFileList();
          }
        }
        renderFileTabs();
      };
      tab.append(open, close);
      return tab;
    }));
  }
  function showFilePreview() {
    el("file-view").hidden = filePreview;
    el("file-rendered").hidden = !filePreview;
    el("file-preview").setAttribute("aria-pressed", String(filePreview));
    if (filePreview)
      el("file-rendered").replaceChildren(richText(fileText));
  }
  el("file-preview").addEventListener("click", () => {
    filePreview = !filePreview;
    showFilePreview();
  });
  async function openFileFromChat(path, line) {
    openCanvas("files");
    await showFile(path, changedPaths().has(path) && selected ? "candidate" : "workspace");
    if (!line)
      return;
    for (const span of el("file-view").querySelectorAll(".ln")) {
      const hit = span.dataset.n === String(line);
      setClass(span, "ln-hit", hit);
      if (hit)
        span.scrollIntoView?.({ block: "center" });
    }
  }
  async function refreshFiles() {
    await project().catch(() => {
      return;
    });
    if (canvasTab !== "files" || el("canvas").hidden)
      return;
    renderFileList();
    if (openFile)
      await showFile(openFile, fileVersion);
  }
  var canvasGrip = el("canvas-resize");
  var canvasDrag = null;
  function setCanvasWidth(width) {
    const canvas = el("canvas");
    if (width === null) {
      canvas.style?.removeProperty?.("width");
      storageSet("dikdik.canvas.width", "");
      return;
    }
    const clamped = Math.round(Math.max(320, Math.min(width, (globalThis.innerWidth || 1440) * 0.72)));
    canvas.style?.setProperty?.("width", `${clamped}px`);
    storageSet("dikdik.canvas.width", String(clamped));
  }
  {
    const saved = Number(storageGet("dikdik.canvas.width"));
    if (saved > 0)
      setCanvasWidth(saved);
  }
  canvasGrip.addEventListener("pointerdown", (event) => {
    canvasDrag = event.pointerId;
    canvasGrip.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  canvasGrip.addEventListener("pointermove", (event) => {
    if (canvasDrag === event.pointerId)
      setCanvasWidth((globalThis.innerWidth || 1440) - event.clientX);
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
    canvasGrip.addEventListener(name, () => {
      canvasDrag = null;
    });
  canvasGrip.addEventListener("keydown", (event) => {
    const width = el("canvas").getBoundingClientRect?.().width ?? 560;
    if (event.key === "ArrowLeft")
      setCanvasWidth(width + 32);
    else if (event.key === "ArrowRight")
      setCanvasWidth(width - 32);
    else if (event.key === "Home")
      setCanvasWidth(null);
    else
      return;
    event.preventDefault();
  });
  canvasGrip.addEventListener("dblclick", () => setCanvasWidth(null));
  var PANEL_TITLES = { changes: "Changes", files: "Files", terminal: "Terminal", browser: "Browser", planner: "Planner", usage: "Usage", git: "Git" };
  var paletteItems = [];
  var paletteIndex = 0;
  function paletteCommands() {
    const task = selected;
    const actions = [{ label: "New task", hint: "Ctrl ⇧ O", icon: "plus", keywords: "conversation start", run: newTask }];
    if (task) {
      actions.push({ label: "Rename this conversation", icon: "pencil", run: () => startRename(task) }, { label: "Folder and tags…", icon: "tag", keywords: "organise move label", run: () => openMeta(task) }, { label: "Fork this conversation", icon: "branch", keywords: "copy branch", run: () => {
        forkTask(task).catch((error) => message(String(error), true));
      } }, { label: "Condense the conversation", icon: "list", keywords: "summarise compact context", run: () => {
        condenseNow().catch((error) => message(error instanceof Error ? error.message : String(error), true));
      } }, { label: "Export transcript (Markdown)", icon: "download", run: () => {
        exportTask(task);
      } }, { label: "Download conversation data (.zip)", icon: "download", run: () => {
        downloadTask(task);
      } });
      if (!el("pause").hidden)
        actions.push({ label: "Pause after this step", icon: "pause", run: () => button("pause").click() });
    }
    actions.push({ label: "Skills, hooks and tools", icon: "info", keywords: "agent capabilities subagents", run: () => {
      showAgentInfo();
    } }, ...Object.entries(PANEL_TITLES).map(([tab, title]) => ({ label: `Open ${title} panel`, icon: "sidebar", keywords: "canvas tab", run: () => openCanvas(tab) })), ...Object.entries(PANE_TITLES).map(([pane, title]) => ({ label: `Settings: ${title}`, icon: "gear", keywords: "preferences options configure", run: () => openSettings(pane) })), { label: el("workspace").className.includes("sidebar-collapsed") ? "Show sidebar" : "Hide sidebar", icon: "sidebar", run: () => button("toggle-sidebar").click() }, { label: showArchived ? "Hide archived conversations" : "Show archived conversations", icon: "archive", run: () => button("show-archived").click() }, ...["system", "light", "dark"].map((theme) => ({ label: `Appearance: ${theme[0].toUpperCase()}${theme.slice(1)}`, icon: "sun", keywords: "theme dark mode", run: () => {
      themeSelect.value = theme;
      themeSelect.dispatchEvent(new Event("change"));
    } })));
    const conversations = lastTasks.filter((item) => item.id !== task).map((item) => ({
      label: conversationLabel(item),
      keywords: `${item.folder ?? ""} ${(item.tags ?? []).join(" ")} conversation`,
      hint: item.folder || (item.archived ? "Archived" : ""),
      icon: "list",
      run: () => {
        selectTask(item.id);
        observe();
      }
    }));
    return [...actions, ...conversations];
  }
  function renderPalette() {
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
      if (item.hint)
        row.append(make("span", "palette-hint", item.hint));
      row.addEventListener("mousedown", (event) => {
        event.preventDefault();
        runPalette(index);
      });
      return row;
    }));
    if (!paletteItems.length)
      list.replaceChildren(make("li", "palette-empty", "Nothing matches"));
    list.children[paletteIndex]?.scrollIntoView?.({ block: "nearest" });
  }
  function runPalette(index) {
    const item = paletteItems[index];
    if (!item)
      return;
    el("palette").close();
    item.run();
  }
  function togglePalette() {
    const dialog = el("palette");
    if (dialog.open) {
      dialog.close();
      return;
    }
    if (typeof dialog.showModal !== "function")
      return;
    input("palette-input").value = "";
    paletteIndex = 0;
    renderPalette();
    dialog.showModal();
    input("palette-input").focus();
  }
  input("palette-input").addEventListener("input", () => {
    paletteIndex = 0;
    renderPalette();
  });
  input("palette-input").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!paletteItems.length)
        return;
      paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : paletteItems.length - 1)) % paletteItems.length;
      renderPalette();
    } else if (event.key === "Enter") {
      event.preventDefault();
      runPalette(paletteIndex);
    }
  });
  el("palette").addEventListener("click", (event) => {
    if (event.target === el("palette"))
      el("palette").close();
  });
  el("open-palette").addEventListener("click", togglePalette);
  var metaTask = null;
  function openMeta(task) {
    const info = lastTasks.find((item) => item.id === task);
    const dialog = el("meta-dialog");
    metaTask = task;
    input("meta-folder").value = info?.folder ?? "";
    input("meta-tags").value = (info?.tags ?? []).join(", ");
    el("meta-error").hidden = true;
    el("folder-options").replaceChildren(...folderOptions.map((name) => {
      const option = make("option");
      option.value = name;
      return option;
    }));
    el("meta-title").textContent = `Folder and tags · ${info ? conversationLabel(info) : "conversation"}`;
    if (typeof dialog.showModal === "function")
      dialog.showModal();
    input("meta-folder").focus();
  }
  el("meta-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!metaTask)
      return;
    try {
      const tags = parseTags(input("meta-tags").value);
      await patchTask(metaTask, { folder: input("meta-folder").value.trim() || null, tags });
      el("meta-dialog").close();
      message("Saved.");
    } catch (error) {
      el("meta-error").textContent = error instanceof Error ? error.message : "Could not save";
      el("meta-error").hidden = false;
    }
  });
  el("meta-cancel").addEventListener("click", () => el("meta-dialog").close());
  select("task-sort").addEventListener("change", () => {
    storageSet("dikdik.list.sort", select("task-sort").value);
    renderHistory();
  });
  select("task-since").addEventListener("change", () => {
    storageSet("dikdik.list.since", select("task-since").value);
    renderHistory();
  });
  select("task-sort").value = storageGet("dikdik.list.sort") || "recent";
  select("task-since").value = storageGet("dikdik.list.since") || "0";
  el("delete-archived").addEventListener("click", async () => {
    const archived = lastTasks.filter((task) => task.archived && task.id !== selected);
    if (!deletingArchived) {
      deletingArchived = true;
      el("delete-archived").textContent = `Click again to delete ${archived.length} for good`;
      setTimeout(() => {
        deletingArchived = false;
        renderHistory();
      }, 4000);
      return;
    }
    deletingArchived = false;
    let failed = 0;
    for (const task of archived) {
      try {
        await request(`/api/tasks/${encodeURIComponent(task.id)}`, "DELETE");
      } catch {
        failed++;
      }
    }
    await loadHistory();
    message(failed ? `Deleted ${archived.length - failed}; ${failed} could not be deleted.` : `Deleted ${archived.length} archived conversations.`, failed > 0);
  });
  async function downloadTask(task) {
    const headers = token ? { Authorization: "Bearer " + token } : {};
    const response = await fetch(`/api/tasks/${encodeURIComponent(task)}/download`, { headers, credentials: "same-origin" });
    if (!response.ok) {
      message("Could not download this conversation", true);
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    const info = lastTasks.find((item) => item.id === task);
    const link = make("a");
    link.href = url;
    link.download = `dikdik-${(info ? conversationLabel(info) : "conversation").replace(/[^\w-]+/g, "-").slice(0, 40)}-${task.slice(0, 8)}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function showAgentInfo() {
    const info = await api("/api/agent-info");
    const section = (title, rows, empty) => {
      const box = make("section", "info-section");
      box.append(make("h4", "", title));
      if (!rows.length)
        box.append(make("p", "hint", empty));
      const list = make("dl", "info-list");
      for (const [name, text] of rows)
        list.append(make("dt", "", name), make("dd", "", text));
      if (rows.length)
        box.append(list);
      return box;
    };
    el("info-body").replaceChildren(section("Tools", info.tools.map((tool) => [tool.name, tool.description]), "No tools."), section("Skills", info.skills.map((skill) => [skill.name, `${skill.description || "No description"} · ${skill.scope}${skill.enabled ? "" : " · off"}`]), "No skills. Add them in Settings → Skills."), section("Subagents", info.subagents.map((agent) => [agent.name, `${agent.description} · ${agent.source}`]), "Only the built-in explorer."), section("Hooks", info.hooks.map((hook) => [`${hook.tool} · ${hook.pattern}`, hook.message || "Blocked"]), "No hooks. Add block rules to .dikdik/hooks.json in the workspace."));
    const dialog = el("info-dialog");
    if (typeof dialog.showModal === "function")
      dialog.showModal();
  }
  el("info-close").addEventListener("click", () => el("info-dialog").close());
  function fillComposer(text) {
    textarea("objective").value = text;
    composerDrafts.update(text);
    autosize();
    textarea("objective").focus?.();
  }
  function renderOnboarding() {
    const steps = [
      { done: modelReady, label: "Set up a model", detail: "Add a model profile and its API key, or a local server.", action: "Open Models", run: () => openSettings("models") },
      { done: lastTasks.length > 0, label: "Say hello", detail: "Ask Dikdik about this project.", action: "Try it", run: () => fillComposer("Hello! Give me a quick tour of this project: what it does and how it is organised.") },
      { done: storageGet("dikdik.onboarding.applied") === "1", label: "Review and apply a change", detail: "Let Dikdik fix something, read the diff, then Apply.", action: "Try it", run: () => fillComposer("Find one small bug or rough edge in this project and fix it.") }
    ];
    const box = el("onboarding");
    box.hidden = storageGet("dikdik.onboarding.dismissed") === "1" || steps.every((step) => step.done);
    el("onboarding-steps").replaceChildren(...steps.map((step) => {
      const li = make("li", step.done ? "onb-step done" : "onb-step");
      const mark = make("span", "onb-mark");
      if (step.done)
        mark.append(icon("check"));
      const text = make("span", "onb-text");
      text.append(make("strong", "", step.label), make("span", "", step.detail));
      li.append(mark, text);
      if (!step.done) {
        const go = make("button", "small", step.action);
        go.type = "button";
        go.onclick = step.run;
        li.append(go);
      }
      return li;
    }));
  }
  el("onboarding-dismiss").addEventListener("click", () => {
    storageSet("dikdik.onboarding.dismissed", "1");
    renderOnboarding();
  });
  function syncModelBanner() {
    const needsModel = parseMode(select("mode").value) !== "offline";
    el("model-banner").hidden = modelReady || !needsModel;
  }
  el("model-banner-setup").addEventListener("click", () => openSettings("models"));
  function settingsHash(pane) {
    const loc = globalThis.location;
    if (!loc || typeof globalThis.history?.replaceState !== "function")
      return;
    const wanted = pane ? `#settings/${pane}` : "";
    if (loc.hash !== wanted && (pane || loc.hash.startsWith("#settings")))
      globalThis.history.replaceState(null, "", loc.pathname + loc.search + wanted);
  }
  function routeSettings() {
    const match = /^#settings(?:\/([\w-]*))?$/.exec(globalThis.location?.hash ?? "");
    if (!match)
      return;
    const pane = match[1] ?? "";
    if (pane && !(pane in PANE_TITLES))
      message(`There is no “${pane}” settings page; showing General.`, true);
    openSettings(pane in PANE_TITLES ? pane : "general");
  }
  el("git-identity").addEventListener("submit", (event) => {
    event.preventDefault();
    saveAppSetting({ git_name: input("git-name").value.trim(), git_email: input("git-email").value.trim() });
  });
  input("sounds").addEventListener("change", () => {
    saveAppSetting({ sounds: input("sounds").checked });
  });
  function chime() {
    if (!appSettings?.sounds || globalThis.document?.hasFocus?.() && !document.hidden)
      return;
    const Context = globalThis.AudioContext;
    if (!Context)
      return;
    const audio = new Context;
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
    setTimeout(() => {
      audio.close();
    }, 1000);
  }
  function resetStop() {
    if (stopArmed !== undefined)
      clearTimeout(stopArmed);
    stopArmed = undefined;
    el("cancel").replaceChildren(icon("stop"), document.createTextNode("Stop"));
    setClass(el("cancel"), "confirming", false);
  }
  var paneDeps = {
    request: (path, method, body) => request(path, method, body),
    make,
    message,
    changed: () => {
      loadModel().catch(() => {
        return;
      });
      loadExposedSecrets();
      ensureLists(true);
    }
  };
  function secretRefsField(text) {
    const value = text.trim();
    if (!value)
      return null;
    if (value.toLowerCase() === "none")
      return [];
    return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
  }
  async function loadExposedSecrets() {
    try {
      const keys = await api("/api/secrets");
      exposedSecrets = keys.filter((key) => key.env).length;
    } catch {
      exposedSecrets = 0;
    }
    syncMode();
  }
  function closeModelMenu() {
    el("model-menu").hidden = true;
  }
  async function openModelMenu() {
    const menu = el("model-menu");
    if (!menu.hidden) {
      closeModelMenu();
      return;
    }
    let profiles = [];
    try {
      profiles = await api("/api/profiles");
    } catch {}
    const pick = (choice) => {
      modelOverride = choice;
      closeModelMenu();
      message(choice ? `Using ${choice.name} for your next messages in this tab.` : "Back to the default model.");
      loadModel().catch(() => {
        return;
      });
    };
    const item = (label, detail, checked, run) => {
      const node = make("button", "model-item");
      node.type = "button";
      node.setAttribute("role", "menuitemradio");
      node.setAttribute("aria-checked", String(checked));
      const mark = make("span", "model-check");
      if (checked)
        mark.append(icon("check"));
      node.append(mark, make("span", "model-name", label));
      if (detail)
        node.append(make("span", "model-detail", detail));
      node.onclick = run;
      return node;
    };
    const fallback = profiles.find((profile) => profile.is_default);
    const manage = make("button");
    manage.type = "button";
    manage.setAttribute("role", "menuitem");
    manage.append(icon("gear"), make("span", "", "Manage models…"));
    manage.onclick = () => {
      closeModelMenu();
      openSettings("models");
    };
    menu.replaceChildren(item("Default", fallback ? `${fallback.name} · ${fallback.model}` : "from settings", modelOverride === null, () => pick(null)), ...profiles.map((profile) => item(profile.name, profile.model, modelOverride?.id === profile.id, () => pick({ id: profile.id, name: profile.name }))), make("hr"), manage);
    const box = el("model-pill").getBoundingClientRect();
    menu.hidden = false;
    const { offsetWidth: width, offsetHeight: height } = menu;
    menu.style.left = `${Math.max(8, Math.min(box.left, innerWidth - width - 8))}px`;
    menu.style.top = `${box.top - height - 6 > 8 ? box.top - height - 6 : box.bottom + 6}px`;
    menu.querySelector("[aria-checked=true]")?.focus();
  }
  if (typeof document.addEventListener === "function") {
    document.addEventListener("click", (event) => {
      const menu = el("model-menu");
      if (!menu.hidden && event.target instanceof Node && !menu.contains(event.target))
        closeModelMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !el("model-menu").hidden)
        closeModelMenu();
    });
  }
  function openLightbox(src, name) {
    const dialog = el("lightbox");
    const image = el("lightbox-image");
    image.src = src;
    image.alt = name;
    el("lightbox-name").textContent = name;
    if (typeof dialog.showModal === "function")
      dialog.showModal();
  }
  el("lightbox-close").addEventListener("click", () => el("lightbox").close());
  el("lightbox").addEventListener("click", (event) => {
    if (event.target === el("lightbox"))
      el("lightbox").close();
  });
  function approvalShortcut(event) {
    if (!(event.ctrlKey || event.metaKey) || !confirmDecisions.size)
      return false;
    const allow = event.key === "Enter" && !event.shiftKey, reject = event.key === "Backspace" && event.shiftKey;
    if (!allow && !reject)
      return false;
    const newest = [...confirmDecisions.values()].pop();
    event.preventDefault();
    event.stopPropagation();
    newest?.(allow);
    return true;
  }
  if (typeof document.addEventListener === "function")
    document.addEventListener("keydown", approvalShortcut, true);
  var stopArmed;
  button("cancel").onclick = async () => {
    if (!selected)
      return;
    if (stopArmed === undefined) {
      el("cancel").replaceChildren(icon("stop"), document.createTextNode("Stop run?"));
      setClass(el("cancel"), "confirming", true);
      stopArmed = setTimeout(resetStop, 3000);
      return;
    }
    resetStop();
    try {
      const data = await api(`/api/tasks/${selected}/interrupt`, {});
      message(data.requested ? "Stopping the current action…" : "Task has already stopped.");
      await observe();
    } catch (error) {
      message(error instanceof Error ? error.message : "Cancellation failed", true);
    }
  };
  button("refresh").onclick = async () => {
    try {
      await Promise.all([project(), loadHistory()]);
      message(`Re-scanned ${projectName || "the workspace"}.`);
    } catch (error) {
      message(error instanceof Error ? error.message : "Refresh failed", true);
    }
  };
  setInterval(() => {
    observe();
  }, 1000);
  globalThis.addEventListener?.("hashchange", () => {
    if (!connected)
      boot();
    else
      routeSettings();
  });
  boot();
})();
