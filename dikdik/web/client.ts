/** A minimal TypeScript HTTP client for a running Dikdik server.
 *
 * Standard-library fetch only; no runtime npm dependencies. Tested against a
 * real loopback server in tests/web/client.test.ts.
 */

export interface Task {
  id: string;
  state: string;
  objective?: string;
  created: number;
  result?: Record<string, unknown>;
  events?: Array<Record<string, unknown>>;
  title?: string;
  pinned?: boolean;
  folder?: string;
  tags?: string[];
}

export interface SubmitRequest {
  objective: string;
  mode: "offline" | "model" | "agent" | "plan";
  verification: "syntax" | "docker";
  approve_external: boolean;
  approve_execution?: boolean;
  edits?: Array<{ path: string; before: string; after: string }>;
  model_profile?: string;
  agent_profile?: string;
}

export class DikdikError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "DikdikError";
    this.status = status;
  }
}

export class DikdikClient {
  url: string;
  token: string;

  constructor(url: string, token: string) {
    this.url = url.replace(/\/$/, "");
    this.token = token;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(this.url + path, {
      method,
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : null,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { const data = await response.json() as { error?: string }; if (data.error) detail = data.error; } catch {}
      throw new DikdikError(response.status, detail);
    }
    return response.status === 204 ? undefined as T : await response.json() as T;
  }

  async submit(objective: string, mode: string, verification: string, shareCode: boolean, edits?: Array<{ path: string; before: string; after: string }>): Promise<string> {
    const body: SubmitRequest = { objective, mode: mode as SubmitRequest["mode"], verification: verification as SubmitRequest["verification"], approve_external: shareCode };
    if (edits) body.edits = edits;
    const result = await this.call<{ id: string }>("POST", "/api/tasks", body);
    return result.id;
  }

  async tasks(): Promise<Task[]> { return this.call<Task[]>("GET", "/api/tasks"); }
  async task(id: string): Promise<Task> { return this.call<Task>("GET", `/api/tasks/${id}`); }
  async cancel(id: string): Promise<boolean> { return (await this.call<{ requested: boolean }>("POST", `/api/tasks/${id}/cancel`, {})).requested; }
  async pause(id: string): Promise<boolean> { return (await this.call<{ requested: boolean }>("POST", `/api/tasks/${id}/pause`, {})).requested; }
  async resume(id: string): Promise<Record<string, unknown>> { return this.call("POST", `/api/tasks/${id}/resume`, {}); }
  async fork(id: string, title?: string): Promise<string> { return (await this.call<{ id: string }>("POST", `/api/tasks/${id}/fork`, title ? { title } : {})).id; }
  async apply(id: string): Promise<Record<string, unknown>> { return this.call("POST", `/api/tasks/${id}/apply`, { confirm: true }); }
  async delete(id: string): Promise<void> { await this.call("DELETE", `/api/tasks/${id}`); }
  async project(): Promise<Record<string, unknown>> { return this.call("GET", "/api/project"); }
  async model(): Promise<Record<string, unknown>> { return this.call("GET", "/api/model"); }
  async health(): Promise<{ status: string }> { return this.call("GET", "/health"); }
  async serverInfo(): Promise<Record<string, unknown>> { return this.call("GET", "/server_info"); }
  async openapi(): Promise<Record<string, unknown>> { return this.call("GET", "/openapi.json"); }
}