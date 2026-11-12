import { expect, test } from "bun:test";
import { browserView, buildTaskRequest, eventToMessages, type TaskEvent } from "../../dikdik/web/contracts";

const task = "a".repeat(32), screenshot = `/api/tasks/${task}/browser/screenshots/${"b".repeat(32)}`;
const event = (data: Record<string, unknown>, kind = "browser_result"): TaskEvent => ({ seq: 1, task_id: task, time: 1, kind, data });

test("browsing consent is separate and needs tool approval even with syntax verification", () => {
  const input = { objective: "Browse", mode: "agent" as const, verification: "syntax" as const, edits: "", approveExternal: true, approveExecution: false, approveBrowser: true };
  expect(() => buildTaskRequest(input)).toThrow("browser tool execution");
  expect(buildTaskRequest({ ...input, approveExecution: true })).toMatchObject({ approve_execution: true, approve_browser: true, verification: "syntax" });
  expect(buildTaskRequest({ ...input, mode: "model" })).not.toHaveProperty("approve_browser");
});

test("browser history keeps the last valid screenshot across failures and reloads", () => {
  const view = browserView([
    event({ status: "passed", url: "https://example.com", title: "<h1>Example</h1>", text: "untrusted", screenshot }),
    event({ url: "https://other.example/" }, "browser_started"),
    event({ status: "blocked", error: "No browser available" }),
  ]);
  expect(view).toMatchObject({ url: "https://example.com/", title: "<h1>Example</h1>", screenshot, status: "blocked", error: "No browser available" });
  expect(eventToMessages(event({ status: "passed", screenshot }), "Browse")[0]?.kind).toBe("browser_result");
});

test("an action on the live page moves the address on and keeps the page interactive", () => {
  const view = browserView([
    event({ status: "passed", url: "https://example.com/", title: "Home", screenshot, interactive: true }),
    event({ action: "click" }, "browser_started"),
    event({ status: "passed", url: "https://example.com/next", title: "Next" }),  // live action: no capture
  ]);
  expect(view).toMatchObject({ url: "https://example.com/next", title: "Next", screenshot, interactive: true, status: "passed" });
  // A record read (console, network) has no title and changes nothing; a bad address is ignored.
  expect(browserView([event({ status: "passed", url: "https://example.com/", title: "Home", screenshot, interactive: true }),
                      event({ status: "passed", url: "https://example.com/" , console: [] })]).title).toBe("Home");
  expect(browserView([event({ status: "passed", url: "https://example.com/", title: "Home", screenshot, interactive: true }),
                      event({ status: "passed", url: "javascript:alert(1)", title: "x" })]).url).toBe("https://example.com/");
  // Without an earlier capture there is nothing to stream, so no page appears.
  expect(browserView([event({ status: "passed", url: "https://example.com/", title: "Home" })]).url).toBe("");
});

test("unsafe links and screenshot sources never reach the browser view", () => {
  for (const url of ["javascript:alert(1)", "file:///secret", "https://user:key@example.com/"]) {
    expect(browserView([event({ status: "passed", url, screenshot })]).screenshot).toBe("");
  }
  for (const source of ["https://evil.example/pixel", "data:image/svg+xml,<svg/>", "/api/tasks/../secrets"]) {
    expect(browserView([event({ status: "passed", url: "https://example.com", screenshot: source })]).screenshot).toBe("");
  }
});

test("browser history keeps every captured page in order, skipping failures", async () => {
  const { browserHistory } = await import("../../dikdik/web/contracts");
  const shot = (n: string) => `/api/tasks/${"a".repeat(32)}/browser/screenshots/${n.repeat(32)}`;
  const event = (seq: number, data: Record<string, unknown>) => ({ seq, task_id: "t", time: seq, kind: "browser_result", data });
  const pages = browserHistory([
    event(1, { status: "passed", url: "https://example.com/", title: "One", screenshot: shot("b") }),
    event(2, { status: "failed", url: "https://bad.example/", error: "no" }),
    event(3, { status: "passed", url: "https://example.org/", title: "Two", screenshot: shot("c") }),
  ]);
  expect(pages.map(page => page.title)).toEqual(["One", "Two"]);
});

test("preview captures are grouped by run and shown with a localhost address", async () => {
  const { browserHistory, displayUrl } = await import("../../dikdik/web/contracts");
  const shot = (c: string) => `/api/tasks/${task}/browser/screenshots/${c.repeat(32)}`;
  const at = (seq: number, data: Record<string, unknown>, kind = "browser_result"): TaskEvent => ({ seq, task_id: task, time: seq, kind, data });
  const pages = browserHistory([
    at(1, {}, "browser_started"),
    at(2, { status: "passed", url: "http://preview.dikdik.example/", title: "App", screenshot: shot("c"), label: "desktop" }),
    at(3, { status: "passed", url: "http://preview.dikdik.example/", title: "App", screenshot: shot("d"), label: "mobile" }),
    at(4, {}, "browser_started"),
    at(5, { status: "passed", url: "https://example.org/", title: "Site", screenshot: shot("e") }),
  ]);
  expect(pages.map(page => [page.label, page.run])).toEqual([["desktop", 1], ["mobile", 1], ["", 4]]);
  expect(displayUrl("http://preview.dikdik.example/")).toBe("localhost");
  expect(displayUrl("http://preview.dikdik.example/pricing?plan=pro")).toBe("localhost/pricing?plan=pro");
  expect(displayUrl("https://example.org/")).toBe("https://example.org/");
});
