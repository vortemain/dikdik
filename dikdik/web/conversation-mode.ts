// Chat vs Code: one segmented switch in the sidebar that decides whether the
// next message starts a read-only research conversation or a workspace edit.
//
// Owns the conversationKind state and its localStorage persistence. The host
// (app.ts) supplies the cross-cutting DOM accessors and the functions that
// re-apply the composer/run-mode UI, so this module never reaches into app
// state directly. isChat() is the single read point used by composerRequest.

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
  // Start a fresh composer task when entering chat mode.
  newTask: () => void;
  // Status line for the toast that confirms the switch.
  message: (text: string, error?: boolean) => void;
}

export interface ConversationMode {
  isChat: () => boolean;
  kind: () => ConversationKind;
  apply: () => void;
  /** Wire the #conversation-kind button listeners. Call once at boot. */
  mount: () => void;
}

export function createConversationMode(deps: ConversationModeDeps): ConversationMode {
  let kind: ConversationKind = "code";
  try {
    const saved = localStorage.getItem("dikdik.conversation");
    if (saved === "chat" || saved === "code") kind = saved;
  } catch { void 0; }

  function isChat(): boolean { return kind === "chat"; }

  function apply(): void {
    if (typeof document.querySelectorAll === "function") {
      for (const button of document.querySelectorAll<HTMLButtonElement>("#conversation-kind button")) {
        deps.setClass(button, "on", button.dataset.conversation === kind);
        button.setAttribute("aria-pressed", String(button.dataset.conversation === kind));
      }
    }
    const chat = isChat();
    // Hide the entire pill wrappers (label + icon + select), not just the selects,
    // so icons don't float over empty space in chat mode.
    const runModePill = document.getElementById("run-mode-pill");
    const modePill = document.getElementById("mode-pill");
    const verificationPill = document.getElementById("verification-pill");
    if (runModePill) runModePill.hidden = chat;
    if (modePill) modePill.hidden = chat;
    if (verificationPill) verificationPill.hidden = chat;
    deps.select("run-mode").hidden = chat;
    deps.select("mode").hidden = chat;
    deps.select("verification").hidden = chat;
    // The options toggle (gear icon) is code-mode only — chat has no options panel.
    const optionsToggle = document.getElementById("options-toggle");
    if (optionsToggle) optionsToggle.hidden = chat;
    // Approvals: chat mode shows only "Run commands on this computer" (enables shell).
    // The other toggles (share code, browser, secrets) are set automatically for chat.
    const approvals = deps.el("approvals");
    const externalField = document.getElementById("external-field");
    const executionField = document.getElementById("execution-field");
    const browserField = document.getElementById("browser-field");
    const secretsField = document.getElementById("secrets-field");
    if (chat) {
      // Chat always shares code with the model (it's a research conversation).
      deps.input("external").checked = true;
      if (externalField) externalField.hidden = true;  // always on, no toggle needed
      if (executionField) executionField.hidden = false;  // show "Run commands" toggle
      if (browserField) browserField.hidden = true;  // no browser in chat
      if (secretsField) secretsField.hidden = true;  // no secrets in chat
      approvals.hidden = false;  // show the approvals section with just execution
      deps.select("verification").value = "syntax";
      deps.input("browser-approval").checked = false;
      deps.applyRunMode();
    } else {
      if (externalField) externalField.hidden = false;
      if (executionField) executionField.hidden = false;
      if (browserField) browserField.hidden = false;
      if (secretsField) secretsField.hidden = false;
      approvals.hidden = false;
    }
    deps.syncComposer();
  }

  function mount(): void {
    if (typeof document.querySelectorAll !== "function") return;
    for (const button of document.querySelectorAll<HTMLButtonElement>("#conversation-kind button")) {
      button.addEventListener("click", () => {
        const next: ConversationKind = button.dataset.conversation === "chat" ? "chat" : "code";
        if (next === kind) return;
        kind = next;
        try { localStorage.setItem("dikdik.conversation", kind); } catch { void 0; }
        apply();
        if (next === "chat") deps.newTask();
      });
    }
  }

  return { isChat, kind: () => kind, apply, mount };
}