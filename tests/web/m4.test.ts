import { expect, test } from "bun:test";
import {
  M4_TEXT, PROFILE_PROVIDERS, apiUrl, buildAutomation, buildProfileCreate, buildProfilePatch, describeSchedule,
  buildSecretInput, buildSkillToggle, deleteProfileUrl, parseAutomations, parseCreatedId, parseProfiles,
  parseRunSummaries, parseSecretRefs, parseSecretSet, parseSkills, safeUrlSegment, sanitizeSecretError,
  validateEndpoint,
} from "../../dikdik/web/contracts";

// --- URL safety: control chars and newlines never reach a request path -------
test("safeUrlSegment rejects empty values, newlines and control characters", () => {
  expect(() => safeUrlSegment("", "id")).toThrow();
  for (const bad of ["a\nb", "a\rb", "a\tb", "a\x00b", "a\x1fb", "a\x7f"]) {
    expect(() => safeUrlSegment(bad, "id")).toThrow();
  }
  expect(safeUrlSegment("safe_id-1", "id")).toBe("safe_id-1");
  expect(apiUrl("/api/secrets", "my.ref-1")).toBe("/api/secrets/my.ref-1");
  expect(deleteProfileUrl("abc", true)).toBe("/api/profiles/abc?confirm_default=1");
  expect(deleteProfileUrl("abc", false)).toBe("/api/profiles/abc");
});

// --- profile builder mirrors the server-side contract ------------------------
test("buildProfileCreate validates name, provider, model, endpoint and secret ref", () => {
  const ok = buildProfileCreate({ name: " p1 ", provider: "openai", model: " gpt-1", endpoint: "https://api.openai.com/v1", timeoutSeconds: "30", credentialSecretRef: "key_1" });
  expect(ok).toEqual({ name: "p1", provider: "openai", model: "gpt-1", endpoint: "https://api.openai.com/v1", timeout_seconds: 30, credential_secret_ref: "key_1" });
  expect(() => buildProfileCreate({ name: "", provider: "openai", model: "m" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "bogus", model: "m" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "openai", model: "" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "openai", model: "m", endpoint: "ftp://x" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "openai", model: "m", timeoutSeconds: "0" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "openai", model: "m", timeoutSeconds: "601" })).toThrow();
  expect(() => buildProfileCreate({ name: "p", provider: "openai", model: "m", credentialSecretRef: "bad ref!" })).toThrow();
});

test("validateEndpoint accepts loopback and the fixed provider bases, rejects others", () => {
  expect(validateEndpoint("https://api.openai.com/v1")).toBe("https://api.openai.com/v1");
  expect(validateEndpoint("http://127.0.0.1:8787")).toBe("http://127.0.0.1:8787");
  expect(() => validateEndpoint("https://evil.example.com")).toThrow();
  expect(() => validateEndpoint("not a url")).toThrow();
});

test("buildProfilePatch rejects empty patches and validates fields", () => {
  expect(buildProfilePatch({ model: "new" })).toEqual({ model: "new" });
  expect(buildProfilePatch({ isDefault: true })).toEqual({ is_default: true });
  expect(() => buildProfilePatch({})).toThrow();
  expect(() => buildProfilePatch({ model: "  " })).toThrow();
  expect(() => buildProfilePatch({ timeoutSeconds: "abc" })).toThrow();
  // A key name rebinds the profile's API key; blank keeps whatever is bound.
  expect(buildProfilePatch({ model: "m", credentialSecretRef: " openai-key " })).toEqual({ model: "m", credential_secret_ref: "openai-key" });
  expect(buildProfilePatch({ model: "m", credentialSecretRef: "  " })).toEqual({ model: "m" });
  expect(() => buildProfilePatch({ credentialSecretRef: "bad ref!" })).toThrow();
});

// --- secret builder and the never-echo-plaintext rule ------------------------
test("buildSecretInput validates the ref pattern and requires a plaintext value", () => {
  expect(buildSecretInput({ ref: "k1", plaintext: "v" })).toEqual({ ref: "k1", plaintext: "v" });
  expect(() => buildSecretInput({ ref: ".hidden", plaintext: "v" })).toThrow();   // ref must not start with dot
  expect(() => buildSecretInput({ ref: "k", plaintext: "" })).toThrow();
  expect(() => buildSecretInput({ ref: "bad ref!", plaintext: "v" })).toThrow();
  expect(() => buildSecretInput({ ref: "k", plaintext: "x".repeat(65537) })).toThrow();
});

test("sanitizeSecretError never lets an error message echo the just-typed plaintext", () => {
  const secret = "super-secret-value-1234";
  const echoing = new Error(`rejected: ${secret}`);
  expect(sanitizeSecretError(echoing, secret)).toBe(M4_TEXT.secretHiddenReply);
  expect(sanitizeSecretError(new Error("network down"), secret)).toBe("network down");
  expect(sanitizeSecretError(new Error(`short: ${secret.slice(0, 3)}`), secret)).toBe(`short: ${secret.slice(0, 3)}`); // too short to match the guard
  expect(sanitizeSecretError("not-an-error", secret)).toBe(M4_TEXT.genericFailure);
});

// --- automation builder enforces the 60-second interval floor ----------------
test("buildAutomation validates the schedule and carries the user's approvals", () => {
  const base = { name: "a", objective: "do thing", scheduleKind: "interval" as const, intervalMinutes: "60", mode: "agent", verification: "syntax", approveExternal: true, approveExecution: false };
  expect(buildAutomation(base)).toEqual({ name: "a", schedule: { interval_seconds: 3600 }, objective: "do thing", mode: "agent", verification: "syntax", approve_external: true, approve_execution: false, timeout_seconds: 1800 });
  expect(buildAutomation({ ...base, scheduleKind: "daily", dailyAt: "07:30", agentProfile: "p" }).schedule).toEqual({ daily_at: "07:30" });
  expect(buildAutomation({ ...base, agentProfile: "p" }).agent_profile).toBe("p");
  expect(buildAutomation({ ...base, mode: "model", agentProfile: "p" }).agent_profile).toBeUndefined();
  for (const bad of [{ name: "" }, { objective: "  " }, { intervalMinutes: "0" }, { intervalMinutes: "1.5" }, { scheduleKind: "daily" as const, dailyAt: "7:5" },
    { mode: "offline" }, { approveExternal: false }, { verification: "docker" }]) {
    expect(() => buildAutomation({ ...base, ...bad })).toThrow();
  }
  expect(buildAutomation({ ...base, verification: "docker", approveExecution: true }).approve_execution).toBe(true);
  expect(buildAutomation({ ...base, scheduleKind: "cron", cron: "0 9 * * 1-5", timezone: "UTC" }).schedule).toEqual({ cron: "0 9 * * 1-5", timezone: "UTC" });
  expect(buildAutomation({ ...base, scheduleKind: "event", eventRepository: ".", eventRef: "HEAD" }).schedule).toEqual({ event: { type: "git_commit", repository: ".", ref: "HEAD" } });
  expect([{ interval_seconds: 60 }, { interval_seconds: 7200 }, { interval_seconds: 86400 }, { interval_seconds: 300 }, { daily_at: "07:30" }, { cron: "0 9 * * 1-5", timezone: "UTC" }, { event: { type: "git_commit", repository: ".", ref: "HEAD" } }].map(describeSchedule))
    .toEqual(["every minute", "every 2 hours", "every day", "every 5 minutes", "daily at 07:30", "0 9 * * 1-5 (UTC)", "when .@HEAD changes"]);
});

test("buildSkillToggle and the toggle builders round-trip the boolean", () => {
  expect(buildSkillToggle(true)).toEqual({ enabled: true });
});

// --- response parsers tolerate unknown extra fields and reject malformed ones -
test("parseProfiles keeps valid rows, drops malformed ones, ignores extra fields", () => {
  const rows = [
    { id: "p1", name: "P1", provider: "openai", model: "m", endpoint: "https://api.openai.com/v1", timeout_seconds: 30, is_default: true, extra: "ignored" },
    { id: "p2", name: "P2", provider: "bogus", model: "m", endpoint: "", timeout_seconds: 30, is_default: false },  // bad provider dropped
    { id: "", name: "P3", provider: "openai", model: "m", endpoint: "", timeout_seconds: 30, is_default: false },   // empty id dropped
  ];
  const profiles = parseProfiles(rows);
  expect(profiles).toHaveLength(1);
  expect(profiles[0]?.id).toBe("p1");
  expect(() => parseProfiles("not an array")).toThrow();
});

test("parseSecretRefs keeps valid refs and drops ones with bad set_at types", () => {
  const refs = parseSecretRefs([{ ref: "k1", set_at: 1700000000 }, { ref: "k2", set_at: null }, { ref: "k3", set_at: true }, { ref: "", set_at: 1 }]);
  expect(refs.map(r => r.ref)).toEqual(["k1", "k2"]);
});

test("parseSkills keeps valid skills and rejects control chars in the path", () => {
  const skills = parseSkills([
    { id: "s1", name: "S1", scope: "personal", enabled: true, path: "/data/skills/s1.md" },
    { id: "s2", name: "S2", scope: "bogus", enabled: true, path: "/x" },
    { id: "s3", name: "S3", scope: "project", enabled: false, path: "bad\npath" },
  ]);
  expect(skills).toHaveLength(1);
  expect(skills[0]?.id).toBe("s1");
});

test("parseAutomations validates the schedule integer and drops malformed rows", () => {
  const autos = parseAutomations([
    { id: "a1", name: "A1", schedule: { interval_seconds: 120 }, workspace_path: "/ws", objective: "o", enabled: true, last_state: "completed", next_run: 123, failures: 0 },
    { id: "a2", name: "A2", schedule: { interval_seconds: 30 }, workspace_path: "/ws", objective: "o", enabled: true, last_state: null, next_run: null, failures: 0 },  // server enforces floor; parser validates shape only, keeps it
    { id: "a3", name: "A3", schedule: { interval_seconds: 120 }, workspace_path: "/ws", objective: "o", enabled: true, last_state: null, next_run: null, failures: 1.5 }, // non-integer failures dropped
    { id: "a4", name: "A4", schedule: { interval_seconds: "60" }, workspace_path: "/ws", objective: "o", enabled: true, last_state: null, next_run: null, failures: 0 }, // non-integer schedule dropped
    { id: "a5", name: "A5", schedule: { daily_at: "07:30" }, workspace_path: "/ws", objective: "o", enabled: true, last_state: null, next_run: 1, failures: 0,
      run: { mode: "agent", verification: "syntax", approve_external: true, approve_execution: false }, last_task_id: "t9" },
  ]);
  expect(autos.map(a => a.id)).toEqual(["a1", "a2", "a5"]);
  expect(autos[0]?.schedule).toEqual({ interval_seconds: 120 });
  expect(autos[2]?.run?.mode).toBe("agent");
  expect(autos[2]?.last_task_id).toBe("t9");
  expect(autos[0]?.run).toBeNull();  // made before runs stored their approvals
});

test("parseRunSummaries and parseCreatedId / parseSecretSet have strict happy and failure paths", () => {
  expect(parseRunSummaries([{ task_id: "t1", state: "completed", created: 5 }, { task_id: "", state: "x" }, { task_id: "t2", state: null, created: 6 }]))
    .toEqual([{ id: "t1", state: "completed", created: 5 }, { id: "t2", state: "deleted", created: 6 }]);
  expect(parseCreatedId({ id: "abc" })).toEqual({ id: "abc" });
  expect(() => parseCreatedId({ id: "" })).toThrow();
  expect(() => parseCreatedId({ id: "a\nb" })).toThrow();
  expect(parseSecretSet({ ref: "k1", set: true })).toEqual({ ref: "k1", set: true });
  expect(() => parseSecretSet({ ref: "k1", set: false })).toThrow();
});

// --- the honesty label is the single source of truth and actually surfaces -----
test("the contract-status honesty label is defined and surfaces in M4_TEXT", () => {
  expect(M4_TEXT.contractStatus).toBe("tested: contract");
  expect(M4_TEXT.contractNote).toContain("live run");
  expect(M4_TEXT.credentialsNote).toContain("never displayed");
  expect(M4_TEXT.secretsNote).toContain("never returned");
  expect(M4_TEXT.skillsNote).toContain("untrusted data");
  expect(PROFILE_PROVIDERS).toEqual(["openai", "anthropic", "azure", "local"]);
});

// --- a panel happy path: profiles panel renders server data and issues a real request ---
test("profiles panel renders fetched profiles and issues a validated create request", async () => {
  type N = { textContent: string; className: string; hidden: boolean; disabled: boolean; checked: boolean; value: string; required: boolean; type: string;
    children: N[]; replacements: number; onclick?: () => void; onchange?: () => void; focus?: () => void;
    listeners: Map<string, (e: { preventDefault(): void }) => void>;
    setAttribute(n: string, v: string): void; addEventListener(n: string, cb: (e: { preventDefault(): void }) => void): void;
    append(...c: N[]): void; replaceChildren(...c: N[]): void; };
  let counter = 0;
  const make = (): N => ({
    textContent: "", className: "", hidden: false, disabled: false, checked: false, value: "", required: false, type: "",
    children: [], replacements: 0, listeners: new Map(),
    setAttribute(n, v) { (this as Record<string, unknown>)[`attr_${n}`] = v; },
    addEventListener(n, cb) { this.listeners.set(n, cb); },
    append(...c) { this.children.push(...c); },
    replaceChildren(...c) { this.children = c; this.replacements++; },
  });
  const create = (tag: string): N => { const n = make(); (n as Record<string, unknown>)._tag = tag; return n; };
  const root = make();
  const calls: { path: string; method: string; body?: unknown }[] = [];
  const request = async <T,>(path: string, method: string, body?: unknown): Promise<T> => {
    calls.push({ path, method, body });
    if (path === "/api/profiles" && method === "GET") return [{ id: "p1", name: "P1", provider: "openai", model: "gpt", endpoint: "", timeout_seconds: 30, is_default: true }] as unknown as T;
    if (path === "/api/profiles" && method === "POST") return { id: "new1234567890" } as unknown as T;
    return [] as unknown as T;
  };
  const { profilesPanel } = await import("../../dikdik/web/contracts");
  const panel = profilesPanel({ root: root as unknown as Parameters<typeof profilesPanel>[0]["root"], create: create as unknown as Parameters<typeof profilesPanel>[0]["create"], request });
  await panel.refresh();
  // Recursively collect textContent across the rendered tree (rows are nested in the list).
  const allText: string[] = [];
  const walk = (n: N): void => { if (n.textContent) allText.push(n.textContent); for (const c of n.children) walk(c); };
  walk(root);
  const blob = allText.join("\n");
  expect(blob).toContain("P1");
  expect(blob).toContain("tested: contract");
  // Find the create form's submit handler and fire it with values set on the inputs.
  const findInputs = (n: N): N[] => n.children.flatMap(c => [c, ...findInputs(c)]);
  const inputs = findInputs(root);
  const nameInput = inputs.find(i => i.value === "" && i.type === "text" && i.required);
  const modelInput = inputs.find(i => i.type === "text" && i.required && i !== nameInput);
  if (nameInput) nameInput.value = "newp";
  if (modelInput) modelInput.value = "newmodel";
  const forms = findInputs(root).concat(root.children.flatMap(c => c.children)).flatMap(n => n.listeners ? Array.from(n.listeners.keys()) : []);
  void forms;
  // Locate the form element by scanning root children for a node with a submit listener.
  const formNode = root.children.find(c => c.listeners.has("submit"));
  expect(formNode).toBeDefined();
  formNode!.listeners.get("submit")!({ preventDefault() {} });
  await new Promise(r => setTimeout(r, 0));
  const post = calls.find(c => c.method === "POST" && c.path === "/api/profiles");
  expect(post).toBeDefined();
  expect((post!.body as { name: string; provider: string; model: string }).name).toBe("newp");
  expect((post!.body as { provider: string }).provider).toBe("openai");
  counter; void counter;
});
