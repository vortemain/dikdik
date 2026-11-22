import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { buildTaskRequest } from "../../dikdik/web/contracts";
import { memoryLines, memoryUsage } from "../../dikdik/web/memory-pane";
import { timeGreeting } from "../../dikdik/web/welcome";

const chat = { objective: "Plot this CSV", mode: "chat" as const, approveExternal: true, edits: "" };

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

test("a chat always offers commands, each asked first, with no switch to manage", () => {
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const app = readFileSync("dikdik/web/app.ts", "utf8");
  expect(html).not.toContain("chat-commands");
  expect(app).toMatch(/mode: "chat", verification: "local",\s+approveExecution: true/);
});
test("the welcome puts the message box between the greeting and the starter chips", () => {
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const hero = html.indexOf('id="welcome-title"'), slot = html.indexOf('id="welcome-composer"'), chips = html.indexOf('id="welcome-suggestions"');
  expect(hero).toBeGreaterThan(0);
  expect(slot).toBeGreaterThan(hero);
  expect(chips).toBeGreaterThan(slot);
  expect(html).toContain('<section id="composer" class="composer"');
  expect(html).toMatch(/id="welcome-suggestions" class="suggestions" role="group" aria-label="Start with"/);
});
test("the welcome greeting follows the time of day", () => {
  expect(timeGreeting(4)).toBe("Good evening");
  expect(timeGreeting(5)).toBe("Good morning");
  expect(timeGreeting(11)).toBe("Good morning");
  expect(timeGreeting(12)).toBe("Good afternoon");
  expect(timeGreeting(17)).toBe("Good afternoon");
  expect(timeGreeting(18)).toBe("Good evening");
});
test("settings has a Memory section wired to its pane", () => {
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const app = readFileSync("dikdik/web/app.ts", "utf8");
  expect(html).toContain('data-pane="memory" aria-selected="false"');
  expect(html).toContain('<div id="panel-memory"');
  expect(app).toContain('memory: memoryPane(el("panel-memory"), paneDeps)');
  expect(app).toContain('memory: "Memory"');
});
