// "Ask about this file" — a side panel that answers questions about the open
// document without touching the main conversation.
//
// Owns its own state (the per-file Q&A threads, the chat task id used when no
// conversation is open, the busy flag). The host (app.ts) supplies a small
// dependency object so this module never reaches into app state directly.
//
// Server-provided answer text is rendered through the host's richText() so any
// HTML in it stays bounded to the same allow-list as chat messages.

import type { TaskSummary } from "./contracts";

export type Make = <K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) => HTMLElementTagNameMap[K];
export type ApiFn = <T>(path: string, body?: unknown) => Promise<T>;
/** Renders untrusted markdown answer text into a safe DOM fragment (matches app.ts's richText). */
export type RichTextFn = (text: string) => HTMLElement;

export interface AskDrawerDeps {
  make: Make;
  api: ApiFn;
  /** Render untrusted markdown/answer text into safe DOM (same as chat). */
  richText: RichTextFn;
  // Element accessors (the host owns the DOM nodes):
  view: () => HTMLElement;            // #file-ask-drawer
  log: () => HTMLElement;            // #file-ask-log
  form: () => HTMLElement;          // #file-ask-form (only addEventListener is used)
  input: () => HTMLInputElement;     // #file-ask-input
  sendButton: () => HTMLButtonElement; // #file-ask-send
  toggle: () => HTMLElement;         // #file-ask
  // Cross-cutting state the drawer needs to ask in the right scope:
  openFile: () => string | null;
  selectedTask: () => string | null;
  loadHistory: () => Promise<void>;
}

export interface AskEntry { question: string; answer: string }

export interface AskDrawer {
  ask(question: string): Promise<void>;
  /** Clear the panel's log (called when a new file opens). */
  reset(): void;
  /** Wire the toggle and submit listeners. Call once at boot. */
  mount(): void;
  /** True while a question is in flight, so the host can disable the composer. */
  busy(): boolean;
}

export function createAskDrawer(deps: AskDrawerDeps): AskDrawer {
  const threads = new Map<string, AskEntry[]>();
  let taskId: string | null = null;
  let busy = false;

  function reset(): void { deps.log().replaceChildren(); }

  function entry(node: { question: string; answer: string } | { question: string; pending: true }): void {
    const log = deps.log();
    const item = deps.make("div", "ask-entry");
    item.append(deps.make("p", "ask-question", node.question));
    if ("pending" in node) {
      item.append(deps.make("p", "ask-answer ask-pending", "Thinking…"));
      log.append(item);
      log.scrollTop = log.scrollHeight;
      return;
    }
    const answer = deps.make("div", "ask-answer");
    answer.append(deps.richText(node.answer));
    item.append(answer);
    log.append(item);
    log.scrollTop = log.scrollHeight;
  }

  async function waitReply(task: string, timeoutMs: number): Promise<string> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      if (Date.now() > deadline) throw new Error("The answer took too long; check the conversation in the sidebar.");
      await new Promise(resolve => setTimeout(resolve, 900));
      const data = await deps.api<{ state: string; events: { kind: string; data: Record<string, unknown> }[] }>(`/api/tasks/${encodeURIComponent(task)}`);
      if (["completed", "failed", "blocked", "cancelled", "interrupted"].includes(data.state)) {
        const message = [...data.events].reverse().find(event => event.kind === "agent_message");
        const content = typeof message?.data?.content === "string" ? message.data.content : "";
        if (!content && data.state !== "completed") throw new Error("The conversation could not answer; check it in the sidebar.");
        return content || "No answer was returned.";
      }
    }
  }

  async function ask(question: string): Promise<void> {
    const path = deps.openFile();
    if (!path || busy || !question.trim()) return;
    busy = true;
    deps.sendButton().disabled = true;
    const selection = String(globalThis.getSelection?.() ?? "").trim();
    const scope = selection.length > 24 && selection.length < 1200 && !selection.startsWith(question)
      ? `In the file “${path}”, about this selected passage:\n"""\n${selection.slice(0, 1000)}\n"""\n\n${question}`
      : `About the file “${path}”:\n${question}`;
    entry({ question, pending: true });
    try {
      let answer = "";
      const selected = deps.selectedTask();
      if (selected) {
        // Side question: answered from the open conversation, nothing is added to it.
        const reply = await deps.api<{ answer: string }>(`/api/tasks/${encodeURIComponent(selected)}/ask`, { question: scope, approve_external: true });
        answer = reply.answer;
      } else if (!taskId) {
        // No conversation open: a Chat conversation carries this file's Q&A.
        const task = await deps.api<TaskSummary>("/api/tasks", { objective: scope, mode: "chat", verification: "syntax", approve_external: true });
        taskId = task.id;
        void deps.loadHistory().catch(() => undefined);
        answer = await waitReply(task.id, 120_000);
      } else {
        await deps.api(`/api/tasks/${encodeURIComponent(taskId)}/messages`, { objective: scope, mode: "chat", verification: "syntax", approve_external: true });
        answer = await waitReply(taskId, 120_000);
      }
      const thread = threads.get(path) ?? [];
      thread.push({ question, answer });
      threads.set(path, thread);
      entry({ question, answer });
    } catch (error) {
      const log = deps.log();
      const last = log.lastElementChild?.querySelector(".ask-pending");
      if (last) (last as HTMLElement).textContent = error instanceof Error ? error.message : "The question failed.";
    } finally {
      busy = false;
      deps.sendButton().disabled = false;
    }
  }

  function mount(): void {
    deps.toggle().addEventListener("click", () => {
      const drawer = deps.view();
      drawer.hidden = !drawer.hidden;
      if (!drawer.hidden) {
        const thread = threads.get(deps.openFile() ?? "");
        if (thread?.length && !deps.log().children.length) for (const item of thread) entry(item);
        deps.input().focus();
      }
    });
    deps.form().addEventListener("submit", event => {
      event.preventDefault();
      const field = deps.input();
      const question = field.value.trim();
      if (!question || busy) return;
      field.value = "";
      void ask(question);
    });
  }

  return { ask, reset, mount, busy: () => busy };
}