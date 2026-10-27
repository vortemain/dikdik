// Settings → MCP servers: built-in library, custom servers, editing, connection tests,
// per-server key mappings and OAuth sign-in. Server-provided text is always textContent.
import type { PaneDeps } from "./models-pane";

export interface McpServer {
  id: string; name: string; transport: "http" | "stdio"; url: string; command: string;
  secret_ref: string; env_var: string; env: Record<string, string>; auth: "" | "oauth";
  catalog_id: string; enabled: boolean; signed_in: boolean;
}
export interface McpArgument { name: string; label: string; required?: boolean }
export interface McpCredential { name: string; label: string; env_var?: string }
export interface McpOption {
  id: string; label: string; transport: "http" | "stdio"; url?: string; command?: string;
  auth?: "oauth"; args?: McpArgument[]; credentials?: McpCredential[];
}
export interface McpEntry { id: string; name: string; description: string; homepage: string; options: McpOption[]; installed: boolean }
export interface McpTool { name: string; description?: string }
export interface McpTest { server: string; version: string; tools: McpTool[]; tool_result?: { is_error: boolean; content: string } }
export interface OAuthJob { id: string; server_id: string; status: string; ok: boolean; error: string; error_kind: string; authorization_url?: string }

/** Textarea lines such as FOO=github-key → a server env mapping. */
export function parseEnvKeys(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    const name = at < 0 ? line : line.slice(0, at).trim();
    const ref = at < 0 ? "" : line.slice(at + 1).trim();
    if (!/^[A-Z_][A-Z0-9_]{0,63}$/.test(name) || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(ref)) {
      throw new Error(`Line ${index + 1} must be VARIABLE=api-key-name.`);
    }
    if (name in env) throw new Error(`${name} is listed twice.`);
    env[name] = ref;
  }
  if (Object.keys(env).length > 10) throw new Error("Use at most 10 environment keys.");
  return env;
}

export function filterMcp<T extends { name: string; description?: string; url?: string; command?: string }>(items: readonly T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  return q ? items.filter(item => [item.name, item.description, item.url, item.command].join(" ").toLowerCase().includes(q)) : [...items];
}

export function mcpPane(root: HTMLElement, deps: PaneDeps): () => Promise<void> {
  const { request, make, message } = deps;
  let servers: McpServer[] = [], query = "";
  const fail = (error: unknown, fallback = "Something went wrong") => message(error instanceof Error ? error.message : fallback, true);
  const button = (label: string, className = "secondary small", onclick?: () => void) => {
    const node = make("button", className, label); node.type = "button";
    if (onclick) node.onclick = onclick;
    return node;
  };
  function dialog(id: string, wide = true): HTMLDialogElement {
    let node = document.getElementById(id) as HTMLDialogElement | null;
    if (!node) {
      node = make("dialog", `small-dialog${wide ? " wide-dialog" : ""}`); node.id = id;
      node.addEventListener("click", event => { if (event.target === node) node!.close(); });
      document.body.append(node);
    }
    return node;
  }
  const head = (title: string, box: HTMLDialogElement) => {
    const row = make("div", "settings-head"); row.append(make("h3", "", title), button("Close", "ghost small", () => box.close())); return row;
  };
  async function act(work: () => Promise<unknown>, done = ""): Promise<boolean> {
    try { await work(); if (done) message(done); await refresh(); deps.changed?.(); return true; }
    catch (error) { fail(error); return false; }
  }

  function serverForm(server?: McpServer): void {
    const box = dialog("mcp-server-dialog");
    const form = make("form", "skill-form mcp-editor");
    const grid = make("div", "skill-fields");
    const field = (labelText: string, value = "", type = "text") => {
      const label = make("label", "", labelText), input = make("input"); input.type = type; input.value = value; input.autocomplete = "off";
      label.append(input); grid.append(label); return input;
    };
    const name = field("Name", server?.name ?? ""); name.maxLength = 32; name.required = true;
    const transportLabel = make("label", "", "Type"), transport = make("select");
    for (const [value, text] of [["http", "Remote (HTTP)"], ["stdio", "Local command"]] as const) { const option = make("option", "", text); option.value = value; transport.append(option); }
    transport.value = server?.transport ?? "http"; transportLabel.append(transport); grid.append(transportLabel);
    const url = field("Server URL", server?.url ?? "", "url"), command = field("Command", server?.command ?? "");
    url.placeholder = "https://mcp.example.com/mcp"; command.placeholder = "npx -y @modelcontextprotocol/server-name";
    const authLabel = make("label", "", "Sign-in"), auth = make("select");
    for (const [value, text] of [["", "API key or none"], ["oauth", "OAuth in browser"]] as const) { const option = make("option", "", text); option.value = value; auth.append(option); }
    auth.value = server?.auth ?? ""; authLabel.append(auth); grid.append(authLabel);
    const key = field("API key name", server?.secret_ref ?? ""); key.placeholder = "Optional: a name from API keys";
    const keyVar = field("Key variable", server?.env_var ?? ""); keyVar.placeholder = "Optional, e.g. GITHUB_TOKEN";
    const envLabel = make("label", "", "Extra environment keys (one VARIABLE=key-name per line)"), env = make("textarea");
    env.rows = 4; env.value = Object.entries(server?.env ?? {}).map(([variable, ref]) => `${variable}=${ref}`).join("\n"); envLabel.append(env); grid.append(envLabel);
    const sync = () => {
      const local = transport.value === "stdio", oauth = auth.value === "oauth";
      (url.parentElement as HTMLElement).hidden = local; (command.parentElement as HTMLElement).hidden = !local;
      authLabel.hidden = local; if (local) auth.value = "";
      (keyVar.parentElement as HTMLElement).hidden = !local; envLabel.hidden = !local;
      (key.parentElement as HTMLElement).hidden = !local && oauth;
    };
    transport.onchange = sync; auth.onchange = sync; sync();
    const actions = make("div", "row"), save = button(server ? "Save changes" : "Add server", "primary small"); save.type = "submit";
    actions.append(button("Cancel", "ghost small", () => box.close()), save); form.append(grid, actions);
    form.onsubmit = event => {
      event.preventDefault();
      try {
        const local = transport.value === "stdio";
        const body = {
          name: name.value.trim(), transport: transport.value,
          url: local ? "" : url.value.trim(), command: local ? command.value.trim() : "",
          auth: local ? "" : auth.value, secret_ref: !local && auth.value === "oauth" ? "" : key.value.trim(),
          env_var: local ? keyVar.value.trim() : "", env: local ? parseEnvKeys(env.value) : {},
        };
        void act(() => request(server ? `/api/mcp/${server.id}` : "/api/mcp", server ? "PATCH" : "POST", body), server ? "Server saved." : "Server added. Test it, then enable it for agent tasks.")
          .then(ok => { if (ok) box.close(); });
      } catch (error) { fail(error); }
    };
    box.replaceChildren(head(server ? `Edit ${server.name}` : "Add MCP server", box), form);
    if (!box.open) box.showModal();
  }

  function testDialog(server: McpServer): void {
    const box = dialog("mcp-test-dialog");
    const body = make("div", "info-body"), status = make("p", "hint", "Connecting…");
    body.append(status); box.replaceChildren(head(`Test ${server.name}`, box), body); if (!box.open) box.showModal();
    void request<McpTest>(`/api/mcp/${server.id}/test`, "POST", {}).then(info => {
      status.textContent = `Connected${info.server ? ` to ${info.server}` : ""}${info.version ? ` ${info.version}` : ""}. ${info.tools.length} tool${info.tools.length === 1 ? "" : "s"}.`;
      const list = make("ul", "m4-list");
      for (const tool of info.tools) {
        const item = make("li", "m4-row"), title = make("p", "m4-title"); title.append(make("span", "m4-strong m4-mono", tool.name));
        const description = make("p", "hint", tool.description || "No description."), run = button("Call tool"), result = make("pre", "skill-content"); result.hidden = true;
        run.onclick = () => {
          const raw = prompt(`JSON arguments for ${tool.name}`, "{}"); if (raw === null) return;
          let args: unknown; try { args = JSON.parse(raw); } catch { message("Arguments must be valid JSON.", true); return; }
          if (!args || Array.isArray(args) || typeof args !== "object") { message("Arguments must be a JSON object.", true); return; }
          run.disabled = true; result.hidden = false; result.textContent = "Calling…";
          void request<McpTest>(`/api/mcp/${server.id}/test`, "POST", { tool_call: { name: tool.name, arguments: args } }).then(reply => {
            result.textContent = reply.tool_result?.content ?? "No result"; result.classList.toggle("error-text", Boolean(reply.tool_result?.is_error));
          }).catch(error => { result.textContent = error instanceof Error ? error.message : "Tool call failed"; result.className = "skill-content error-text"; }).finally(() => { run.disabled = false; });
        };
        item.append(title, description, run, result); list.append(item);
      }
      body.append(list);
    }).catch(error => { status.textContent = error instanceof Error ? error.message : "Connection failed"; status.className = "error-text"; });
  }

  async function signIn(server: McpServer): Promise<void> {
    // Open while the click still carries user activation; navigating it after the
    // discovery request avoids browsers silently blocking the provider window.
    const popup = globalThis.open?.("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
      const job = await request<OAuthJob>(`/api/mcp/${server.id}/oauth/start`, "POST", {});
      if (job.status !== "authorizing" || !job.authorization_url) throw new Error(job.error || "Could not start sign-in");
      if (popup) popup.location.href = job.authorization_url;
      else {
        const box = dialog("mcp-oauth-link", false), body = make("div", "info-body"), link = make("a", "secondary", "Open sign-in page");
        link.href = job.authorization_url; link.target = "_blank"; link.rel = "noopener noreferrer";
        body.append(make("p", "hint", "Your browser blocked the sign-in window. Open it here, then return to Dikdik."), link);
        box.replaceChildren(head(`Sign in to ${server.name}`, box), body); box.showModal();
      }
      const deadline = Date.now() + 305_000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const current = await request<OAuthJob>(`/api/mcp/oauth/${job.id}`, "GET");
        if (current.status === "authorizing" || current.status === "exchanging") continue;
        if (!current.ok) throw new Error(current.error || "Sign-in was not completed");
        message(`${server.name} is signed in.`); await refresh(); return;
      }
      throw new Error("Sign-in was not completed in time");
    } catch (error) { popup?.close(); fail(error, "Sign-in failed"); }
  }

  function installDialog(entry: McpEntry): void {
    if (!entry.options.length) { message("This library entry has no connection method.", true); return; }
    const box = dialog("mcp-install-dialog");
    const form = make("form", "skill-form"), intro = make("p", "hint", entry.description), grid = make("div", "skill-fields");
    const nameLabel = make("label", "", "Name"), name = make("input"); name.value = entry.id; nameLabel.append(name); grid.append(nameLabel);
    const optionLabel = make("label", "", "Connection"), option = make("select");
    for (const item of entry.options) { const node = make("option", "", item.label); node.value = item.id; option.append(node); }
    optionLabel.append(option); grid.append(optionLabel);
    const dynamic = make("div", "mcp-install-fields"), approval = make("label", "check approval-check"), approve = make("input"); approve.type = "checkbox";
    approval.append(approve, document.createTextNode("I approve running this command on this machine"));
    const enabledLabel = make("label", "check"), enabled = make("input"); enabled.type = "checkbox"; enabledLabel.append(enabled, document.createTextNode("Enable for agent tasks after installation"));
    let argFields = new Map<string, HTMLInputElement>(), credentialFields = new Map<string, { ref: HTMLInputElement; value: HTMLInputElement }>();
    const renderOption = () => {
      const selected = entry.options.find(item => item.id === option.value) ?? entry.options[0]!;
      argFields = new Map(); credentialFields = new Map(); dynamic.replaceChildren();
      dynamic.append(make("p", "m4-meta m4-mono", selected.transport === "http" ? selected.url ?? "" : selected.command ?? ""));
      for (const arg of selected.args ?? []) {
        const label = make("label", "", arg.label), input = make("input"); input.required = Boolean(arg.required); label.append(input); dynamic.append(label); argFields.set(arg.name, input);
      }
      for (const credential of selected.credentials ?? []) {
        const refLabel = make("label", "", `${credential.label} · API key name`), ref = make("input"); ref.required = true; ref.placeholder = "Existing name, or name for the new key"; refLabel.append(ref);
        const valueLabel = make("label", "", `${credential.label} · new value (optional)`), value = make("input"); value.type = "password"; value.autocomplete = "new-password"; value.placeholder = "Leave blank to use the stored key"; valueLabel.append(value);
        dynamic.append(refLabel, valueLabel); credentialFields.set(credential.name, { ref, value });
      }
      approval.hidden = selected.transport !== "stdio";
    };
    option.onchange = renderOption; renderOption();
    const actions = make("div", "row"), install = button("Install", "primary small"); install.type = "submit";
    actions.append(button("Cancel", "ghost small", () => box.close()), install); form.append(intro, grid, dynamic, approval, enabledLabel, actions);
    form.onsubmit = event => {
      event.preventDefault(); const selected = entry.options.find(item => item.id === option.value) ?? entry.options[0]!;
      if (selected.transport === "stdio" && !approve.checked) { message("Approve running the command first.", true); return; }
      const args = Object.fromEntries([...argFields].map(([key, input]) => [key, input.value]));
      const credentials = Object.fromEntries([...credentialFields].map(([key, fields]) => [key, fields.value.value ? { save_as: fields.ref.value.trim(), value: fields.value.value } : { ref: fields.ref.value.trim() }]));
      install.disabled = true;
      void request<{ name: string; oauth: boolean }>("/api/mcp/catalog/install", "POST", {
        entry: entry.id, option: selected.id, name: name.value.trim(), args, credentials,
        approve_command: selected.transport === "stdio" && approve.checked, enable: enabled.checked,
      }).then(done => { box.close(); message(`Installed ${done.name}.${done.oauth ? " Sign in before enabling it." : ""}`); return refresh(); })
        .catch(error => fail(error, "Install failed")).finally(() => { install.disabled = false; });
    };
    box.replaceChildren(head(`Install ${entry.name}`, box), form); if (!box.open) box.showModal();
  }

  async function libraryDialog(): Promise<void> {
    const box = dialog("mcp-library-dialog"), body = make("div", "info-body"), loading = make("p", "hint", "Loading server library…");
    box.replaceChildren(head("MCP server library", box), body); body.append(loading); if (!box.open) box.showModal();
    try {
      const entries = await request<McpEntry[]>("/api/mcp/catalog", "GET"), search = make("input"); search.type = "search"; search.placeholder = "Search servers"; search.setAttribute("aria-label", "Search MCP servers");
      const grid = make("ul", "skill-cards");
      const draw = () => {
        const found = filterMcp(entries, search.value);
        grid.replaceChildren(...found.map(entry => {
          const card = make("li", "skill-card"), top = make("div", "skill-card-top"); top.append(make("strong", "", entry.name));
          const install = button(entry.installed ? "Installed" : "Install", "small", () => installDialog(entry)); install.disabled = entry.installed; top.append(install);
          const methods = make("p", "skill-pills"); for (const item of entry.options) methods.append(make("span", "m4-badge muted-badge", item.transport === "http" ? "Remote" : "Command"));
          card.append(top, make("p", "skill-desc", entry.description), methods); return card;
        }));
        if (!found.length) grid.replaceChildren(make("li", "hint", "No servers match your search."));
      };
      search.oninput = draw; body.replaceChildren(search, grid); draw();
    } catch (error) { body.replaceChildren(make("p", "error-text", error instanceof Error ? error.message : "Could not load the library")); }
  }

  function render(): void {
    const toolbar = make("div", "plugin-toolbar"), search = make("input"); search.type = "search"; search.value = query; search.placeholder = "Search configured servers"; search.setAttribute("aria-label", "Search configured MCP servers");
    toolbar.append(search, button("Browse library", "secondary small", () => { void libraryDialog(); }), button("Add server", "primary small", () => serverForm()));
    const list = make("ol", "m4-list mcp-server-list");
    const draw = () => {
      const found = filterMcp(servers, query);
      list.replaceChildren(...found.map(server => {
        const row = make("li", "m4-row"), title = make("p", "m4-title");
        title.append(make("span", "m4-strong", server.name), make("span", "m4-badge", server.transport === "http" ? "remote" : "command"));
        if (server.auth === "oauth") title.append(make("span", server.signed_in ? "m4-badge" : "m4-badge warn-badge", server.signed_in ? "signed in" : "sign-in needed"));
        const target = make("p", "m4-meta m4-mono", server.transport === "http" ? server.url : server.command), keys = make("p", "hint");
        const refs = [server.secret_ref && `${server.env_var || "Authorization"} → ${server.secret_ref}`, ...Object.entries(server.env).map(([key, ref]) => `${key} → ${ref}`)].filter(Boolean);
        keys.textContent = refs.length ? `Keys: ${refs.join(", ")}` : "No API keys assigned.";
        const actions = make("div", "m4-actions"), toggle = make("input"); toggle.type = "checkbox"; toggle.checked = server.enabled; toggle.setAttribute("aria-label", `Enable ${server.name}`);
        toggle.onchange = () => { const enabled = toggle.checked; void act(() => request(`/api/mcp/${server.id}`, "PATCH", { enabled }), enabled ? `${server.name} enabled for agent tasks.` : `${server.name} disabled.`).then(ok => { if (!ok) toggle.checked = !enabled; }); };
        const switchLabel = make("label", "switch"); switchLabel.title = "Enable for agent tasks"; switchLabel.append(toggle); actions.append(switchLabel);
        if (server.auth === "oauth") actions.append(server.signed_in
          ? button("Sign out", "secondary small", () => { void act(() => request(`/api/mcp/${server.id}/oauth`, "DELETE"), `${server.name} signed out.`); })
          : button("Sign in", "primary small", () => { void signIn(server); }));
        actions.append(button("Test", "secondary small", () => testDialog(server)), button("Edit", "secondary small", () => serverForm(server)), button("Delete", "secondary small", () => {
          if (!confirm(`Delete ${server.name}?`)) return; void act(() => request(`/api/mcp/${server.id}`, "DELETE"), "Server deleted.");
        }));
        row.append(title, target, keys, actions); return row;
      }));
      if (!found.length) list.replaceChildren(make("li", "hint", query ? "No servers match your search." : "No MCP servers yet. Browse the library or add one manually."));
    };
    search.oninput = () => { query = search.value; draw(); }; draw(); root.replaceChildren(toolbar, list);
  }
  async function refresh(): Promise<void> {
    try { servers = await request<McpServer[]>("/api/mcp", "GET"); render(); }
    catch (error) { root.replaceChildren(make("p", "error-text", `Could not load MCP servers: ${error instanceof Error ? error.message : "unknown error"}`)); }
  }
  return refresh;
}
