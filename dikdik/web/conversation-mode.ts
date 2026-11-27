// Chat vs Code: the two tabs at the top of the sidebar.
//
// Chat is a conversation that belongs to no project — research, analysis, documents
// and data, with its own private folder for attachments and created files — so a
// new chat never asks for (or touches) the open project. Code is the agent that
// works in the project folder. The tab decides what "New" starts, which history the
// sidebar lists, and whether project controls are shown at all.
//
// Owns the conversationKind state and its localStorage persistence. The host
// (app.ts) supplies DOM accessors and the functions that re-apply the composer and
// history, so this module never reaches into app state directly.

export type ConversationKind = "chat" | "code";

export type SelectEl = (id: string) => HTMLSelectElement;
export type InputEl = (id: string) => HTMLInputElement;
export type Elem = (id: string) => HTMLElement;
export type SetClass = (node: HTMLElement, name: string, on: boolean) => void;

export interface ConversationModeDeps {
  select: SelectEl;
  input: InputEl;
  el: Elem;
  setClass: SetClass;
  // Host-side UI re-apply, called after the switch changes the relevant fields.
  applyRunMode: () => void;
  syncComposer: () => void;
  // Start a fresh composer (no conversation selected).
  newTask: () => void;
  // Status line for the toast that confirms the switch.
  message: (text: string, error?: boolean) => void;
  // The open conversation's kind, or null when none is open.
  selectedKind?: () => ConversationKind | null;
  // Re-render the sidebar list and the welcome screen for the new tab.
  refresh?: () => void;
}

export interface ConversationMode {
  isChat: () => boolean;
  kind: () => ConversationKind;
  apply: () => void;
  /** Switch tabs. `fresh` starts a new conversation when the open one is of the other kind. */
  set: (next: ConversationKind, fresh?: boolean) => void;
  /** Wire the #conversation-kind button listeners. Call once at boot. */
  mount: () => void;
}

/** A conversation listed under the Chat tab: stored in the chat scope, or an older chat-mode run. */
export function isChatConversation(task: { mode?: string; scope?: string }): boolean {
  return task.scope === "chat" || task.mode === "chat";
}

export function createConversationMode(deps: ConversationModeDeps): ConversationMode {
  // Chat is the front door: anyone can start talking without choosing a project.
  let kind: ConversationKind = "chat";
  try {
    const saved = localStorage.getItem("dikdik.conversation.v2");
    if (saved === "chat" || saved === "code") kind = saved;
  } catch { void 0; }

  function isChat(): boolean { return kind === "chat"; }

  function text(id: string, value: string): void {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  }
  function hide(id: string, hidden: boolean): void {
    const node = document.getElementById(id);
    if (node) node.hidden = hidden;
  }

  function apply(): void {
    const chat = isChat();
    if (typeof document.querySelectorAll === "function") {
      for (const button of document.querySelectorAll<HTMLButtonElement>("#conversation-kind button")) {
        const on = button.dataset.conversation === kind;
        deps.setClass(button, "on", on);
        button.setAttribute("aria-pressed", String(on));
      }
    }
    if (document.body?.dataset) document.body.dataset.conversation = kind;
    // Labels follow the tab.
    text("new-task-label", chat ? "New chat" : "New task");
    text("welcome-new-key", chat ? "New chat" : "New task");
    text("task-search-label", chat ? "Search chats" : "Search tasks");
    const search = document.getElementById("task-search") as HTMLInputElement | null;
    if (search) search.placeholder = chat ? "Search chats" : "Search tasks";
    const rail = document.getElementById("rail-new");
    if (rail) { rail.title = `${chat ? "New chat" : "New task"} (Ctrl ⇧ O)`; rail.setAttribute("aria-label", chat ? "New chat" : "New task"); }
    const title = document.getElementById("welcome-title");
    if (title) title.textContent = chat ? "What would you like to explore?" : "What should we build?";
    // A chat has no project: hide every project control instead of greying it out.
    hide("project-picker", chat);
    hide("topbar-project", chat);
    for (const node of document.querySelectorAll?.<HTMLElement>(".topbar-sep, .welcome-project") ?? []) node.hidden = chat;
    // Composer controls that only mean something for code.
    hide("env-pill", chat);
    hide("run-mode-pill", chat);
    hide("mode-pill", chat);
    hide("verification-pill", chat);
    deps.select("run-mode").hidden = chat;
    deps.select("mode").hidden = chat;
    deps.select("verification").hidden = chat;
    hide("options-toggle", chat);
    const approvals = deps.el("approvals");
    if (chat) {
      deps.input("external").checked = true;   // a chat always talks to the model
      hide("external-field", true);
      hide("execution-field", true);           // the chat's own Commands switch replaces it
      hide("browser-field", true);
      hide("secrets-field", true);
      approvals.hidden = false;
      deps.select("verification").value = "syntax";
      deps.input("browser-approval").checked = false;
      deps.input("execution").disabled = false;
      deps.input("execution").required = false;
      const execution = document.getElementById("execution-field");
      if (execution) execution.title = "Let Dikdik run commands (Python scripts, data tools) in this chat's own folder. You approve each one.";
      deps.applyRunMode();
    } else {
      hide("external-field", false);
      hide("execution-field", false);
      hide("browser-field", false);
      hide("secrets-field", false);
      approvals.hidden = false;
    }
    deps.syncComposer();
    deps.refresh?.();
  }

  function set(next: ConversationKind, fresh = true): void {
    if (next === kind) return;
    kind = next;
    try { localStorage.setItem("dikdik.conversation.v2", kind); } catch { void 0; }
    const open = deps.selectedKind?.() ?? null;
    if (fresh && open !== null && open !== kind) deps.newTask();
    apply();
  }

  function mount(): void {
    if (typeof document.querySelectorAll !== "function") return;
    for (const button of document.querySelectorAll<HTMLButtonElement>("#conversation-kind button")) {
      button.addEventListener("click", () => set(button.dataset.conversation === "code" ? "code" : "chat"));
    }
  }

  return { isChat, kind: () => kind, apply, set, mount };
}
