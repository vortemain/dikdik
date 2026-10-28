/** Searchable chat model chooser. Provider discovery never changes the default model. */
import type { PaneDeps, ModelProfile } from "./models-pane";
import type { CatalogProvider, LiveModel } from "./providers-pane";
import { contextLabel } from "./providers-pane";
import { providerIcon, providerId } from "./icons";

export interface ModelPickerDeps extends PaneDeps {
  profiles: () => Promise<ModelProfile[]>;
  selected: () => string | null;
  pick: (choice: { id: string; name: string } | null) => void;
  manage: (provider?: string) => void;
}
export function matchesModel(query: string, ...values: string[]): boolean {
  return query.trim().toLowerCase().split(/\s+/).every(word => values.some(value => value.toLowerCase().includes(word)));
}

export function modelPicker(root: HTMLElement, anchor: HTMLElement, deps: ModelPickerDeps): { open(): Promise<void>; close(): void } {
  const { make, request } = deps;
  let profiles: ModelProfile[] = [], providers: CatalogProvider[] = [], chosen: CatalogProvider | null = null;
  let query = "", limit = 50, epoch = 0, loading = false, selecting = false, failure = "";
  const listings = new Map<string, LiveModel[]>();
  // A click can replace its row before it bubbles. Keep it inside the picker
  // even when its original target has already been detached from the DOM.
  root.addEventListener("click", event => event.stopPropagation());
  const control = (label: string, className: string, action: () => void): HTMLButtonElement => {
    const node = make("button", className, label); node.type = "button"; node.onclick = action; return node;
  };
  function position(): void {
    const box = anchor.getBoundingClientRect();
    const height = Math.min(520, Math.max(220, box.top - 18));
    root.style.maxHeight = `${height}px`;
    root.style.width = `${Math.min(376, innerWidth - 24)}px`;
    root.style.left = `${Math.max(12, Math.min(box.left, innerWidth - root.offsetWidth - 12))}px`;
    root.style.top = `${Math.max(12, box.top - root.getBoundingClientRect().height - 8)}px`;
  }
  function close(): void {
    epoch++; root.hidden = true; anchor.setAttribute("aria-expanded", "false");
  }
  globalThis.addEventListener("resize", () => { if (!root.hidden) position(); });
  function pick(profile: ModelProfile | null): void {
    deps.pick(profile ? { id: profile.id, name: profile.model } : null); close(); anchor.focus();
  }
  async function use(provider: CatalogProvider, model: LiveModel): Promise<void> {
    if (!provider.connection && !provider.no_key) { close(); deps.manage(provider.id); return; }
    if (selecting) return;
    const ticket = epoch; selecting = true; failure = ""; render();
    try {
      const saved = await request<{ profile: string }>(`/api/model-catalog/${encodeURIComponent(provider.id)}/use`, "POST", { model: model.id, default: false });
      if (ticket !== epoch) return;
      deps.pick({ id: saved.profile, name: model.id }); close(); anchor.focus();
    } catch (error) { if (ticket === epoch) failure = error instanceof Error ? error.message : "Could not select model"; }
    finally { if (ticket === epoch) { selecting = false; render(); } }
  }
  async function browse(provider: CatalogProvider, refresh = false): Promise<void> {
    chosen = provider; query = ""; limit = 50; failure = "";
    const ticket = ++epoch;
    if (provider.models_unavailable) { loading = false; render(true); return; }
    if (!provider.connection && !provider.public_models && !provider.no_key) { loading = false; render(true); return; }
    if (listings.has(provider.id) && !refresh) { render(true); return; }
    loading = true; render(true);
    try {
      const result = await request<{ models: LiveModel[] }>(`/api/model-catalog/${encodeURIComponent(provider.id)}/models`, "GET");
      listings.set(provider.id, result.models);
    } catch (error) { if (ticket === epoch) failure = error instanceof Error ? error.message : "Could not load models"; }
    finally { if (ticket === epoch) { loading = false; render(true); } }
  }
  function row(name: string, detail: string, provider: string, selected: boolean, action: () => void): HTMLButtonElement {
    const node = control("", "picker-row", action); node.dataset.modelChoice = "true";
    node.setAttribute("aria-pressed", String(selected)); node.title = [name, detail].filter(Boolean).join(" · ");
    const text = make("span", "picker-row-text"); text.append(make("strong", "", name));
    if (detail) text.append(make("small", "", detail));
    node.append(providerIcon(provider), text, make("span", selected ? "picker-mark selected" : "picker-mark", selected ? "✓" : ""));
    node.disabled = selecting;
    return node;
  }
  function render(focusSearch = false): void {
    if (root.hidden) return;
    const head = make("div", "picker-head");
    if (chosen) {
      const back = control("‹", "picker-back", () => { epoch++; chosen = null; query = ""; loading = false; failure = ""; render(true); });
      back.setAttribute("aria-label", "Back to providers"); head.append(back, providerIcon(chosen.id));
    }
    head.append(make("strong", "", chosen?.name ?? "Choose a model"));
    const dismiss = control("×", "picker-close", () => { close(); anchor.focus(); }); dismiss.setAttribute("aria-label", "Close model picker");
    head.append(dismiss);
    const searchWrap = make("div", "picker-search"), search = make("input");
    search.type = "search"; search.value = query; search.autocomplete = "off"; search.spellcheck = false;
    search.placeholder = chosen ? "Search this provider’s models" : "Search models or providers";
    search.setAttribute("aria-label", search.placeholder);
    searchWrap.append(make("span", "picker-search-icon", "⌕"), search);
    const results = make("div", "picker-results");
    const paint = () => {
      results.replaceChildren();
      if (chosen) {
        const provider = chosen, models = listings.get(provider.id), ready = !!provider.connection || provider.public_models || provider.no_key;
        if (provider.models_unavailable) results.append(make("p", "picker-notice", provider.models_unavailable), control("Manage provider", "ghost small picker-connect", () => { close(); deps.manage(provider.id); }));
        else if (loading) results.append(make("p", "picker-notice", "Loading models from provider…"));
        else if (!ready) {
          results.append(make("p", "picker-notice", `Connect ${provider.name} to browse the models available to your account.`),
            control("Connect provider", "primary small picker-connect", () => { close(); deps.manage(provider.id); }));
        } else if (failure) {
          results.append(make("p", "picker-notice error-text", failure), control("Try again", "ghost small picker-connect", () => { void browse(provider, true); }));
        } else if (models) {
          const matches = models.filter(model => matchesModel(query, model.id, model.name));
          const label = make("div", "picker-section"); label.append(make("span", "", `${matches.length} models`), control("Refresh", "ghost small", () => { void browse(provider, true); })); results.append(label);
          for (const model of matches.slice(0, limit)) {
            const profile = profiles.find(p => p.model === model.id && providerId(p.provider, p.endpoint) === provider.id);
            const item = row(model.name, [model.name !== model.id ? model.id : "", contextLabel(model.context)].filter(Boolean).join(" · "), provider.id,
              !!profile && deps.selected() === profile.id, () => { void use(provider, model); });
            if (model.chat_supported === false) { item.disabled = true; item.title = "This model does not support chat"; }
            else if (!provider.connection && !provider.no_key) { item.title = `Connect ${provider.name} to use ${model.id}`; item.lastElementChild!.textContent = "↗"; }
            results.append(item);
          }
          if (!matches.length) results.append(make("p", "picker-notice", "No matching models."));
          if (matches.length > limit) results.append(control(`Show ${Math.min(50, matches.length - limit)} more`, "picker-more", () => { limit += 50; paint(); position(); }));
        }
      } else {
        const fallback = profiles.find(p => p.is_default);
        const saved = profiles.filter(p => (query || p.id !== fallback?.id) && matchesModel(query, p.name, p.model, providerId(p.provider, p.endpoint)));
        if (!query && fallback) results.append(row(fallback.model, "Default · " + fallback.name, providerId(fallback.provider, fallback.endpoint), deps.selected() === null || deps.selected() === fallback.id, () => pick(null)));
        if (saved.length) results.append(make("p", "picker-section", "Saved models"));
        for (const profile of saved) results.append(row(profile.model, profile.name, providerId(profile.provider, profile.endpoint), deps.selected() === profile.id || deps.selected() === null && profile.is_default, () => pick(profile)));
        const shown = providers.filter(p => matchesModel(query, p.name, p.id));
        if (shown.length) results.append(make("p", "picker-section", "Browse providers"));
        for (const provider of shown) {
          const item = row(provider.name, provider.models_unavailable ? "Manual model IDs" : provider.connection ? "Connected · Browse all models" : provider.public_models ? "Browse public catalog" : provider.no_key ? "Browse local models" : "Connect to browse models", provider.id, false, () => { void browse(provider); });
          item.removeAttribute("aria-pressed");
          item.lastElementChild!.textContent = "›"; results.append(item);
        }
        if (!saved.length && !shown.length) results.append(make("p", "picker-notice", "No matching models or providers."));
        if (failure) results.append(make("p", "picker-notice error-text", failure));
      }
    };
    search.oninput = () => { query = search.value; limit = 50; paint(); };
    const foot = make("div", "picker-footer");
    foot.append(make("span", "", "For your next messages"), control(chosen && !chosen.connection && !chosen.no_key ? "Connect provider ↗" : "Manage providers ↗", "ghost small", () => { close(); deps.manage(chosen?.id); }));
    root.replaceChildren(head, searchWrap, results, foot); paint(); position();
    if (focusSearch) search.focus();
  }
  root.addEventListener("keydown", (event: KeyboardEvent) => {
    if (event.key === "Escape") { event.preventDefault(); close(); anchor.focus(); return; }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const items = [...root.querySelectorAll<HTMLButtonElement>("[data-model-choice]:not(:disabled)")];
    if (!items.length) return;
    event.preventDefault(); const index = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  });
  return { close, async open() {
    if (!root.hidden) { close(); return; }
    const ticket = ++epoch; chosen = null; query = ""; failure = ""; selecting = false;
    listings.clear();  // Keys/accounts can change while the picker is closed.
    root.hidden = false; root.className = "model-picker"; root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Choose a model"); anchor.setAttribute("aria-expanded", "true");
    root.replaceChildren(make("p", "picker-notice", "Loading your models…")); position();
    try {
      const [saved, catalog] = await Promise.all([deps.profiles(), request<{ order: string[]; providers: Record<string, CatalogProvider> }>("/api/model-catalog", "GET")]);
      if (ticket !== epoch) return;
      profiles = saved; providers = (catalog.order ?? Object.keys(catalog.providers)).map(id => catalog.providers[id]!).filter(Boolean);
      providers.sort((a, b) => Number(!!b.connection) - Number(!!a.connection)); render(true);
    } catch (error) { if (ticket === epoch) { profiles = []; providers = []; failure = error instanceof Error ? error.message : "Could not load models"; render(true); } }
  } };
}
