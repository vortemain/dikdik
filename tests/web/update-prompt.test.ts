import { expect, test } from "bun:test";

// The real app module against a small DOM double, like review-polling.test.ts: the update
// recommendation must stay up across re-renders until the user answers it.
class Node {
  textContent = "";
  className = "";
  hidden = false;
  disabled = false;
  checked = false;
  value = "";
  type = "";
  dataset: Record<string, string> = {};
  attributes = new Map<string, string>();
  children: Node[] = [];
  onclick?: () => unknown;
  listeners = new Map<string, (event: { preventDefault(): void }) => unknown>();
  addEventListener(name: string, callback: (event: { preventDefault(): void }) => unknown) { this.listeners.set(name, callback); }
  replaceChildren(...children: Node[]) { this.children = children; this.textContent = children.map(child => child.textContent).join(""); }
  append(...children: Node[]) { this.children.push(...children); }
  prepend(...children: Node[]) { this.children.unshift(...children); }
  setAttribute(name: string, value: string) { this.attributes.set(name, value); }
  querySelectorAll() { return this.children.filter(child => child instanceof Button); }
}
class Button extends Node {}
class Input extends Node {}
class Select extends Node {}
class Textarea extends Node {}

test("the update recommendation stays until Later or an action, then stays quiet for that version", async () => {
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const install = (name: string, value: unknown) => {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  };
  const nodes = new Map<string, Node>();
  for (const id of ["token", "external", "execution", "browser-approval", "browser-consent", "browser-enabled", "browser-url", "attach-input", "condenser", "palette-input", "meta-folder", "meta-tags", "sounds", "notification-previews", "update-checks", "git-name", "git-email", "secrets-approval", "agent-critic", "agent-memory", "agent-secrets", "terminal-cwd", "terminal-command"]) nodes.set(id, new Input());
  for (const id of ["cancel", "refresh", "start", "browser-open", "browser-stop", "browser-back", "browser-forward", "browser-reload", "browser-external-empty", "browser-live-stop", "planner-create", "planner-submit", "planner-cancel", "pause", "git-save", "terminal-new", "terminal-copy", "terminal-clear", "terminal-stop", "terminal-run", "browser-text-toggle", "browser-example", "notification-test",
    "update-check-now", "update-install", "update-pop-go", "update-pop-later"]) nodes.set(id, new Button());
  for (const id of ["run-mode", "mode", "verification", "confirmation-policy", "task-sort", "task-since", "task-scope", "title-model", "image-model", "browser-history", "browser-zoom", "notification-mode"]) nodes.set(id, new Select());
  for (const id of ["edits", "objective", "planner-objective"]) nodes.set(id, new Textarea());
  const node = (id: string): Node => {
    if (!nodes.has(id)) nodes.set(id, new Node());
    return nodes.get(id)!;
  };
  node("run-mode").value = "classic"; node("mode").value = "offline"; node("verification").value = "syntax";
  let checks = 0;
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
  try {
    install("HTMLElement", Node); install("HTMLButtonElement", Button); install("HTMLInputElement", Input);
    install("HTMLSelectElement", Select); install("HTMLTextAreaElement", Textarea);
    const stored = new Map<string, string>([["dikdik.conversation.v2", "code"], ["dikdik.runMode.v2", "classic"]]);
    install("localStorage", { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => { stored.set(key, value); },
      removeItem: (key: string) => { stored.delete(key); } });
    install("document", { getElementById: node, createElement: (tag: string) => tag === "button" ? new Button() : new Node(), createElementNS: () => new Node() });
    install("setInterval", () => 1);
    install("fetch", async (url: string) => {
      if (url.startsWith("/api/desktop/update/check")) {
        checks++;
        return Response.json({ current: "0.8.0", latest: "v0.8.1", available: true, can_install: true, install: "windows", url: "https://example.test/r" });
      }
      if (url === "/api/desktop/update/status") return Response.json({ state: "idle" });
      if (url === "/api/session" || url === "/api/session/local") return Response.json({ signed_in: true });
      if (url === "/api/model") return Response.json({ configured: false, source: "none", problem: "No model is set up." });
      if (url === "/api/project") return Response.json({ name: "fixture", files: [], python_tests: [], manifests: [], limitations: "fixture" });
      if (url.startsWith("/api/drafts/")) return Response.json({ scope: url.split("/").at(-1), content: "", revision: 0, updated: 0 });
      if (url === "/api/tasks") return Response.json([]);
      if (url === "/api/connections") return Response.json([]);
      if (url === "/api/routers") return Response.json({ routers: [], strategies: {} });
      if (url === "/api/model-catalog") return Response.json({ default_provider: "anthropic", providers: {} });
      if (url === "/api/settings/app") return Response.json({ confirmation: "never", condenser: true, git_name: "", git_email: "", sounds: false, update_checks: true });
      if (url === "/api/profiles" || url === "/api/secrets" || url === "/api/skills" || url === "/api/automations" || url === "/api/agent-profiles") return Response.json([]);
      return Response.json({});
    });
    // Its own instance: review-polling.test.ts may already hold the shared module in this process.
    await import("../../dikdik/web/app?update-prompt");
    await settle();
    node("token").value = "local-test";
    await node("connect-form").listeners.get("submit")!({ preventDefault() {} });
    await settle();
    expect(checks).toBeGreaterThan(0);
    expect(node("update-pop").hidden).toBe(false);
    expect(node("update-pop-text").textContent).toContain("v0.8.1");

    // Checking again (as Check now, the six-hour timer or opening Settings do) re-renders: the offer stays.
    const before = checks;
    await node("update-check-now").listeners.get("click")!({ preventDefault() {} });
    await settle();
    expect(checks).toBeGreaterThan(before);
    expect(node("update-pop").hidden).toBe(false);

    // Later answers it: hidden now, and the skipped version stays quiet on the next check.
    await node("update-pop-later").listeners.get("click")!({ preventDefault() {} });
    expect(node("update-pop").hidden).toBe(true);
    expect(stored.get("dikdik.update.snooze")).toBe("v0.8.1");
    await node("update-check-now").listeners.get("click")!({ preventDefault() {} });
    await settle();
    expect(node("update-pop").hidden).toBe(true);
  } finally {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else Reflect.deleteProperty(globalThis, name);
    }
  }
});
