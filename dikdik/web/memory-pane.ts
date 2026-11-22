// Settings → Memory: the durable user and project memory, background review, and the skill
// proposals review leaves for the user. Memory and proposals are agent-written text, shown as plain
// text only, never as HTML.
import type { PaneDeps } from "./models-pane";
import { confirmDialog } from "./dialogs";

export type MemoryTarget = "user" | "project";
export interface MemoryEntry { id: number; content: string; source: string; updated: number }
export interface MemoryStore { entries: MemoryEntry[]; used: number; limit: number }
export interface MemoryView { user: MemoryStore; project: MemoryStore | null }
export interface SkillProposal { id: string; created: number; task_id: string | null; name: string; description: string; content: string; status: string }
interface AppFlags { memory?: boolean; background_review?: boolean }

const STORES: Record<MemoryTarget, { title: string; hint: string }> = {
  user: { title: "About you", hint: "Who you are and how you like to work. Used in every conversation." },
  project: { title: "This project", hint: "Facts, conventions and decisions for the open project. Used in its code tasks." },
};

/** One entry per line, spaces collapsed the way the server stores them; blank and repeated lines dropped. */
export function memoryLines(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).map(line => line.split(/\s+/).filter(Boolean).join(" ")).filter(Boolean))];
}
/** How full a store would be with these entries, stated in words. */
export function memoryUsage(entries: readonly string[], limit: number): { used: number; over: boolean; label: string } {
  const used = entries.reduce((sum, entry) => sum + entry.length, 0);
  const format = (value: number) => value.toLocaleString("en-US");
  return { used, over: used > limit, label: `${format(used)} of ${format(limit)} characters${used > limit ? ` — ${format(used - limit)} over the limit` : ""}` };
}

export function memoryPane(root: HTMLElement, deps: PaneDeps): () => Promise<void> {
  const { request, make, message } = deps;
  let view: MemoryView | null = null, proposals: SkillProposal[] = [], flags: AppFlags = {};
  const fail = (error: unknown) => message(error instanceof Error ? error.message : "Something went wrong", true);
  const button = (label: string, className: string, onclick: () => void) => {
    const node = make("button", className, label); node.type = "button"; node.onclick = onclick;
    return node;
  };
  async function refresh(): Promise<void> {
    try {
      [view, proposals, flags] = await Promise.all([
        request<MemoryView>("/api/memory", "GET"),
        request<{ proposals: SkillProposal[] }>("/api/skill-proposals?status=pending", "GET").then(reply => reply.proposals),
        request<AppFlags>("/api/settings/app", "GET"),
      ]);
      render();
    } catch (error) { root.replaceChildren(make("p", "error-text", `Could not load memory: ${error instanceof Error ? error.message : "unknown error"}`)); }
  }
  async function act(work: () => Promise<unknown>, done: string): Promise<void> {
    try { await work(); message(done); await refresh(); deps.changed?.(); } catch (error) { fail(error); }
  }

  function flag(key: keyof AppFlags, title: string, text: string): HTMLElement {
    const row = make("div", "setting-row");
    const words = make("div"); words.append(make("h4", "", title), make("p", "", text));
    const label = make("label", "check"); const box = make("input"); box.type = "checkbox";
    box.id = `memory-flag-${key}`; box.checked = flags[key] !== false;
    box.onchange = () => { void act(() => request("/api/settings/app", "PATCH", { [key]: box.checked }), `${title} ${box.checked ? "on" : "off"}.`); };
    label.append(box, document.createTextNode("On"));
    row.append(words, label);
    return row;
  }

  function store(target: MemoryTarget, data: MemoryStore): HTMLElement {
    const box = make("section", "pane-block memory-store");
    const id = `memory-${target}`;
    box.setAttribute("aria-labelledby", `${id}-title`);
    const head = make("div", "block-head");
    const title = make("h4", "", STORES[target].title); title.id = `${id}-title`;
    head.append(title);
    const saved = data.entries.map(entry => entry.content).join("\n");
    const meter = make("progress", "memory-meter"); meter.max = data.limit;
    meter.setAttribute("aria-label", `${STORES[target].title} memory used`);
    const usage = make("p", "memory-usage"); usage.id = `${id}-usage`; usage.setAttribute("aria-live", "polite");
    const label = make("label", "", "One fact per line"); label.htmlFor = id;
    const field = make("textarea", "memory-text"); field.id = id; field.rows = Math.min(12, Math.max(4, data.entries.length + 1));
    field.value = saved; field.spellcheck = true; field.placeholder = target === "user" ? "Prefers short answers with examples" : "Tests run with python -m unittest";
    field.setAttribute("aria-describedby", `${id}-usage`);
    const save = button("Save", "primary small", () => {
      void act(() => request(`/api/memory/${target}`, "PATCH", { entries: memoryLines(field.value) }), `${STORES[target].title} memory saved.`);
    });
    const revert = button("Undo changes", "ghost small", () => { field.value = saved; update(); field.focus(); });
    const clear = button("Clear", "ghost small danger", () => {
      void confirmDialog(`Clear “${STORES[target].title}” memory?`, `This deletes all ${data.entries.length} saved ${data.entries.length === 1 ? "fact" : "facts"}. It cannot be undone.`, "Clear memory")
        .then(yes => { if (yes) void act(() => request(`/api/memory/${target}`, "DELETE"), `${STORES[target].title} memory cleared.`); });
    });
    clear.disabled = !data.entries.length;
    function update(): void {
      const lines = memoryLines(field.value);
      const state = memoryUsage(lines, data.limit);
      meter.value = Math.min(state.used, data.limit);
      usage.textContent = state.label;
      usage.classList.toggle("error-text", state.over);
      const changed = lines.join("\n") !== saved;
      save.disabled = !changed || state.over;
      revert.disabled = !changed;
    }
    field.oninput = update;
    update();
    const actions = make("div", "row memory-actions"); actions.append(save, revert, clear);
    box.append(head, make("p", "hint", STORES[target].hint), meter, usage, label, field, actions);
    return box;
  }

  function proposalList(): HTMLElement {
    const box = make("section", "pane-block");
    box.setAttribute("aria-labelledby", "memory-proposals-title");
    const head = make("div", "block-head");
    const title = make("h4", "", "Proposed skills"); title.id = "memory-proposals-title";
    head.append(title, make("span", "m4-badge", `${proposals.length} waiting`));
    box.append(head, make("p", "hint", "Background review can suggest a skill after a conversation. Nothing is added until you approve it; an approved skill becomes a personal skill you can edit or turn off under Skills."));
    if (!proposals.length) { box.append(make("p", "hint", "No proposals waiting.")); return box; }
    const list = make("ul", "m4-list");
    for (const proposal of proposals) {
      const row = make("li", "m4-row memory-proposal");
      row.append(make("p", "m4-title", proposal.name), make("p", "m4-meta", proposal.description || "No description."));
      const details = make("details"); details.append(make("summary", "", "Show the skill"), make("pre", "skill-content", proposal.content));
      const actions = make("div", "row");
      const approve = button("Approve", "primary small", () => {
        void act(() => request(`/api/skill-proposals/${encodeURIComponent(proposal.id)}/approve`, "POST"), `Added “${proposal.name}” to your skills.`);
      });
      approve.setAttribute("aria-label", `Approve skill ${proposal.name}`);
      const reject = button("Reject", "ghost small", () => {
        void act(() => request(`/api/skill-proposals/${encodeURIComponent(proposal.id)}/reject`, "POST"), `Rejected “${proposal.name}”.`);
      });
      reject.setAttribute("aria-label", `Reject skill ${proposal.name}`);
      actions.append(approve, reject);
      row.append(details, actions);
      list.append(row);
    }
    box.append(list);
    return box;
  }

  function render(): void {
    if (!view) return;
    const settings = make("section", "pane-block memory-settings");
    settings.append(
      flag("memory", "Use memory", "Conversations read these facts and can save new ones. Saved text is treated as data, never as instructions or permissions."),
      flag("background_review", "Background review", "After a conversation, your router’s fast model looks back and may save a fact or propose a skill. Skipped when no fast model is set."),
    );
    const project = view.project ? store("project", view.project)
      : (() => { const box = make("section", "pane-block"); box.append(make("h4", "", STORES.project.title), make("p", "hint", "Open a project to see and edit its memory. Chats have no project memory.")); return box; })();
    root.replaceChildren(settings, store("user", view.user), project, proposalList());
  }
  return refresh;
}
