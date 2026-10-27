import { expect, test } from "bun:test";

// A deliberately small DOM double exercises the real app polling/render path.
// It tracks node identity and text assignments; it is not a browser layout test.
class Node {
  textContent = "";
  className = "";
  hidden = false;
  disabled = false;
  required = false;
  checked = false;
  value = "";
  type = "";
  dataset: Record<string, string> = {};
  attributes = new Map<string, string>();
  children: Node[] = [];
  replacements = 0;
  onclick?: () => unknown;
  onchange?: () => unknown;
  listeners = new Map<string, (event: { preventDefault(): void }) => unknown>();
  addEventListener(name: string, callback: (event: { preventDefault(): void }) => unknown) { this.listeners.set(name, callback); }
  // Like the real DOM, replacing children recomputes textContent (diff lines render as spans).
  replaceChildren(...children: Node[]) { this.children = children; this.textContent = children.map(child => child.textContent).join(""); this.replacements++; }
  append(...children: Node[]) { this.children.push(...children); }
  setAttribute(name: string, value: string) { this.attributes.set(name, value); }
  querySelectorAll() { return this.children.filter(child => child instanceof Button); }
}
class Button extends Node {}
class Input extends Node {}
class Select extends Node {}
class Textarea extends Node {}

test("actual polling retains file selection and control nodes, with safe fallback on changed results", async () => {
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const install = (name: string, value: unknown) => {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  };
  const nodes = new Map<string, Node>();
  for (const id of ["token", "external", "execution", "browser-approval", "browser-consent", "browser-enabled", "browser-url", "attach-input", "condenser", "palette-input", "meta-folder", "meta-tags", "sounds", "git-name", "git-email", "secrets-approval", "agent-critic", "agent-memory", "agent-secrets"]) nodes.set(id, new Input());
  for (const id of ["cancel", "refresh", "start", "browser-open", "browser-stop", "browser-external-empty", "planner-create", "planner-build", "pause", "git-save"]) nodes.set(id, new Button());
  for (const id of ["run-mode", "mode", "verification", "confirmation-policy", "task-sort", "task-since", "task-scope", "title-model"]) nodes.set(id, new Select());
  for (const id of ["edits", "objective"]) nodes.set(id, new Textarea());
  const node = (id: string): Node => {
    if (!nodes.has(id)) nodes.set(id, new Node());
    return nodes.get(id)!;
  };
  node("run-mode").value = "classic"; node("mode").value = "offline"; node("verification").value = "syntax";
  const path = "<script>alert(1)</script>.py";
  const diff = `diff --git a/${path} b/${path}\nnew file mode 100644\n`;
  let result: unknown = { diff, file_changes: [{ path, status: "added", start_line: 0, end_line: 2 }] };
  let state = "running", requests = 0, poll: (() => void) | undefined;
  let holdGet: Promise<void> | null = null;
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  try {
    install("HTMLElement", Node); install("HTMLButtonElement", Button); install("HTMLInputElement", Input);
    install("HTMLSelectElement", Select); install("HTMLTextAreaElement", Textarea);
    install("document", { getElementById: node, createElement: (tag: string) => tag === "button" ? new Button() : new Node(), createElementNS: () => new Node() });
    install("setInterval", (callback: () => void) => { poll = callback; return 1; });
    install("fetch", async (url: string) => {
      if (url === "/api/session") return Response.json({ signed_in: true });
      if (url === "/api/model") return Response.json({ configured: false, source: "none", problem: "No model is set up." });
      if (url === "/api/project") return Response.json({ name: "fixture", files: [], python_tests: [], manifests: [], limitations: "fixture" });
      if (url.startsWith('/api/drafts/')) return Response.json({ scope: url.split('/').at(-1), content: '', revision: 0, updated: 0 });
      if (url.endsWith('/planner')) return Response.json({ task_id: 'a'.repeat(32), planner_id: null, content: '', revision: 0, built_revision: 0 });
      if (url === "/api/tasks") return Response.json([{ id: "task-1", state }]);
      if (url === "/api/connections") return Response.json([]);
      if (url === "/api/routers") return Response.json({ routers: [], strategies: {} });
      if (url === "/api/model-catalog") return Response.json({ default_provider: "anthropic", providers: {} });
      if (url === "/api/settings/app") return Response.json({ confirmation: "never", condenser: true, git_name: "", git_email: "", sounds: false });
      if (url === '/api/tasks/task-1/messages') { state = 'running'; result = null; return Response.json({ queued: false }); }
      if (url === "/api/profiles" || url === "/api/secrets" || url === "/api/skills" || url === "/api/automations" || url === "/api/agent-profiles") return Response.json([]);
      requests++;
      const snapshot = { id: "task-1", state, request: { objective: "Review candidate" }, events: [], result };
      if (holdGet) await holdGet;
      return Response.json(snapshot);
    });
    await import("../../dikdik/web/app");
    node("token").value = "local-test";
    await node("connect-form").listeners.get("submit")!({ preventDefault() {} });
    node("history").children[0]!.onclick!();
    await tick();
    expect(requests).toBe(1);
    expect(node("review-controls").hidden).toBe(false);
    const all = node("review-controls").children[0]!;
    const file = node("review-controls").children[1]!;
    expect(file.textContent).toBe(`Added · ${path}`);
    file.onclick!();
    expect(file.attributes.get("aria-pressed")).toBe("true");
    expect(all.attributes.get("aria-pressed")).toBe("false");
    expect(node("selected-file").textContent).toBe(`Added · ${path}`);
    expect(node("diff").textContent).toBe(diff);
    const replacements = node("review-controls").replacements;
    poll!(); await tick();
    poll!(); await tick();
    expect(requests).toBe(3);
    expect(node("review-controls").replacements).toBe(replacements);
    expect(node("review-controls").children[1]).toBe(file);
    expect(file.attributes.get("aria-pressed")).toBe("true");
    all.onclick!();
    expect(all.attributes.get("aria-pressed")).toBe("true");
    expect(node("selected-file").textContent).toBe("All changes");

    result = { diff, changed_files: [path] };
    poll!(); await tick();
    expect(node("review-controls").hidden).toBe(true);
    expect(node("review-controls").children).toHaveLength(0);
    expect(node("review-note").textContent).toContain("unavailable");
    expect(node("diff").textContent).toBe(diff);
    result = { diff, file_changes: [{ path: "wrong.py", status: "added", start_line: 0, end_line: 2 }] };
    poll!(); await tick();
    expect(node("review-note").textContent).toContain("could not be validated");
    expect(node("diff").textContent).toBe(diff);

    state = "failed"; result = { error: "Provider unavailable", diff: "", file_changes: [] };
    poll!(); await tick();
    expect(node("diff").hidden).toBe(true);
    expect(node("selected-file").hidden).toBe(true);
    expect(node("review-controls").hidden).toBe(true);
    expect(node("cancel").disabled).toBe(true);
    const stoppedRequests = requests;
    poll!(); await tick();
    expect(requests).toBe(stoppedRequests);

    // An older completed response must not hide a follow-up accepted meanwhile.
    state = 'completed'; result = null;
    let release!: () => void;
    holdGet = new Promise<void>(resolve => { release = resolve; });
    node('history').children[0]!.onclick!(); await tick();
    node('objective').value = 'Start another turn';
    node('edits').value = '[{"path":"a.py","before":"1","after":"2"}]';
    await node('task-form').listeners.get('submit')!({ preventDefault() {} });
    holdGet = null; release(); await tick(); await tick();
    expect(node('task-state').textContent).toBe('running');
    expect(node('cancel').disabled).toBe(false);
  } finally {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else Reflect.deleteProperty(globalThis, name);
    }
  }
});
