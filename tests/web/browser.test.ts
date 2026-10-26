import { expect, test } from "bun:test";
import { browserView, buildTaskRequest, eventToMessages, type TaskEvent } from "../../dikdik/web/contracts";

const task = "a".repeat(32), screenshot = `/api/tasks/${task}/browser/screenshots/${"b".repeat(32)}`;
const event = (data: Record<string, unknown>, kind = "browser_result"): TaskEvent => ({ seq: 1, task_id: task, time: 1, kind, data });

test("browsing consent is separate and needs Docker approval even with syntax verification", () => {
  const input = { objective: "Browse", mode: "agent" as const, verification: "syntax" as const, edits: "", approveExternal: true, approveExecution: false, approveBrowser: true };
  expect(() => buildTaskRequest(input)).toThrow("Docker");
  expect(buildTaskRequest({ ...input, approveExecution: true })).toMatchObject({ approve_execution: true, approve_browser: true, verification: "syntax" });
  expect(buildTaskRequest({ ...input, mode: "model" })).not.toHaveProperty("approve_browser");
});

test("browser history keeps the last valid screenshot across failures and reloads", () => {
  const view = browserView([
    event({ status: "passed", url: "https://example.com", title: "<h1>Example</h1>", text: "untrusted", screenshot }),
    event({ url: "https://other.example/" }, "browser_started"),
    event({ status: "blocked", error: "Docker unavailable" }),
  ]);
  expect(view).toMatchObject({ url: "https://example.com/", title: "<h1>Example</h1>", screenshot, status: "blocked", error: "Docker unavailable" });
  expect(eventToMessages(event({ status: "passed", screenshot }), "Browse")[0]?.kind).toBe("browser_result");
});

test("unsafe links and screenshot sources never reach the browser view", () => {
  for (const url of ["javascript:alert(1)", "file:///secret", "https://user:key@example.com/"]) {
    expect(browserView([event({ status: "passed", url, screenshot })]).screenshot).toBe("");
  }
  for (const source of ["https://evil.example/pixel", "data:image/svg+xml,<svg/>", "/api/tasks/../secrets"]) {
    expect(browserView([event({ status: "passed", url: "https://example.com", screenshot: source })]).screenshot).toBe("");
  }
});
