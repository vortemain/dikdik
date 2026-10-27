import { expect, test, mock } from "bun:test";
import { DikdikClient, DikdikError } from "../../dikdik/web/client";

function mockFetch(status: number, body: unknown) {
  const fn = mock(() => Promise.resolve({
    ok: status < 400,
    status,
    statusText: status === 401 ? "Unauthorized" : "OK",
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response));
  globalThis.fetch = fn as typeof fetch;
  return fn;
}

test("submit builds the right request body and returns the id", async () => {
  const fn = mockFetch(202, { id: "abc" });
  const c = new DikdikClient("http://127.0.0.1:8787", "tok");
  const id = await c.submit("Fix it", "offline", "syntax", true, [{ path: "a.py", before: "1", after: "2" }]);
  expect(id).toBe("abc");
  expect(fn).toHaveBeenCalledTimes(1);
  const [url, init] = fn.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://127.0.0.1:8787/api/tasks");
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body.objective).toBe("Fix it");
  expect(body.approve_external).toBe(true);
  expect(body.edits).toEqual([{ path: "a.py", before: "1", after: "2" }]);
});

test("errors are DikdikError with the server status and message", async () => {
  mockFetch(401, { error: "Not signed in" });
  const c = new DikdikClient("http://127.0.0.1:8787", "wrong");
  try {
    await c.tasks();
    expect(true).toBe(false);
  } catch (e) {
    expect(e).toBeInstanceOf(DikdikError);
    expect((e as DikdikError).status).toBe(401);
    expect((e as DikdikError).message).toBe("Not signed in");
  }
});

test("health and serverInfo call unauthenticated-style GETs", async () => {
  const fn = mockFetch(200, { status: "ok" });
  const c = new DikdikClient("http://127.0.0.1:8787", "tok");
  const h = await c.health();
  expect(h.status).toBe("ok");
  const [url] = fn.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://127.0.0.1:8787/health");
});

test("fork sends an optional title", async () => {
  const fn = mockFetch(201, { id: "forked" });
  const c = new DikdikClient("http://127.0.0.1:8787", "tok");
  const id = await c.fork("abc", "My fork");
  expect(id).toBe("forked");
  const [, init] = fn.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string);
  expect(body).toEqual({ title: "My fork" });
});

test("apply sends confirm true", async () => {
  const fn = mockFetch(200, { applied: ["a.py"] });
  const c = new DikdikClient("http://127.0.0.1:8787", "tok");
  const result = await c.apply("abc");
  expect(result).toEqual({ applied: ["a.py"] });
  const [, init] = fn.mock.calls[0] as [string, RequestInit];
  const body = JSON.parse(init.body as string);
  expect(body).toEqual({ confirm: true });
});