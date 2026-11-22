import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CHAT_COMMANDS_KEY, CHAT_COMMANDS_LIMIT, CHAT_COMMANDS_OFF, CHAT_COMMANDS_ON, chatCommands } from "../../dikdik/web/chat-commands";
import { buildTaskRequest } from "../../dikdik/web/contracts";
import { memoryLines, memoryUsage } from "../../dikdik/web/memory-pane";

function storage() {
  const data = new Map<string, string>();
  return { data, getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
}
const chat = { objective: "Plot this CSV", mode: "chat" as const, approveExternal: true, edits: "" };

test("the commands switch is off by default and remembered per chat", () => {
  const store = storage();
  const choice = chatCommands(store);
  expect(choice.get(null)).toBe(false);
  expect(choice.get("a")).toBe(false);
  choice.set("a", true);
  expect(choice.get("a")).toBe(true);
  expect(choice.get("b")).toBe(false);
  expect(chatCommands(store).get("a")).toBe(true);  // survives a reload
  choice.set("a", false);
  expect(choice.get("a")).toBe(false);
  expect(store.data.get(CHAT_COMMANDS_KEY)).toBe("[]");
});
test("a new chat keeps the choice made before its first message, then the next one starts off", () => {
  const choice = chatCommands(storage());
  choice.set(null, true);
  expect(choice.get(null)).toBe(true);
  choice.adopt("new-chat");
  expect(choice.get("new-chat")).toBe(true);
  expect(choice.get(null)).toBe(false);
  choice.set(null, true);
  choice.reset();
  expect(choice.get(null)).toBe(false);
});
test("the remembered list is bounded and tolerates damaged or missing storage", () => {
  const store = storage();
  const choice = chatCommands(store);
  for (let i = 0; i < CHAT_COMMANDS_LIMIT + 5; i++) choice.set(`c${i}`, true);
  expect(choice.get("c0")).toBe(false);
  expect(choice.get(`c${CHAT_COMMANDS_LIMIT + 4}`)).toBe(true);
  store.data.set(CHAT_COMMANDS_KEY, "{not json");
  expect(choice.get("c1")).toBe(false);
  const none = chatCommands(null);
  none.set("x", true);
  expect(none.get("x")).toBe(false);
});
test("the switch text says commands run on this computer and each one is asked first", () => {
  for (const text of [CHAT_COMMANDS_OFF, CHAT_COMMANDS_ON]) {
    expect(text).toContain("on this computer");
    expect(text).toContain("asks you before each one");
  }
});

test("a chat with commands on asks the server to run them locally, asking before each", () => {
  const request = buildTaskRequest({ ...chat, verification: "local", approveExecution: true });
  expect(request).toMatchObject({ mode: "chat", verification: "local", approve_execution: true, confirmation: "ask" });
  expect(request).not.toHaveProperty("approve_browser");
  expect(request).not.toHaveProperty("approve_secrets");
});
test("a chat with commands off stays read-only", () => {
  for (const request of [buildTaskRequest({ ...chat, verification: "syntax", approveExecution: false }),
    buildTaskRequest({ ...chat, verification: "local", approveExecution: false }),
    buildTaskRequest({ ...chat, verification: "syntax", approveExecution: true })]) {
    expect(request).toMatchObject({ verification: "syntax", approve_execution: false });
    expect(request).not.toHaveProperty("confirmation");
  }
});

test("memory text becomes one entry per line, as the server stores it", () => {
  expect(memoryLines("  Prefers   short answers \r\n\nUses Windows\nUses Windows\n   ")).toEqual(["Prefers short answers", "Uses Windows"]);
  expect(memoryLines("")).toEqual([]);
});
test("memory usage is stated in words, including how far over the cap", () => {
  expect(memoryUsage(["abcd", "ef"], 2000)).toEqual({ used: 6, over: false, label: "6 of 2,000 characters" });
  expect(memoryUsage(["x".repeat(2005)], 2000)).toEqual({ used: 2005, over: true, label: "2,005 of 2,000 characters — 5 over the limit" });
});

test("the composer has a labelled, off-by-default Commands switch and a worded badge", () => {
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const field = /<label id="chat-commands-field"[^>]*>.*?<\/label>/.exec(html)?.[0] ?? "";
  expect(field).toContain('role="switch"');
  expect(field).toContain('<span class="chat-commands-word">Commands</span>');
  expect(field).not.toContain("checked");
  expect(field).toContain('aria-describedby="chat-commands-help"');
  expect(html).toMatch(/id="chat-commands-help" class="sr-only">Off by default\. Commands run on this computer/);
  expect(html).toMatch(/<p id="chat-commands-note"[^>]*role="status"[^>]*hidden>.*Commands on/);
});
test("settings has a Memory section wired to its pane", () => {
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const app = readFileSync("dikdik/web/app.ts", "utf8");
  expect(html).toContain('data-pane="memory" aria-selected="false"');
  expect(html).toContain('<div id="panel-memory"');
  expect(app).toContain('memory: memoryPane(el("panel-memory"), paneDeps)');
  expect(app).toContain('memory: "Memory"');
});
