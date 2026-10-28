/** A quiet credential vault: values are write-only, advanced controls stay folded. */
import type { PaneDeps, SecretEntry, Connection } from "./models-pane";
import { when } from "./models-pane";
import { providerIcon, providerId } from "./icons";

export function keysPane(root: HTMLElement, deps: PaneDeps): () => Promise<void> {
  const { request, make, message } = deps;
  let keys: SecretEntry[] = [], connections: Connection[] = [], editing = "", adding = false, query = "", busy = false, deleting = "";
  const button = (text: string, style: string, action?: () => void) => {
    const node = make("button", style, text); node.type = "button"; node.disabled = busy;
    if (action) node.onclick = action; return node;
  };
  const field = (label: string, id: string, type: string, value: string, placeholder = "") => {
    const box = make("div", "vault-field"), title = make("label", "", label), input = make("input");
    title.htmlFor = id; input.id = id; input.type = type; input.value = value; input.placeholder = placeholder;
    input.autocomplete = "off"; input.spellcheck = false; box.append(title, input); return { box, input };
  };
  const navigate = (provider?: string) => { if (deps.navigate) deps.navigate("models", provider); else location.hash = "#settings/models"; };
  async function act(work: () => Promise<unknown>, done: string): Promise<void> {
    if (busy) return;
    busy = true;
    for (const control of root.querySelectorAll<HTMLButtonElement>("button")) control.disabled = true;
    try {
      await work(); editing = ""; deleting = ""; adding = false; message(done); await refresh(); deps.changed?.();
    } catch (error) { message(error instanceof Error ? error.message : "Could not save this change", true); }
    finally { busy = false; for (const control of root.querySelectorAll<HTMLButtonElement>("button")) control.disabled = false; }
  }
  function newKeyForm(): HTMLElement {
    const form = make("form", "vault-form");
    const name = field("Key name", "key-name", "text", "", "e.g. github-work"), secret = field("API key or token", "key-value", "password", "", "Paste your key");
    name.input.required = true; name.input.maxLength = 64; secret.input.required = true;
    const fields = make("div", "vault-form-fields"); fields.append(name.box, secret.box);
    const actions = make("div", "vault-form-actions"), save = button("Save key", "primary small"); save.type = "submit";
    const error = make("p", "vault-error"); error.setAttribute("role", "alert"); error.hidden = true;
    actions.append(make("span", "", "Encrypted locally. Never displayed after saving."), button("Cancel", "ghost small", () => { adding = false; render(); }), save);
    form.append(make("h4", "", "Add a key"), fields, error, actions);
    form.onsubmit = event => {
      event.preventDefault();
      const ref = name.input.value.trim();
      if (keys.some(key => key.ref === ref)) { error.hidden = false; error.textContent = "That name already exists. Use Manage → Replace key to update it."; name.input.focus(); return; }
      if (!secret.input.value.trim()) return;
      void act(async () => { await request("/api/secrets", "POST", { ref, plaintext: secret.input.value }); secret.input.value = ""; }, `Saved “${ref}”.`);
    };
    setTimeout(() => name.input.focus(), 0); return form;
  }
  function editor(key: SecretEntry): HTMLElement {
    const box = make("div", "vault-editor");
    const rename = make("form", "vault-edit-row"), name = field("Key name", "vault-rename", "text", key.ref);
    name.input.maxLength = 64; name.input.required = true;
    const renameButton = button("Save name", "secondary small"); renameButton.type = "submit";
    rename.append(name.box, renameButton);
    rename.onsubmit = event => { event.preventDefault(); if (name.input.value.trim() !== key.ref) void act(() => request(`/api/secrets/${encodeURIComponent(key.ref)}/rename`, "POST", { to: name.input.value.trim() }), "Key renamed. Linked profiles and MCP servers were updated."); };
    const replace = make("form", "vault-edit-row"), value = field("Replace key", "vault-replace", "password", "", "Paste a new value"); value.input.required = true;
    const replaceButton = button("Replace key", "secondary small"); replaceButton.type = "submit";
    replace.append(value.box, replaceButton);
    replace.onsubmit = event => { event.preventDefault(); if (value.input.value.trim()) void act(async () => { await request("/api/secrets", "POST", { ref: key.ref, plaintext: value.input.value }); value.input.value = ""; }, "Key replaced. The previous value can be restored."); };
    const advanced = make("details", "vault-advanced"), summary = make("summary", "", "Sandbox access");
    const env = field("Environment variable", "vault-env", "text", key.env, "e.g. GITHUB_TOKEN"); env.input.maxLength = 64;
    const envForm = make("form", "vault-edit-row"), envButton = button("Save access", "secondary small"); envButton.type = "submit";
    envForm.append(env.box, envButton);
    envForm.onsubmit = event => { event.preventDefault(); void act(() => request(`/api/secrets/${encodeURIComponent(key.ref)}`, "PATCH", { env: env.input.value.trim() || null }), env.input.value.trim() ? "Sandbox access updated. Each task still needs your approval." : "Sandbox access removed."); };
    advanced.append(summary, make("p", "", "Only tasks you explicitly allow can receive this key. Leave blank to keep it out of sandbox commands."), envForm);
    const actions = make("div", "vault-editor-actions");
    if (key.previous_set_at) actions.append(button("Restore previous value", "ghost small", () => { void act(() => request(`/api/secrets/${encodeURIComponent(key.ref)}/restore`, "POST", {}), "Previous value restored."); }));
    if (deleting === key.ref) {
      actions.append(make("span", "vault-delete-note", "Delete this saved key?"), button("Cancel", "ghost small", () => { deleting = ""; render(); }),
        button("Delete key", "danger small", () => { void act(() => request(`/api/secrets/${encodeURIComponent(key.ref)}`, "DELETE"), "Key deleted."); }));
    } else actions.append(button("Delete key…", "ghost small vault-delete", () => { deleting = key.ref; render(); }));
    box.append(rename, replace, advanced, actions); return box;
  }
  function render(): void {
    root.classList.add("credential-vault");
    const intro = make("div", "vault-intro"), copy = make("div");
    copy.append(make("h3", "", "Your keys, kept private."), make("p", "", "Manage provider connections and saved credentials in one place."));
    intro.append(copy, make("span", "vault-security", "◈ Encrypted on this device"));
    const providerSection = make("section", "vault-section"), providerHead = make("div", "vault-section-head");
    providerHead.append(make("h4", "", "Provider credentials"), button("Manage providers ↗", "ghost small", navigate)); providerSection.append(providerHead);
    const connected = connections.filter(connection => connection.key_set);
    if (connected.length) {
      const list = make("div", "vault-provider-list");
      for (const connection of connected) {
        const row = make("div", "vault-provider"), text = make("div", "vault-key-text");
        text.append(make("strong", "", connection.name), make("small", "", `${connection.models} saved model${connection.models === 1 ? "" : "s"}`));
        const provider = providerId(connection.provider, connection.endpoint);
        row.append(providerIcon(provider), text, make("span", "vault-connected", "Connected"), button("Manage", "ghost small", () => navigate(provider || undefined))); list.append(row);
      }
      providerSection.append(list);
    } else providerSection.append(make("div", "vault-provider-empty", "Connect a provider in Models to save its key and browse its available models."));
    const vault = make("section", "vault-section"), toolbar = make("div", "vault-section-head");
    const heading = make("h4", "", "Saved keys"); heading.append(make("span", "vault-count", String(keys.length)));
    toolbar.append(heading, button("＋ Add key", "primary small", () => { adding = !adding; editing = ""; render(); })); vault.append(toolbar);
    if (adding) vault.append(newKeyForm());
    const search = make("input", "vault-search"); search.type = "search"; search.placeholder = "Search saved keys"; search.value = query; search.setAttribute("aria-label", "Search saved keys");
    const list = make("ul", "vault-key-list");
    const paint = () => {
      list.replaceChildren();
      const shown = keys.filter(key => (key.ref + " " + key.env).toLowerCase().includes(query.trim().toLowerCase()));
      for (const key of shown) {
        const item = make("li", "vault-key"), row = make("div", "vault-key-row"), mark = make("span", "vault-key-symbol"), text = make("div", "vault-key-text");
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        svg.setAttribute("class", "i"); use.setAttribute("href", "#i-key"); svg.append(use); mark.append(svg);
        mark.setAttribute("aria-hidden", "true");
        const info = make("small", "", `Updated ${when(key.set_at)}`);
        text.append(make("strong", "", key.ref), info);
        const hidden = make("span", "vault-masked", "••••••••"); hidden.setAttribute("aria-label", "Saved value hidden");
        const manage = button(editing === key.ref ? "Done" : "Manage", "ghost small", () => { editing = editing === key.ref ? "" : key.ref; deleting = ""; render(); }); manage.setAttribute("aria-expanded", String(editing === key.ref)); manage.setAttribute("aria-label", `Manage ${key.ref}`);
        row.append(mark, text, hidden, manage); item.append(row);
        if (key.env) item.append(make("p", "vault-access-note", `Sandbox variable · ${key.env}`));
        if (editing === key.ref) item.append(editor(key));
        list.append(item);
      }
      if (!shown.length) {
        const empty = make("li", "vault-empty");
        empty.append(make("strong", "", keys.length ? "No matching keys" : "No saved keys yet"), make("p", "", keys.length ? "Try a different name." : "Add a token for an MCP server, model profile, or approved sandbox task.")); list.append(empty);
      }
    };
    search.oninput = () => { query = search.value; paint(); };
    if (keys.length) vault.append(search);
    vault.append(list); paint();
    const foot = make("p", "vault-footnote", "Key values stay encrypted locally and are never sent back to this page.");
    root.replaceChildren(intro, providerSection, vault, foot);
  }
  async function refresh(): Promise<void> {
    try {
      const [stored, linked] = await Promise.all([request<SecretEntry[]>("/api/secrets", "GET"), request<Connection[]>("/api/connections", "GET").catch(() => [])]);
      keys = stored; connections = linked; render();
    } catch (error) {
      root.replaceChildren(make("p", "error-text", error instanceof Error ? error.message : "Could not load saved keys"), button("Try again", "secondary small", () => { void refresh(); }));
    }
  }
  return refresh;
}
