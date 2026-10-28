/**
 * Settings → Models, OpenRouter-style: every provider with its models.
 *
 * Connect a provider by pasting its key once; pick any model (the suggestions, or the
 * provider's own live list) and it becomes the model Dikdik uses. Your own
 * OpenAI-compatible provider is a name, a URL and a key. Everything older (connections,
 * profiles, routers) stays available under "Advanced".
 */
import type { PaneDeps } from "./models-pane";

export interface CatalogProvider {
  id: string; name: string; kind: string; base: string; key_help: string; blurb: string; models: string[];
  connection: string | null; connection_name: string | null; key_set: boolean; no_key?: boolean; custom?: boolean;
}
export interface LiveModel { id: string; name: string; context: number | null; prompt_price: string | null; completion_price: string | null }

/** "$0.15 / $0.60 per 1M tokens" from per-token prices, as OpenRouter lists them. */
export function priceLabel(model: LiveModel): string {
  const perMillion = (value: string | null) => value === null || value === undefined || value === "" || Number.isNaN(Number(value)) ? null : Number(value) * 1_000_000;
  const input = perMillion(model.prompt_price), output = perMillion(model.completion_price);
  if (input === null && output === null) return "";
  if (input === 0 && output === 0) return "Free";
  const money = (n: number | null) => n === null ? "?" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
  return `${money(input)} in · ${money(output)} out per 1M`;
}
export function contextLabel(tokens: number | null): string {
  if (!tokens) return "";
  return tokens >= 1_000_000 ? `${Math.round(tokens / 100_000) / 10}M context` : `${Math.round(tokens / 1000)}K context`;
}

export function providersPane(root: HTMLElement, deps: PaneDeps): () => Promise<void> {
  const { request, make, message } = deps;
  let providers: CatalogProvider[] = [], selected = "", current = "", query = "";
  const live = new Map<string, LiveModel[]>();
  const fail = (error: unknown) => message(error instanceof Error ? error.message : "Something went wrong", true);
  const button = (label: string, className: string, onclick: () => void) => {
    const node = make("button", className, label) as HTMLButtonElement; node.type = "button"; node.onclick = onclick; return node;
  };

  async function load(): Promise<void> {
    const [catalog, model] = await Promise.all([
      request<{ order: string[]; providers: Record<string, CatalogProvider> }>("/api/model-catalog", "GET"),
      request<{ configured: boolean; model?: string; provider_name?: string; profile?: string }>("/api/model", "GET"),
    ]);
    providers = catalog.order.map(id => catalog.providers[id]!).filter(Boolean);
    current = model.configured ? `${model.model ?? ""}${model.profile ? ` · ${model.profile}` : ""}` : "";
    render();
  }

  async function use(provider: CatalogProvider, model: string): Promise<void> {
    try {
      await request("/api/model-catalog/" + encodeURIComponent(provider.id) + "/use", "POST", { model, default: true });
      message(`Now using ${model}.`);
      deps.changed?.();
      await load();
    } catch (error) { fail(error); }
  }

  function modelRow(provider: CatalogProvider, id: string, detail = ""): HTMLElement {
    const row = make("li", "provider-model");
    const text = make("span", "provider-model-text");
    text.append(make("strong", "", id));
    if (detail) text.append(make("small", "", detail));
    row.append(text, button(provider.connection ? "Use" : "Connect first", "ghost small", () => { if (provider.connection) void use(provider, id); }));
    return row;
  }

  function detail(provider: CatalogProvider): HTMLElement {
    const box = make("section", "provider-detail");
    const head = make("div", "provider-detail-head");
    head.append(avatar(provider), make("strong", "", provider.name), make("span", provider.connection ? "state ok" : "state", provider.connection ? "Connected" : "Not connected"));
    box.append(head, make("p", "muted", provider.blurb + (provider.base ? ` · ${provider.base}` : "")));
    // Connect, or replace the key.
    const form = make("form", "provider-connect");
    const key = make("input") as HTMLInputElement;
    key.type = "password"; key.autocomplete = "off"; key.placeholder = provider.no_key ? "No key needed (optional)" : provider.connection ? "Paste a new key to replace it" : `Paste your ${provider.name} API key`;
    key.setAttribute("aria-label", `${provider.name} API key`);
    const endpoint = make("input") as HTMLInputElement;
    endpoint.placeholder = "https://my-resource.openai.azure.com/openai/v1"; endpoint.hidden = provider.kind !== "azure" || !!provider.connection;
    const submit = make("button", "primary small", provider.connection ? "Replace key" : "Connect") as HTMLButtonElement; submit.type = "submit";
    form.append(key, endpoint, submit);
    if (provider.key_help) { const link = make("a", "", provider.no_key ? "Download" : "Get a key") as HTMLAnchorElement; link.href = provider.key_help; link.target = "_blank"; link.rel = "noopener noreferrer"; form.append(link); }
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!key.value.trim() && !provider.no_key && !provider.connection) { message(`Paste your ${provider.name} API key.`, true); return; }
      if (provider.connection && !key.value.trim()) return;
      submit.disabled = true;
      try {
        await request(`/api/model-catalog/${encodeURIComponent(provider.id)}/connect`, "POST", { api_key: key.value.trim() || undefined, endpoint: endpoint.value.trim() || undefined });
        key.value = "";
        message(provider.connection ? `${provider.name} key replaced.` : `${provider.name} connected. Pick a model below.`);
        deps.changed?.(); await load();
      } catch (error) { fail(error); submit.disabled = false; }
    });
    box.append(form);
    // Models: suggestions, then the provider's own list on request.
    const list = make("ul", "provider-models");
    const models = live.get(provider.id);
    const search = make("input") as HTMLInputElement;
    search.placeholder = `Search ${provider.name} models`; search.value = query; search.className = "provider-search";
    search.oninput = () => { query = search.value; paint(); };
    const paint = () => {
      const needle = query.trim().toLowerCase();
      const rows = models ? models.filter(m => !needle || m.id.toLowerCase().includes(needle) || m.name.toLowerCase().includes(needle)).slice(0, 200)
        .map(m => modelRow(provider, m.id, [m.name !== m.id ? m.name : "", contextLabel(m.context), priceLabel(m)].filter(Boolean).join(" · ")))
        : provider.models.filter(m => !needle || m.toLowerCase().includes(needle)).map(m => modelRow(provider, m));
      list.replaceChildren(...(rows.length ? rows : [make("li", "muted", "No models match")]));
    };
    const tools = make("div", "provider-models-head");
    tools.append(make("strong", "", models ? `${models.length} models from ${provider.name}` : "Popular models"), search);
    const loadAll = button(models ? "Reload list" : "Load all models", "ghost small", async () => {
      loadAll.disabled = true; loadAll.textContent = "Loading…";
      try { live.set(provider.id, (await request<{ models: LiveModel[] }>(`/api/model-catalog/${encodeURIComponent(provider.id)}/models`, "GET")).models); render(); }
      catch (error) { fail(error); loadAll.disabled = false; loadAll.textContent = "Load all models"; }
    });
    tools.append(loadAll);
    const custom = make("form", "provider-custom-model");
    const typed = make("input") as HTMLInputElement; typed.placeholder = "Or type any model name"; typed.setAttribute("aria-label", "Model name");
    const go = make("button", "ghost small", "Use") as HTMLButtonElement; go.type = "submit";
    custom.append(typed, go);
    custom.addEventListener("submit", event => { event.preventDefault(); if (typed.value.trim() && provider.connection) void use(provider, typed.value.trim()); else if (!provider.connection) message(`Connect ${provider.name} first.`, true); });
    paint();
    box.append(tools, list, custom);
    return box;
  }

  function avatar(provider: CatalogProvider): HTMLElement {
    const letters = provider.name.replace(/[^A-Za-z0-9 ]/g, "").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
    const node = make("span", "provider-avatar", letters || "?");
    node.dataset.hue = String([...provider.id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 12);
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function customForm(): HTMLElement {
    const box = make("section", "provider-detail");
    box.append(make("strong", "", "Add your own provider"), make("p", "muted", "Any OpenAI-compatible API: a name, its base URL and a key (no key for servers on this computer)."));
    const form = make("form", "provider-connect");
    const name = make("input") as HTMLInputElement; name.placeholder = "Name, e.g. Company gateway";
    const base = make("input") as HTMLInputElement; base.placeholder = "https://api.example.com/v1";
    const key = make("input") as HTMLInputElement; key.type = "password"; key.placeholder = "API key"; key.autocomplete = "off";
    const add = make("button", "primary small", "Add provider") as HTMLButtonElement; add.type = "submit";
    form.append(name, base, key, add);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        const created = await request<CatalogProvider>("/api/model-catalog/custom", "POST", { name: name.value.trim(), base_url: base.value.trim(), api_key: key.value.trim() || undefined });
        message(`${created.name} added.`); selected = created.id; deps.changed?.(); await load();
      } catch (error) { fail(error); }
    });
    box.append(form);
    return box;
  }

  /** The chosen provider's panel opens below the grid; bring it into view. */
  function reveal(): void {
    const panel = root.querySelector?.(".provider-detail") as HTMLElement | null;
    panel?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  function render(): void {
    const head = make("div", "providers-head");
    head.append(make("p", "lead", current ? `Dikdik is using ${current}.` : "Connect a provider and pick a model to start."));
    const grid = make("div", "provider-grid");
    for (const provider of providers) {
      const card = make("button", "provider-card") as HTMLButtonElement;
      card.type = "button"; card.setAttribute("aria-pressed", String(provider.id === selected));
      const text = make("span", "provider-card-text");
      text.append(make("strong", "", provider.name), make("small", "", provider.connection ? "Connected" : provider.blurb));
      card.append(avatar(provider), text);
      if (provider.connection) card.dataset.connected = "true";
      card.onclick = () => { selected = selected === provider.id ? "" : provider.id; query = ""; render(); reveal(); };
      grid.append(card);
    }
    const plus = make("button", "provider-card provider-add") as HTMLButtonElement;
    plus.type = "button"; plus.setAttribute("aria-pressed", String(selected === "custom"));
    plus.append(make("span", "provider-avatar", "+"), make("span", "provider-card-text", "Custom provider"));
    plus.onclick = () => { selected = selected === "custom" ? "" : "custom"; render(); reveal(); };
    grid.append(plus);
    const chosen = providers.find(p => p.id === selected);
    root.replaceChildren(head, grid, ...(chosen ? [detail(chosen)] : selected === "custom" ? [customForm()] : []));
  }

  return async () => { try { await load(); } catch (error) { fail(error); } };
}
