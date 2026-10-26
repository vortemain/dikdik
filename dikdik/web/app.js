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
    return ["completed", "failed", "blocked", "cancelled", "interrupted", "draft"].includes(state);
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
      ...["agent", "plan"].includes(input.mode) && input.agentProfile ? { agent_profile: input.agentProfile } : {}
    };
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
      case "completed":
      case "failed":
      case "blocked":
      case "cancelled":
      case "interrupted":
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
  function conversationLabel(task) {
    return textOf(task.title, 200) || textOf(task.objective, 60) || task.id.slice(0, 8);
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
  var PROFILE_PROVIDERS = ["openai", "anthropic", "azure", "local"];
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
  function deleteProfileUrl(id, confirmDefault) {
    return apiUrl("/api/profiles", id) + (confirmDefault ? "?confirm_default=1" : "");
  }
  var FIXED_API_ENDPOINTS = ["https://api.openai.com/v1", "https://api.anthropic.com/v1", "https://mojave.cloud/v1"];
  var LOOPBACK_ENDPOINT = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/;
  function validateEndpoint(value) {
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      throw new Error("Endpoint must be an absolute http(s) URL.");
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:")
      throw new Error("Endpoint must use http or https.");
    if (FIXED_API_ENDPOINTS.includes(value.replace(/\/+$/, "")))
      return value;
    if (!LOOPBACK_ENDPOINT.test(value))
      throw new Error("Endpoint must be a loopback URL or one of the known provider API bases.");
    return value;
  }
  function parseTimeoutSeconds(value) {
    if (value === undefined || value === "")
      return;
    const seconds = typeof value === "number" ? value : Number(String(value).trim());
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 600)
      throw new Error(M4_TEXT.timeoutRule);
    return seconds;
  }
  var SECRET_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
  function buildProfileCreate(input) {
    const name = input.name.trim();
    if (!name)
      throw new Error("Enter a profile name.");
    if (!PROFILE_PROVIDERS.includes(input.provider))
      throw new Error("Choose a supported provider.");
    const model = input.model.trim();
    if (!model)
      throw new Error("Enter a model name.");
    const endpoint = (input.endpoint ?? "").trim();
    if (endpoint)
      validateEndpoint(endpoint);
    const timeout_seconds = parseTimeoutSeconds(input.timeoutSeconds);
    const credential_secret_ref = (input.credentialSecretRef ?? "").trim();
    if (credential_secret_ref && !SECRET_REF_PATTERN.test(credential_secret_ref))
      throw new Error(M4_TEXT.secretRefRule);
    const body = { name, provider: input.provider, model, ...endpoint ? { endpoint } : {}, ...timeout_seconds !== undefined ? { timeout_seconds } : {}, ...credential_secret_ref ? { credential_secret_ref } : {} };
    return body;
  }
  function buildProfilePatch(input) {
    const patch = {};
    if (input.model !== undefined) {
      const model = input.model.trim();
      if (!model)
        throw new Error("Enter a model name to save.");
      patch.model = model;
    }
    if (input.endpoint !== undefined) {
      const endpoint = input.endpoint.trim();
      if (endpoint)
        validateEndpoint(endpoint);
      patch.endpoint = endpoint;
    }
    const timeout = parseTimeoutSeconds(input.timeoutSeconds);
    if (timeout !== undefined)
      patch.timeout_seconds = timeout;
    if (input.isDefault !== undefined)
      patch.is_default = input.isDefault;
    const ref = input.credentialSecretRef?.trim();
    if (ref) {
      if (!SECRET_REF_PATTERN.test(ref))
        throw new Error(M4_TEXT.secretRefRule);
      patch.credential_secret_ref = ref;
    }
    if (Object.keys(patch).length === 0)
      throw new Error("Nothing to update for this profile.");
    return patch;
  }
  function buildSecretInput(input) {
    const ref = input.ref.trim();
    if (!SECRET_REF_PATTERN.test(ref))
      throw new Error(M4_TEXT.secretRefRule);
    if (input.plaintext === "")
      throw new Error(M4_TEXT.plaintextRequired);
    if (input.plaintext.length > 64 * 1024)
      throw new Error("Secret exceeds the 64KB request limit.");
    return { ref, plaintext: input.plaintext };
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
  function parseProfiles(value) {
    if (!Array.isArray(value))
      throw new Error("Profile list response did not match the expected shape.");
    const profiles = [];
    for (const item of value) {
      if (!isRecord(item))
        continue;
      const id = safeText(item.id), name = safeText(item.name), model = safeText(item.model);
      const endpoint = item.endpoint === null ? "" : item.endpoint;
      const timeout = item.timeout_seconds === null ? 60 : item.timeout_seconds;
      const isDefault = item.is_default;
      if (!id || !name || !model || typeof endpoint !== "string" || typeof timeout !== "number" || !Number.isInteger(timeout) || typeof isDefault !== "boolean")
        continue;
      if (!PROFILE_PROVIDERS.includes(item.provider))
        continue;
      profiles.push({ id, name, provider: item.provider, model, endpoint, timeout_seconds: timeout, is_default: isDefault });
    }
    return profiles;
  }
  function parseSecretRefs(value) {
    if (!Array.isArray(value))
      throw new Error("Secret list response did not match the expected shape.");
    const refs = [];
    for (const item of value) {
      if (!isRecord(item))
        continue;
      const ref = safeText(item.ref);
      if (!ref)
        continue;
      const setAt = item.set_at;
      if (setAt !== null && typeof setAt !== "number" && typeof setAt !== "string")
        continue;
      refs.push({ ref, set_at: setAt });
    }
    return refs;
  }
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
  function parseCreatedId(value) {
    if (!isRecord(value) || typeof value.id !== "string" || value.id === "" || CONTROL_CHARS.test(value.id)) {
      throw new Error("The server did not return a usable id for the new record.");
    }
    return { id: value.id };
  }
  function parseSecretSet(value) {
    if (!isRecord(value) || typeof value.ref !== "string" || value.ref === "" || value.set !== true) {
      throw new Error("The server did not confirm the secret was stored; treat it as not set.");
    }
    return { ref: value.ref, set: true };
  }
  function sanitizeSecretError(error, plaintext) {
    const message = error instanceof Error && error.message ? error.message : M4_TEXT.genericFailure;
    return plaintext.length >= 4 && message.includes(plaintext) ? M4_TEXT.secretHiddenReply : message;
  }
  function displayTimestamp(value) {
    if (value === null || value === undefined || value === "")
      return "—";
    if (typeof value === "number" && Number.isFinite(value)) {
      try {
        return new Date(value < 1000000000000 ? value * 1000 : value).toISOString().replace("T", " ").replace(".000Z", " UTC");
      } catch {
        return String(value);
      }
    }
    if (typeof value === "string")
      return value;
    return String(value);
  }
  function m4el(create, tag, className = "", text = "") {
    const node = create(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }
  function m4focus(node) {
    if (typeof node.focus === "function")
      node.focus();
  }
  function m4input(create, container, kind, labelText, type, required) {
    const id = `m4-${kind}-${(m4FieldCounter++).toString(36)}`;
    const label = m4el(create, "label");
    label.setAttribute("for", id);
    label.textContent = labelText;
    const input = m4el(create, "input");
    input.type = type;
    input.required = required;
    input.setAttribute("id", id);
    container.append(label, input);
    return input;
  }
  function m4select(create, container, kind, labelText, options, selected) {
    const id = `m4-${kind}-${(m4FieldCounter++).toString(36)}`;
    const label = m4el(create, "label");
    label.setAttribute("for", id);
    label.textContent = labelText;
    const select = m4el(create, "select");
    select.setAttribute("id", id);
    const nodes = options.map((option) => {
      const node = m4el(create, "option", "", option);
      node.value = option;
      return node;
    });
    select.append(...nodes);
    select.value = selected;
    container.append(label, select);
    return select;
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
  var m4FieldCounter = 0;
  function profilesPanel({ root, create, request }) {
    let profiles = [];
    let confirmId = null, editId = null, loading = false;
    const note = m4el(create, "p", "hint", M4_TEXT.credentialsNote);
    const verify = m4el(create, "p", "hint m4-mono", `${M4_TEXT.contractStatus} — ${M4_TEXT.contractNote}`);
    const form = m4el(create, "form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitCreate();
    });
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
    statusLine.setAttribute("role", "status");
    statusLine.setAttribute("aria-live", "polite");
    const errorLine = m4el(create, "p", "error");
    errorLine.setAttribute("role", "alert");
    const list = m4el(create, "ol", "m4-list");
    root.append(note, verify, form, actions, statusLine, errorLine, list);
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
      showErrorReset();
    };
    const showErrorReset = () => {
      errorLine.hidden = true;
    };
    const refresh = async () => {
      if (loading)
        return;
      loading = true;
      refreshButton.disabled = true;
      clearFeedback();
      try {
        profiles = parseProfiles(await request("/api/profiles", "GET"));
        confirmId = null;
        editId = null;
        renderList();
        if (!profiles.length)
          showStatus(M4_TEXT.emptyProfiles);
      } catch (error) {
        showError(failMessage(error));
      } finally {
        loading = false;
        refreshButton.disabled = false;
      }
    };
    refreshButton.onclick = () => void refresh();
    const submitCreate = async () => {
      clearFeedback();
      try {
        const created = parseCreatedId(await request("/api/profiles", "POST", buildProfileCreate({
          name: nameField.value,
          provider: providerField.value,
          model: modelField.value,
          endpoint: endpointField.value,
          timeoutSeconds: timeoutField.value,
          credentialSecretRef: secretRefField.value
        })));
        nameField.value = "";
        modelField.value = "";
        endpointField.value = "";
        timeoutField.value = "";
        secretRefField.value = "";
        providerField.value = "openai";
        editId = null;
        confirmId = null;
        showStatus(`${M4_TEXT.profileCreated} (${created.id.slice(0, 8)})`);
        renderList();
        await refresh();
      } catch (error) {
        showError(failMessage(error));
      }
    };
    const saveEdit = async (id, model, endpoint, timeout, isDefault, secretRef) => {
      clearFeedback();
      try {
        const patch = buildProfilePatch({ model, endpoint, timeoutSeconds: timeout, isDefault, credentialSecretRef: secretRef });
        const local = profiles.find((profile) => profile.id === id);
        if (local) {
          if (patch.model !== undefined)
            local.model = patch.model;
          if (patch.endpoint !== undefined)
            local.endpoint = patch.endpoint;
          if (patch.timeout_seconds !== undefined)
            local.timeout_seconds = patch.timeout_seconds;
          if (patch.is_default !== undefined)
            local.is_default = patch.is_default;
        }
        editId = null;
        confirmId = null;
        renderList();
        await request(apiUrl("/api/profiles", id), "PATCH", patch);
        showStatus(M4_TEXT.profileSaved);
        await refresh();
      } catch (error) {
        editId = null;
        showError(failMessage(error));
        await refresh();
      }
    };
    const removeProfile = async (id, isDefault) => {
      clearFeedback();
      try {
        await request(deleteProfileUrl(id, isDefault), "DELETE");
        profiles = profiles.filter((profile) => profile.id !== id);
        confirmId = null;
        renderList();
        showStatus(M4_TEXT.profileDeleted);
        await refresh();
      } catch (error) {
        confirmId = null;
        renderList();
        showError(failMessage(error));
      }
    };
    function renderList() {
      const rows = [];
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
        editButton.onclick = () => {
          editId = profile.id;
          confirmId = null;
          renderList();
        };
        const deleteButton = m4el(create, "button", confirmId === profile.id ? "danger" : "secondary", confirmId === profile.id ? M4_TEXT.deleteConfirm : M4_TEXT.delete);
        deleteButton.type = "button";
        deleteButton.setAttribute("aria-pressed", String(confirmId === profile.id));
        deleteButton.onclick = () => {
          if (confirmId !== profile.id) {
            confirmId = profile.id;
            editId = null;
            renderList();
          } else
            removeProfile(profile.id, profile.is_default);
        };
        actionsRow.append(editButton, deleteButton);
        if (confirmId === profile.id) {
          const keepButton = m4el(create, "button", "secondary", M4_TEXT.keep);
          keepButton.type = "button";
          keepButton.onclick = () => {
            confirmId = null;
            renderList();
          };
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
          cancelButton.onclick = () => {
            editId = null;
            renderList();
          };
          editor.append(saveButton, cancelButton);
          editor.addEventListener("submit", (event) => {
            event.preventDefault();
            saveEdit(profile.id, modelEdit.value, endpointEdit.value, timeoutEdit.value, defaultToggle.checked, keyEdit.value);
          });
          row.append(editor);
          m4focus(modelEdit);
        }
        rows.push(row);
      }
      list.replaceChildren(...rows);
    }
    return { refresh };
  }
  function secretsPanel({ root, create, request }) {
    let refs = [];
    let confirmId = null, loading = false;
    const note = m4el(create, "p", "hint", M4_TEXT.secretsNote);
    const form = m4el(create, "form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitSet();
    });
    const refField = m4input(create, form, "secret-ref", "Ref (letters, digits, . _ -)", "text", true);
    const plaintextField = m4input(create, form, "secret-value", "Secret value", "password", true);
    plaintextField.setAttribute("autocomplete", "new-password");
    m4button(create, form, M4_TEXT.setSecret, "submit");
    const actions = m4el(create, "p", "row");
    const refreshButton = m4button(create, actions, M4_TEXT.refreshSecrets, "button");
    const statusLine = m4el(create, "p", "hint");
    statusLine.setAttribute("role", "status");
    statusLine.setAttribute("aria-live", "polite");
    const errorLine = m4el(create, "p", "error");
    errorLine.setAttribute("role", "alert");
    const list = m4el(create, "ol", "m4-list");
    root.append(note, form, actions, statusLine, errorLine, list);
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
        refs = parseSecretRefs(await request("/api/secrets", "GET"));
        confirmId = null;
        renderList();
        if (!refs.length)
          showStatus(M4_TEXT.emptySecrets);
      } catch (error) {
        showError(failMessage(error));
      } finally {
        loading = false;
        refreshButton.disabled = false;
      }
    };
    refreshButton.onclick = () => void refresh();
    const submitSet = async () => {
      clearFeedback();
      let input;
      try {
        input = buildSecretInput({ ref: refField.value, plaintext: plaintextField.value });
      } catch (error) {
        showError(failMessage(error));
        return;
      }
      const attemptedValue = plaintextField.value;
      plaintextField.value = "";
      try {
        const result = parseSecretSet(await request("/api/secrets", "POST", input));
        showStatus(`${M4_TEXT.secretSetConfirmation} (ref: ${result.ref}, set: ${String(result.set)})`);
        refField.value = "";
        await refresh();
      } catch (error) {
        showError(sanitizeSecretError(error, attemptedValue));
      }
    };
    const removeRef = async (ref) => {
      clearFeedback();
      try {
        await request(apiUrl("/api/secrets", ref), "DELETE");
        refs = refs.filter((entry) => entry.ref !== ref);
        confirmId = null;
        renderList();
        showStatus(M4_TEXT.secretDeleted);
      } catch (error) {
        confirmId = null;
        renderList();
        showError(failMessage(error));
      }
    };
    function renderList() {
      const rows = [];
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
          if (confirmId !== entry.ref) {
            confirmId = entry.ref;
            renderList();
          } else
            removeRef(entry.ref);
        };
        actionsRow.append(deleteButton);
        if (confirmId === entry.ref) {
          const keepButton = m4el(create, "button", "secondary", M4_TEXT.keep);
          keepButton.type = "button";
          keepButton.onclick = () => {
            confirmId = null;
            renderList();
          };
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
      } else
        nodes.push(token2.startsWith("`") ? make("code", "", token2.slice(1, -1)) : make("strong", "", token2.slice(2, -2)));
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
  var TOOL_LABELS = {
    read_file: ["file", "Read", "path"],
    list_files: ["list", "Listed files", "query"],
    search_text: ["search", "Searched", "text"],
    edit_file: ["pencil", "Edited", "path"],
    create_file: ["file-plus", "Created", "path"],
    run_tests: ["terminal", "Ran checks", ""],
    run_command: ["terminal", "Ran", "command"],
    finish: ["flag", "Finished", ""]
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
        return { icon: "terminal", title: `Output · ${toolName(msg)}`, arg: (msg.body ?? "").split(`
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
      case "recovery_cleanup":
        return { icon: "alert", title: "Recovered after restart", arg: detailField(details, "warning") };
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
      li.append(make("p", "turn-note", "No files changed in this turn."));
      return li;
    }
    const card = make("div", `result-card result-${state}`);
    const head = make("div", "result-head");
    const badge = make("span", "result-icon");
    badge.append(icon(state === "completed" ? "check" : state === "cancelled" ? "stop" : "alert"));
    const titles = { completed: "Candidate ready for review", failed: "Task failed", blocked: "Task blocked", cancelled: "Task cancelled", interrupted: "Task interrupted" };
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
      resume.onclick = () => {
        sendMessage("Continue where you left off. Finish the original request.");
      };
      actions.append(resume);
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
    } else if (msg.body)
      content.append(richText(msg.body));
    li.append(content);
    return li;
  }
  var rendered = new Set;
  var pendingCalls = [];
  var lastRole = "";
  function renderMessage(msg) {
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
      li2.append(make("div", "bubble", msg.body ?? ""));
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
  function renderMessageStream(messages) {
    const stream = el("message-stream"), thread = el("thread");
    const stick = nearBottom(thread);
    for (const msg of messages) {
      if (rendered.has(msg.id))
        continue;
      rendered.add(msg.id);
      const node = renderMessage(msg);
      if (node)
        stream.append(node);
    }
    if (stick)
      thread.scrollTop = thread.scrollHeight;
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
      el("file-name").textContent = path;
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
    button("start").disabled = submitting || !textarea("objective").value.trim();
  }
  function selectTask(id) {
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
    if (content !== null) {
      textarea("objective").value = content;
      autosize();
    }
    const labels = { loading: "Loading draft…", saving: "Saving draft…", saved: "Draft saved", unsaved: "Draft not saved. Keep this tab open.", conflict: "This draft changed in another tab. Choose which version to keep." };
    el("draft-status").textContent = labels[status.state];
    el("draft-status-row").hidden = status.state === "saved" && !textarea("objective").value;
    el("draft-use-saved").hidden = status.state !== "conflict";
    el("draft-keep-local").hidden = status.state !== "conflict";
    el("draft-retry").hidden = status.state !== "unsaved";
    el("draft-conflict").hidden = status.state !== "conflict";
    el("draft-remote").textContent = status.remote ?? "";
    syncSend();
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
    el("model-label").textContent = info.configured ? info.model ?? "Model" : "Set up a model";
    pill.title = info.configured ? info.source === "profile" ? `${info.model} · ${info.provider_name} (profile “${info.profile}”). Change it in Settings → Models.` : `${info.model} from server environment variables` : info.problem ?? "No model is set up";
  }
  async function project() {
    const data = await api("/api/project");
    projectName = data.name;
    projectFiles = data.files.map((file) => file.path);
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
  function renderHistory() {
    const query = (el("task-search").value ?? "").trim().toLowerCase();
    const visible = lastTasks.filter((task) => (showArchived || !task.archived || task.id === selected) && (!query || `${task.title ?? ""} ${task.objective ?? ""}`.toLowerCase().includes(query)));
    const pinned = visible.filter((task) => task.pinned), rest = visible.filter((task) => !task.pinned);
    const nodes = [];
    if (pinned.length)
      nodes.push(make("p", "list-head", "Pinned"), ...pinned.map(taskRow));
    if (rest.length)
      nodes.push(...pinned.length ? [make("p", "list-head", showArchived ? "All" : "Recent")] : [], ...rest.map(taskRow));
    if (!nodes.length)
      nodes.push(make("div", "conv-empty", query ? "No matching tasks" : "No tasks yet"));
    el("history").replaceChildren(...nodes);
    el("recent-head").hidden = pinned.length > 0;
    const archived = lastTasks.filter((task) => task.archived).length;
    el("show-archived").hidden = archived === 0;
    el("show-archived").textContent = showArchived ? "Hide archived" : `Show archived (${archived})`;
  }
  async function loadHistory() {
    lastTasks = await api("/api/tasks");
    renderHistory();
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
      if (record.kind === "running" || isTerminal(record.kind)) {
        setState(record.kind);
        selectedState = record.kind;
        renderPlanner();
        loadHistory();
      }
      if (record.kind === "running") {
        el("working").hidden = false;
        el("working-label").textContent = "Working";
      }
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
        models: mountPanel("panel-profiles", profilesPanel),
        secrets: mountPanel("panel-secrets", secretsPanel),
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
    if (typeof dialog.showModal === "function")
      dialog.showModal();
    showPane(pane);
    closeSidebarDrawer();
  }
  el("open-settings").addEventListener("click", () => openSettings("general"));
  el("model-pill").addEventListener("click", () => openSettings("models"));
  el("settings").addEventListener("close", () => {
    loadModel().catch(() => {
      return;
    });
    loadAgents().catch(() => {
      return;
    });
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
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 260)}px`;
  }
  function setOptions(open) {
    el("options-panel").hidden = !open;
    el("options-toggle").setAttribute("aria-expanded", String(open));
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
    el("execution-field").hidden = !execution;
    input("execution").disabled = !execution;
    input("execution").required = execution;
    el("execution-field").title = "Runs approved tools and tests inside restricted Docker containers.";
    el("model-pill").hidden = !policy.requiresExternal;
    el("agent-profile-pill").hidden = !["agent", "plan"].includes(mode) || !agentProfiles.length;
    if (policy.showEdits)
      setOptions(true);
    syncComposer();
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
  });
  textarea("objective").addEventListener("keydown", (event) => {
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
  function composerRequest(text) {
    return buildTaskRequest({
      objective: text,
      mode: parseMode(select("mode").value),
      edits: textarea("edits").value,
      verification: parseVerification(select("verification").value),
      approveExecution: input("execution").checked,
      approveExternal: input("external").checked,
      approveBrowser: input("browser-approval").checked,
      agentProfile: el("agent-profile").value ?? ""
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
      use_skills: field("agent-skills").checked
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
  async function sendMessage(text) {
    if (!selected)
      return false;
    const task = selected;
    try {
      const reply = await api(`/api/tasks/${encodeURIComponent(task)}/messages`, composerRequest(text));
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
    if (submitting || !textarea("objective").value.trim())
      return;
    submitting = true;
    syncSend();
    textarea("objective").disabled = true;
    const originalScope = composerDrafts.scope, originalText = textarea("objective").value;
    try {
      let text = textarea("objective").value;
      const command = /^\/(plan|code)(?:\s+([\s\S]*))?$/.exec(text.trim());
      if (command) {
        if (command[1] === "plan")
          await enterPlanning();
        else
          await enterCode();
        text = command[2]?.trim() ?? "";
        textarea("objective").value = text;
        if (!text) {
          await composerDrafts.sent(originalScope, originalText);
          return;
        }
      }
      if (selected && select("mode").value === "plan" && observedTaskMode !== "plan" && command?.[1] !== "plan")
        await enterPlanning();
      if (selected) {
        composerRequest(text);
        if (await sendMessage(text))
          await composerDrafts.sent(originalScope, originalText);
        return;
      }
      const task = await api("/api/tasks", composerRequest(text));
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
  button("cancel").onclick = async () => {
    if (!selected)
      return;
    try {
      const data = await api(`/api/tasks/${selected}/cancel`, {});
      message(data.requested ? "Stopping…" : "Task has already stopped.");
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
  });
  boot();
})();
