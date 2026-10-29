// Settings → Plugins: installed, project and marketplace plugins; a review before trusting one.
// Every value from a plugin (names, descriptions, file text) is shown as plain text, never HTML.
import type { PaneDeps } from "./models-pane";
import { confirmDialog } from "./dialogs";
import { isNetworkSource } from "./skills-pane";

export interface PluginHook {
  tool?: string; pattern?: string; message?: string; id?: string; event?: string;
  decision?: string; reason?: string; priority?: number; timeout_seconds?: number;
  match?: { tool?: string; contains?: string; pattern?: string };
}
export interface PluginAdds { skills: string[]; agents: string[]; mcp_servers: { name: string; transport: string; target: string }[]; hooks: PluginHook[] }
export interface Parameter { name: string; type?: "text" | "number" | "boolean"; label?: string }
export interface Plugin {
  name: string; scope: "installed" | "project"; version?: string; description?: string; enabled: boolean; trusted: boolean;
  source?: string; resolved_ref?: string; parameters?: Parameter[]; prompt?: string; adds?: PluginAdds; error?: string; files?: string[];
}
export interface PluginEntry { marketplace: string; marketplace_name: string; name: string; description: string; version: string; repo_path: string; installed: boolean }
export type PluginView = "all" | "installed" | "available" | "project";
export interface PluginCard { kind: "plugin"; plugin: Plugin }
export interface EntryCard { kind: "entry"; entry: PluginEntry }

/** Plain text shown before enabling a plugin; preserve the actual rule semantics. */
export function pluginHookLabel(hook: PluginHook): string {
  if (!hook.event) return `tool.before · block · ${hook.tool || "*"}: ${hook.pattern || "everything"}${hook.message ? ` · ${hook.message}` : ""}`;
  const conditions = [hook.match?.tool || "*", hook.match?.contains ? `contains ${hook.match.contains}` : "", hook.match?.pattern ? `pattern ${hook.match.pattern}` : ""].filter(Boolean).join(" · ");
  return `${hook.id || "Hook"} · ${hook.event} · ${hook.decision || "invalid"} · ${conditions} · priority ${hook.priority ?? 0} · ${hook.timeout_seconds ?? 5}s${hook.reason ? ` · ${hook.reason}` : ""}`;
}

/** The cards a view shows, filtered by the search text (name, description, source, ref). */
export function pluginCards(plugins: readonly Plugin[], catalog: readonly PluginEntry[], view: PluginView, query: string): (PluginCard | EntryCard)[] {
  const q = query.trim().toLowerCase();
  const match = (...parts: (string | undefined)[]) => !q || parts.join(" ").toLowerCase().includes(q);
  const mine: (PluginCard | EntryCard)[] = plugins
    .filter(p => view === "all" || (view === "installed" && p.scope === "installed") || (view === "project" && p.scope === "project"))
    .filter(p => match(p.name, p.description, p.source, p.resolved_ref))
    .map(plugin => ({ kind: "plugin", plugin }));
  const available: (PluginCard | EntryCard)[] = view === "all" || view === "available"
    ? catalog.filter(e => !e.installed && match(e.name, e.description, e.marketplace_name, e.repo_path)).map(entry => ({ kind: "entry", entry }))
    : [];
  return view === "available" ? available : [...mine, ...available];
}
/** Launch parameters from the form: numbers default to 0, booleans to false. */
export function launchValues(parameters: readonly Parameter[], read: (name: string) => string | boolean): Record<string, string | number | boolean> {
  const values: Record<string, string | number | boolean> = {};
  for (const parameter of parameters) {
    const raw = read(parameter.name);
    if (parameter.type === "boolean") values[parameter.name] = raw === true;
    else if (parameter.type === "number") { const n = Number(raw); values[parameter.name] = Number.isFinite(n) && String(raw).trim() !== "" ? n : 0; }
    else values[parameter.name] = String(raw ?? "");
  }
  return values;
}

export function pluginsPane(root: HTMLElement, deps: PaneDeps & { startTask: (prompt: string) => void }): () => Promise<void> {
  const { request, make, message } = deps;
  let plugins: Plugin[] = [], catalog: PluginEntry[] = [], view: PluginView = "all", query = "";
  const fail = (error: unknown) => message(error instanceof Error ? error.message : "Something went wrong", true);
  const button = (label: string, className = "secondary", onclick?: () => void) => {
    const node = make("button", className, label); node.type = "button";
    if (onclick) node.onclick = onclick;
    return node;
  };
  async function act(work: () => Promise<unknown>, done: string): Promise<void> {
    try { await work(); if (done) message(done); await refresh(); deps.changed?.(); } catch (error) { fail(error); }
  }
  async function refresh(): Promise<void> {
    try {
      [plugins, catalog] = await Promise.all([request<Plugin[]>("/api/plugins", "GET"), request<PluginEntry[]>("/api/plugins/catalog", "GET")]);
      render();
    } catch (error) { root.replaceChildren(make("p", "error-text", `Could not load plugins: ${error instanceof Error ? error.message : "unknown error"}`)); }
  }
  const url = (name: string, tail = "") => `/api/plugins/${encodeURIComponent(name)}${tail}`;

  function dialog(): HTMLDialogElement {
    let node = document.getElementById("plugin-dialog") as HTMLDialogElement | null;
    if (!node) {
      node = make("dialog", "small-dialog wide-dialog plugin-dialog"); node.id = "plugin-dialog";
      node.addEventListener("click", event => { if (event.target === node) node!.close(); });
      document.body.append(node);
    }
    return node;
  }
  function addsList(adds: PluginAdds | undefined): HTMLElement {
    const list = make("dl", "info-list");
    const row = (label: string, items: string[]) => list.append(make("dt", "", label), make("dd", "", items.length ? items.join(", ") : "none"));
    row("Skills", adds?.skills ?? []);
    row("Subagents", adds?.agents ?? []);
    row("Hooks", (adds?.hooks ?? []).map(pluginHookLabel));
    row("MCP servers (added switched off)", (adds?.mcp_servers ?? []).map(s => `${s.name} (${s.transport} ${s.target})`));
    return list;
  }
  async function openDetail(name: string): Promise<void> {
    let plugin: Plugin;
    try { plugin = await request<Plugin>(url(name), "GET"); } catch (error) { fail(error); return; }
    const box = dialog();
    const head = make("div", "settings-head");
    head.append(make("h3", "", `${plugin.name}${plugin.version ? ` v${plugin.version}` : ""}`), button("Close", "ghost small", () => box.close()));
    const body = make("div", "info-body");
    body.append(make("p", "hint", plugin.scope === "project" ? "Shipped with this project (.dikdik/plugins)." : `Installed from ${plugin.source ?? "a folder"}${plugin.resolved_ref ? ` at ${plugin.resolved_ref.slice(0, 12)}` : ""}.`));
    body.append(make("p", "", plugin.description || "No description."));
    body.append(make("h4", "info-label", "This plugin adds"), addsList(plugin.adds));
    // Trust and enable
    const controls = make("div", "plugin-controls");
    if (!plugin.trusted) {
      const trust = make("label", "check approval-check"); const box2 = make("input"); box2.type = "checkbox";
      trust.append(box2, document.createTextNode(`I trust this plugin from ${plugin.scope === "project" ? "this project" : plugin.source ?? "this folder"} and want it to add the items above`));
      const enable = button("Enable", "primary small", () => {
        if (!box2.checked) { message("Tick the trust box first.", true); return; }
        void act(() => request(url(plugin.name), "PATCH", { enabled: true, trust: true }), "Plugin enabled. Its MCP servers are switched off until you enable them.").then(() => openDetail(plugin.name));
      });
      controls.append(trust, enable);
    } else {
      controls.append(button(plugin.enabled ? "Disable" : "Enable", plugin.enabled ? "secondary small" : "primary small", () => {
        void act(() => request(url(plugin.name), "PATCH", { enabled: !plugin.enabled }), plugin.enabled ? "Plugin disabled." : "Plugin enabled.").then(() => openDetail(plugin.name));
      }));
    }
    if (plugin.scope === "installed") {
      if (plugin.source && plugin.source !== "local") controls.append(button("Update", "secondary small", async () => {
        const network = isNetworkSource(plugin.source ?? "");
        if (network && !await confirmDialog(`Refresh ${plugin.name}?`, `Fetch this plugin again from ${plugin.source}.`, "Fetch plugin")) return;
        void act(() => request(url(plugin.name, "/refresh"), "POST", { approve_network: network }), "Plugin updated.").then(() => openDetail(plugin.name));
      }));
      controls.append(button("Uninstall", "secondary small", () => { box.close(); void act(() => request(url(plugin.name), "DELETE"), "Plugin uninstalled."); }));
    }
    body.append(controls);
    // Start a conversation with it
    if (plugin.prompt && plugin.enabled && plugin.trusted) {
      const launch = make("form", "skill-form");
      launch.append(make("h4", "info-label", "Start a conversation"));
      const grid = make("div", "skill-fields");
      const fields = new Map<string, HTMLInputElement>();
      for (const parameter of plugin.parameters ?? []) {
        const label = make("label", "", parameter.label || parameter.name); const input = make("input");
        input.type = parameter.type === "boolean" ? "checkbox" : parameter.type === "number" ? "number" : "text";
        input.id = `plugin-param-${parameter.name}`; label.htmlFor = input.id;
        fields.set(parameter.name, input); grid.append(label, input);
      }
      const go = button("Start conversation", "primary small"); go.type = "submit";
      launch.append(grid, go);
      launch.onsubmit = event => {
        event.preventDefault();
        const values = launchValues(plugin.parameters ?? [], name => { const f = fields.get(name)!; return f.type === "checkbox" ? f.checked : f.value; });
        void request<{ prompt: string }>(url(plugin.name, "/launch"), "POST", { parameters: values })
          .then(reply => { box.close(); deps.startTask(reply.prompt); }).catch(fail);
      };
      body.append(launch);
    }
    // Files
    const files = make("details", "plugin-files");
    files.append(make("summary", "", `Files (${plugin.files?.length ?? 0})`));
    const list = make("ul", "plugin-file-list"), view = make("pre", "skill-content"); view.hidden = true;
    for (const path of plugin.files ?? []) {
      const item = make("li"); const open = button(path, "ghost small", () => {
        void request<{ text: string; binary: boolean }>(url(plugin.name, `/files?path=${encodeURIComponent(path)}`), "GET")
          .then(file => { view.hidden = false; view.textContent = file.binary ? "Binary file – preview not available" : file.text; }).catch(fail);
      });
      item.append(open); list.append(item);
    }
    files.append(list, view);
    body.append(files);
    box.replaceChildren(head, body);
    if (!box.open) box.showModal();
  }

  function render(): void {
    const top = make("div", "plugin-toolbar");
    const search = make("input"); search.type = "search"; search.placeholder = "Search plugins"; search.value = query;
    search.setAttribute("aria-label", "Search plugins");
    const segments = make("div", "segmented");
    for (const [key, label] of [["all", "All"], ["installed", "Installed"], ["available", "Available"], ["project", "Project"]] as const) {
      const seg = make("button", "", label); seg.type = "button"; seg.setAttribute("aria-pressed", String(view === key));
      seg.onclick = () => { view = key; render(); };
      segments.append(seg);
    }
    top.append(search, segments);
    const grid = make("ul", "skill-cards");
    const draw = () => {
      const cards = pluginCards(plugins, catalog, view, query);
      grid.replaceChildren(...cards.map(card => {
        const li = make("li", "skill-card");
        if (card.kind === "plugin") {
          const p = card.plugin;
          li.tabIndex = 0; li.setAttribute("role", "button");
          li.onclick = () => { void openDetail(p.name); };
          li.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void openDetail(p.name); } };
          const head = make("div", "skill-card-top"); head.append(make("strong", "", p.name));
          head.append(make("span", p.enabled ? "m4-badge" : "m4-badge muted-badge", p.error ? "Invalid" : p.enabled ? "On" : p.trusted ? "Off" : "Not trusted"));
          const pills = make("p", "skill-pills");
          pills.append(make("span", "m4-badge muted-badge", p.scope === "project" ? "Project" : "Installed"));
          if (p.version) pills.append(make("span", "m4-badge muted-badge", `v${p.version}`));
          li.append(head, make("p", "skill-desc", p.error || p.description || "No description."), pills);
        } else {
          const e = card.entry;
          const head = make("div", "skill-card-top"); head.append(make("strong", "", e.name));
          head.append(button("Install", "small", () => { void act(() => request("/api/plugins/catalog/install", "POST", { marketplace: e.marketplace, repo_path: e.repo_path }), "Plugin installed. Open it to review and enable it."); }));
          const pills = make("p", "skill-pills"); pills.append(make("span", "m4-badge muted-badge", e.marketplace_name));
          if (e.version) pills.append(make("span", "m4-badge muted-badge", `v${e.version}`));
          li.append(head, make("p", "skill-desc", e.description || "No description."), pills);
        }
        return li;
      }));
      if (!cards.length) grid.replaceChildren(make("li", "hint", query ? "No plugins match your search." : "No plugins here yet."));
    };
    search.oninput = () => { query = search.value; draw(); };
    draw();
    // Add a plugin
    const form = make("form", "skill-form");
    form.append(make("h4", "info-label", "Add a plugin"));
    const fields = make("div", "skill-fields");
    const field = (label: string, id: string, placeholder: string) => { const l = make("label", "", label); l.htmlFor = id; const i = make("input"); i.id = id; i.placeholder = placeholder; i.autocomplete = "off"; fields.append(l, i); return i; };
    const source = field("Source", "plugin-source", "A folder, https://…, file://… or github:owner/repo");
    const path = field("Folder in source", "plugin-path", "Optional");
    const ref = field("Version", "plugin-ref", "Optional branch, tag or commit");
    const ok = make("label", "check approval-check"); const okBox = make("input"); okBox.type = "checkbox";
    ok.append(okBox, document.createTextNode("I approve fetching this from the internet")); ok.hidden = true;
    const install = button("Install", "primary small"); install.type = "submit"; install.disabled = true;
    source.oninput = () => { ok.hidden = !isNetworkSource(source.value); install.disabled = !source.value.trim(); };
    form.append(fields, ok, install);
    form.onsubmit = event => {
      event.preventDefault();
      const network = isNetworkSource(source.value);
      if (network && !okBox.checked) { message("Approve fetching from the internet first.", true); return; }
      void act(async () => {
        const done = await request<{ name: string }>("/api/plugins/install", "POST", { source: source.value, repo_path: path.value.trim() || null, ref: ref.value.trim() || null, approve_network: network && okBox.checked });
        message(`Installed “${done.name}”. Open it to review what it adds and enable it.`);
      }, "");
    };
    root.replaceChildren(top, grid, form);
  }
  return refresh;
}
