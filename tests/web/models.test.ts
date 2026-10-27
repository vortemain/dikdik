import { describe, expect, test } from "bun:test";
import { optionalNumber, profileBody } from "../../dikdik/web/models-pane";
import { buildTaskRequest, eventToMessages, type TaskEvent } from "../../dikdik/web/contracts";

const fields = { name: " Fast ", provider: "openai", model: " gpt-5-mini ", endpoint: "", timeout: "", connection: "", keyRef: "openai-key", temperature: "", topP: "", maxTokens: "" };
const event = (kind: string, data: Record<string, unknown>): TaskEvent => ({ seq: 3, task_id: "t", time: 1, kind, data });

describe("profile editor", () => {
  test("builds a clean body; a connection replaces the stored key", () => {
    expect(profileBody(fields)).toEqual({ name: "Fast", provider: "openai", model: "gpt-5-mini", endpoint: null, connection_id: null, credential_secret_ref: "openai-key", temperature: null, top_p: null, max_output_tokens: null });
    expect(profileBody({ ...fields, connection: "c1", temperature: "0.4", maxTokens: "800", timeout: "90" })).toMatchObject({ connection_id: "c1", credential_secret_ref: null, temperature: 0.4, max_output_tokens: 800, timeout_seconds: 90 });
  });
  test("refuses out-of-range settings with readable messages", () => {
    expect(() => profileBody({ ...fields, name: " " })).toThrow("name");
    expect(() => profileBody({ ...fields, temperature: "3" })).toThrow("Temperature must be 0–2");
    expect(() => profileBody({ ...fields, topP: "1.5" })).toThrow("Top P must be 0–1");
    expect(() => profileBody({ ...fields, maxTokens: "2.5" })).toThrow("whole number");
    expect(optionalNumber("  ", "x")).toBeNull();
    expect(() => optionalNumber("abc", "Timeout")).toThrow("Timeout must be a number");
  });
});

describe("requests and events", () => {
  const agent = { objective: "x", mode: "agent" as const, verification: "docker" as const, approveExecution: true, approveExternal: true, edits: "[]" };
  test("sandbox secrets ride only on approved Docker agent runs", () => {
    expect(buildTaskRequest({ ...agent, approveSecrets: true }).approve_secrets).toBe(true);
    expect(buildTaskRequest({ ...agent, verification: "syntax", approveSecrets: true })).not.toHaveProperty("approve_secrets");
    expect(buildTaskRequest({ ...agent, approveSecrets: false })).not.toHaveProperty("approve_secrets");
  });
  test("routing, critic and sandbox-secret rows", () => {
    expect(eventToMessages(event("model_routed", { router: "R", profile: "Strong", reason: "first message" }), "")[0]).toMatchObject({ title: "Routed to Strong", body: "first message" });
    expect(eventToMessages(event("critic", { achieved: false, reason: "Tests missing", next: "Add one" }), "")[0]).toMatchObject({ title: "Critic: not done yet", body: "Tests missing\nAdd one" });
    expect(eventToMessages(event("critic", { error: "Model request failed" }), "")[0]?.title).toBe("Critic unavailable");
    expect(eventToMessages(event("sandbox_secrets", { names: ["GH_TOKEN", 5] }), "")[0]?.body).toBe("GH_TOKEN");
  });
});
