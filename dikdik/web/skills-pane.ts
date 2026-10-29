// Settings → Skills: your skills (search, filters, details), installing, marketplaces and their catalog.
// Built with explicit DOM nodes; skill text from disk or a marketplace is shown as plain text, never HTML.
import type { PaneDeps } from "./models-pane";
import { confirmDialog } from "./dialogs";

export interface SkillRow {
  id: string; name: string; description: string; scope: "personal" | "project" | "installed"; enabled: boolean; path: string;
  category?: string; version?: string; license?: string; triggers?: string; source?: string; resolved_ref?: string;
}
export interface Marketplace { id: string; name: string; source: string; repo_path: string | null; synced: number | null; status: string | null }
export interface CatalogEntry { marketplace: string; marketplace_name: string; name: string; description: string; category: string; repo_path: string; installed: boolean }
export interface SkillFilter { query: string; state: "" | "on" | "off"; source: "" | SkillRow["scope"]; category: string }

/** Skills matching the search box and the facet choices. */
export function filterSkills(rows: readonly SkillRow[], filter: SkillFilter): SkillRow[] {
  const query = filter.query.trim().toLowerCase();
  return rows.filter(row => (!query || [row.name, row.description, row.category ?? "", row.triggers ?? ""].join(" ").toLowerCase().includes(query))
    && (!filter.state || (filter.state === "on") === row.enabled)
    && (!filter.source || row.scope === filter.source)
    && (!filter.category || (row.category ?? "") === filter.category));
}
/** Counts per facet value, so each choice shows how many skills it keeps. */
export function facetCounts(rows: readonly SkillRow[]): { source: Record<string, number>; category: Record<string, number>; on: number; off: number } {
  const source: Record<string, number> = {}, category: Record<string, number> = {};
  for (const row of rows) {
    source[row.scope] = (source[row.scope] ?? 0) + 1;
    if (row.category) category[row.category] = (category[row.category] ?? 0) + 1;
  }
  return { source, category, on: rows.filter(row => row.enabled).length, off: rows.filter(row => !row.enabled).length };
}
/** Sources that are fetched over the internet need an approval at the moment of install or sync. */
export function isNetworkSource(source: string): boolean {
  return /^(https:\/\/|github:)/i.test(source.trim());
}

const SOURCE_LABELS: Record<string, string> = { personal: "Personal", project: "This project", installed: "Installed" };

export function skillsPane(root: HTMLElement, deps: PaneDeps & { useSkill: (name: string) => void }): () => Promise<void> {
  const { request, make, message } = deps;
  let rows: SkillRow[] = [], markets: Marketplace[] = [], catalog: CatalogEntry[] = [];
  const filter: SkillFilter = { query: "", state: "", source: "", category: "" };
  const fail = (error: unknown) => message(error instanceof Error ? error.message : "Something went wrong", true);
  const button = (label: string, className = "secondary", onclick?: () => void) => {
    const node = make("button", className, label); node.type = "button";
    if (onclick) node.onclick = onclick;
    return node;
  };
  const approval = (label: string) => {
    const wrap = make("label", "check approval-check"); const box = make("input"); box.type = "checkbox";
    wrap.append(box, document.createTextNode(label));
    return { wrap, box };
  };
  async function act(work: () => Promise<unknown>, done: string): Promise<void> {
    try { await work(); if (done) message(done); await refresh(); deps.changed?.(); } catch (error) { fail(error); }
  }
  async function refresh(): Promise<void> {
    try {
      [rows, markets, catalog] = await Promise.all([
        request<SkillRow[]>("/api/skills", "GET"), request<Marketplace[]>("/api/skills/marketplaces", "GET"), request<CatalogEntry[]>("/api/skills/catalog", "GET"),
      ]);
      render();
    } catch (error) { root.replaceChildren(make("p", "error-text", `Could not load skills: ${error instanceof Error ? error.message : "unknown error"}`)); }
  }

  // --- detail view -------------------------------------------------------------------
  function dialog(): HTMLDialogElement {
    let node = document.getElementById("skill-dialog") as HTMLDialogElement | null;
    if (!node) {
      node = make("dialog", "small-dialog wide-dialog skill-dialog"); node.id = "skill-dialog";
      node.addEventListener("click", event => { if (event.target === node) node!.close(); });
      document.body.append(node);
    }
    return node;
  }
  async function openDetail(row: SkillRow): Promise<void> {
    let detail: SkillRow & { content: string };
    try { detail = await request<SkillRow & { content: string }>(`/api/skills/${encodeURIComponent(row.id)}`, "GET"); }
    catch (error) { fail(error); return; }
    const box = dialog();
    const head = make("div", "settings-head");
    head.append(make("h3", "", detail.name), button("Close", "ghost small", () => box.close()));
    const body = make("div", "info-body");
    const pills = make("p", "skill-pills");
    for (const text of [SOURCE_LABELS[detail.scope], detail.category, detail.version && `v${detail.version}`, detail.license, detail.triggers && `triggers: ${detail.triggers}`].filter(Boolean) as string[]) pills.append(make("span", "m4-badge", text));
    const toggle = make("label", "check"); const on = make("input"); on.type = "checkbox"; on.checked = detail.enabled;
    toggle.append(on, document.createTextNode("Enabled — the agent reads it in new tasks"));
    on.onchange = () => { void act(() => request(`/api/skills/${encodeURIComponent(detail.id)}`, "PUT", { enabled: on.checked }), on.checked ? "Skill on." : "Skill off."); use.disabled = !on.checked; };
    const use = button("Use skill", "primary small", () => { box.close(); deps.useSkill(detail.id); });
    use.disabled = !detail.enabled;
    const actions = make("div", "row");
    actions.append(use);
    if (detail.scope !== "installed") {
      actions.append(button("Copy path", "ghost small", () => {
        void navigator.clipboard?.writeText(detail.path).then(() => message("Copied to clipboard")).catch(() => message("Could not copy", true));
      }));
    } else if (detail.source) body.append(make("p", "hint", `From ${detail.source}${detail.resolved_ref ? ` at ${detail.resolved_ref.slice(0, 12)}` : ""}`));
    const content = make("pre", "skill-content", detail.content);
    body.prepend(pills, make("p", "", detail.description || "No description."), toggle, actions);
    body.append(content);
    box.replaceChildren(head, body);
    if (!box.open) box.showModal();
  }

  // --- your skills -----------------------------------------------------------------------
  function facet(label: string, value: string, count: number, current: string, set: (value: string) => void): HTMLButtonElement {
    const chip = make("button", "tag-chip", `${label} ${count}`); chip.type = "button";
    chip.setAttribute("aria-pressed", String(current === value));
    chip.disabled = count === 0 && current !== value;
    chip.onclick = () => { set(current === value ? "" : value); render(); };
    return chip;
  }
  function yourSkills(): HTMLElement {
    const box = make("section", "pane-block");
    const head = make("div", "block-head"); head.append(make("h4", "", "Your skills")); box.append(head);
    const search = make("input"); search.type = "search"; search.placeholder = "Search skills"; search.value = filter.query;
    search.setAttribute("aria-label", "Search skills");
    search.oninput = () => { filter.query = search.value; drawCards(); };
    const counts = facetCounts(rows);
    const facets = make("div", "tag-chips skill-facets");
    facets.append(facet("On", "on", counts.on, filter.state, v => { filter.state = v as SkillFilter["state"]; }),
      facet("Off", "off", counts.off, filter.state, v => { filter.state = v as SkillFilter["state"]; }),
      ...Object.entries(SOURCE_LABELS).map(([key, label]) => facet(label, key, counts.source[key] ?? 0, filter.source, v => { filter.source = v as SkillFilter["source"]; })),
      ...Object.entries(counts.category).map(([key, count]) => facet(key, key, count, filter.category, v => { filter.category = v; })));
    const cards = make("ul", "skill-cards");
    function drawCards(): void {
      const shown = filterSkills(rows, filter);
      cards.replaceChildren(...shown.map(row => {
        const card = make("li", "skill-card");
        card.tabIndex = 0; card.setAttribute("role", "button");
        card.onclick = event => { if (!(event.target as HTMLElement).closest("input, label")) void openDetail(row); };
        card.onkeydown = event => { if ((event.key === "Enter" || event.key === " ") && event.target === card) { event.preventDefault(); void openDetail(row); } };
        const top = make("div", "skill-card-top");
        top.append(make("strong", "", row.name));
        const toggle = make("label", "switch"); const on = make("input"); on.type = "checkbox"; on.checked = row.enabled;
        on.setAttribute("aria-label", `${row.enabled ? "Turn off" : "Turn on"} ${row.name}`);
        on.onchange = () => { void act(() => request(`/api/skills/${encodeURIComponent(row.id)}`, "PUT", { enabled: on.checked }), on.checked ? "Skill on." : "Skill off."); };
        toggle.append(on);
        top.append(toggle);
        const pills = make("p", "skill-pills");
        pills.append(make("span", "m4-badge", SOURCE_LABELS[row.scope] ?? row.scope));
        if (row.category) pills.append(make("span", "m4-badge muted-badge", row.category));
        card.append(top, make("p", "skill-desc", row.description || "No description."), pills);
        if (row.scope === "installed") {
          const actions = make("div", "m4-actions");
          if (row.source && row.source !== "local") actions.append(button("Refresh", "secondary", async () => {
            const network = isNetworkSource(row.source ?? "");
            if (network && !await confirmDialog(`Refresh ${row.name}?`, `Fetch this skill again from ${row.source}.`, "Fetch skill")) return;
            void act(() => request(`/api/skills/${encodeURIComponent(row.id)}/refresh`, "POST", { approve_network: network }), "Skill refreshed.");
          }));
          actions.append(button("Uninstall", "secondary", () => { void act(() => request(`/api/skills/${encodeURIComponent(row.id)}`, "DELETE"), "Skill uninstalled."); }));
          actions.onclick = event => event.stopPropagation();
          card.append(actions);
        }
        return card;
      }));
      if (!shown.length) cards.replaceChildren(make("li", "hint", rows.length ? "No skill matches." : "No skills yet. Write one above, or install one below."));
    }
    drawCards();
    box.append(search, facets, cards);
    return box;
  }

  // --- installing, marketplaces, catalog -----------------------------------------------------
  function installBlock(): HTMLElement {
    const box = make("section", "pane-block");
    const head = make("div", "block-head"); head.append(make("h4", "", "Install a skill")); box.append(head);
    box.append(make("p", "hint", "From a folder on this machine, a Git repository (https:// or file://) or github:owner/repo. Nothing in it is run; only its files are copied."));
    const form = make("form", "skill-form");
    const grid = make("div", "skill-fields");
    const field = (label: string, id: string, placeholder: string) => {
      const l = make("label", "", label); l.htmlFor = id; const i = make("input"); i.id = id; i.placeholder = placeholder; i.autocomplete = "off";
      grid.append(l, i); return i;
    };
    const source = field("Source", "skill-source", "C:\\skills\\tdd  ·  github:owner/repo  ·  https://…");
    const path = field("Folder in source", "skill-repo-path", "Optional, e.g. skills/tdd");
    const ref = field("Version", "skill-ref", "Optional branch, tag or commit");
    const ok = approval("I approve fetching this from the internet");
    ok.wrap.hidden = true;
    source.oninput = () => { ok.wrap.hidden = !isNetworkSource(source.value); };
    const go = button("Install", "primary small"); go.type = "submit";
    form.append(grid, ok.wrap, go);
    form.onsubmit = event => {
      event.preventDefault();
      const network = isNetworkSource(source.value);
      if (network && !ok.box.checked) { message("Approve fetching from the internet first.", true); return; }
      void act(async () => {
        const done = await request<{ name: string }>("/api/skills/install", "POST", { source: source.value, repo_path: path.value.trim() || null, ref: ref.value.trim() || null, approve_network: network && ok.box.checked });
        message(`Installed “${done.name}”.`);
      }, "");
    };
    box.append(form);
    return box;
  }
  function marketBlock(): HTMLElement {
    const box = make("section", "pane-block");
    const head = make("div", "block-head"); head.append(make("h4", "", "Marketplaces")); box.append(head);
    box.append(make("p", "hint", "Repositories of skills you add yourself. Sync copies one here so its skills can be browsed and installed offline."));
    const list = make("ul", "m4-list");
    for (const market of markets) {
      const row = make("li", "m4-row");
      const title = make("p", "m4-title"); title.append(make("span", "m4-strong", market.name));
      if (market.status?.startsWith("error")) title.append(make("span", "m4-badge warn-badge", "Last sync failed"));
      row.append(title, make("span", "m4-meta", `${market.source}${market.repo_path ? ` · ${market.repo_path}` : ""} · ${market.synced ? `synced ${new Date(market.synced * 1000).toLocaleString()}` : "never synced"}`));
      if (market.status?.startsWith("error")) row.append(make("span", "m4-meta", market.status));
      const actions = make("span", "m4-actions");
      actions.append(button("Sync", "secondary", async () => {
        const network = isNetworkSource(market.source);
        if (network && !await confirmDialog(`Fetch ${market.name}?`, `Download this marketplace from ${market.source}.`, "Fetch marketplace")) return;
        void act(async () => {
          const result = await request<{ status: string }>(`/api/skills/marketplaces/${market.id}/sync`, "POST", { approve_network: network });
          message(result.status === "ok" ? "Synced." : `Sync failed; the previous copy is kept. ${result.status}`, result.status !== "ok");
        }, "");
      }), button("Remove", "secondary", () => { void act(() => request(`/api/skills/marketplaces/${market.id}`, "DELETE"), "Marketplace removed."); }));
      row.append(actions);
      list.append(row);
    }
    if (!markets.length) list.append(make("li", "hint", "No marketplaces yet."));
    const form = make("form", "inline-form");
    const name = make("input"); name.placeholder = "Name"; name.setAttribute("aria-label", "Marketplace name");
    const source = make("input"); source.placeholder = "github:owner/repo or a folder"; source.setAttribute("aria-label", "Marketplace source");
    const folder = make("input"); folder.placeholder = "Skills folder (optional)"; folder.setAttribute("aria-label", "Skills folder in the marketplace");
    const add = button("Add marketplace", "small"); add.type = "submit";
    form.append(name, source, folder, add);
    form.onsubmit = event => { event.preventDefault(); void act(() => request("/api/skills/marketplaces", "POST", { name: name.value, source: source.value, repo_path: folder.value.trim() || null }), "Marketplace added. Sync it to browse its skills."); };
    box.append(list, form);
    if (catalog.length) {
      box.append(make("h4", "catalog-head", "Available to install"));
      const entries = make("ul", "skill-cards");
      for (const entry of catalog) {
        const card = make("li", "skill-card");
        const top = make("div", "skill-card-top"); top.append(make("strong", "", entry.name));
        top.append(entry.installed ? make("span", "m4-badge", "Installed") : button("Install", "small", () => {
          void act(() => request("/api/skills/catalog/install", "POST", { marketplace: entry.marketplace, repo_path: entry.repo_path }), `Installed “${entry.name}”.`);
        }));
        const pills = make("p", "skill-pills"); pills.append(make("span", "m4-badge muted-badge", entry.marketplace_name));
        if (entry.category) pills.append(make("span", "m4-badge muted-badge", entry.category));
        card.append(top, make("p", "skill-desc", entry.description || "No description."), pills);
        entries.append(card);
      }
      box.append(entries);
    }
    return box;
  }

  function render(): void {
    root.replaceChildren(yourSkills(), installBlock(), marketBlock());
  }
  return refresh;
}
