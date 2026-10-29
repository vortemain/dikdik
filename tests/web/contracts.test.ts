import { describe, expect, test } from "bun:test";
import { buildTaskRequest, confirmationForRunMode, isTerminal, modePolicy, parseMode, parseVerification, resultStatus } from "../../dikdik/web/contracts";

const base = { objective: "Fix addition", mode: "offline" as const, verification: "syntax" as const, approveExecution: false, approveExternal: false, edits: '[{"path":"calculator.py","before":"a-b","after":"a+b"}]' };

describe("mode and permission boundaries", () => {
  test("Auto overrides older Ask or risky policies with no tool prompts", () => {
    const request = buildTaskRequest({ ...base, mode: "agent", verification: "local", approveExternal: true,
      approveExecution: true, confirmation: confirmationForRunMode("auto") });
    expect(request.confirmation).toBe("never");
    expect(confirmationForRunMode("ask")).toBe("ask");
    expect(confirmationForRunMode("classic")).toBeUndefined();
  });
  test("offline preserves exact edits and does not forward stale approvals", () => {
    const request = buildTaskRequest({ ...base, approveExecution: true, approveExternal: true });
    expect(request.edits).toEqual([{ path: "calculator.py", before: "a-b", after: "a+b" }]);
    expect(request.approve_execution).toBe(false);
    expect(request.approve_external).toBe(false);
    expect(modePolicy("offline", "syntax").showEdits).toBe(true);
  });
  test("model requires external approval but allows syntax-only single proposal", () => {
    expect(() => buildTaskRequest({ ...base, mode: "model" })).toThrow("external provider");
    const request = buildTaskRequest({ ...base, mode: "model", approveExternal: true, edits: "invalid JSON" });
    expect(request.verification).toBe("syntax");
    expect(request.edits).toEqual([]);
    expect(modePolicy("model", "syntax").locksVerification).toBe(false);
  });
  test("agent runs syntax-only or with Docker tests, and ignores user-authored edits", () => {
    const agent = { ...base, mode: "agent" as const, approveExternal: true, edits: "invalid JSON" };
    expect(buildTaskRequest(agent)).toEqual({
      objective: base.objective, mode: "agent", verification: "syntax", edits: [], approve_execution: false, approve_external: true,
    });
    const docker = { ...agent, verification: "docker" as const };
    expect(() => buildTaskRequest(docker)).toThrow("Docker");
    expect(() => buildTaskRequest({ ...docker, approveExecution: true, approveExternal: false })).toThrow("external provider");
    expect(buildTaskRequest({ ...docker, approveExecution: true })).toEqual({
      objective: base.objective, mode: "agent", verification: "docker", edits: [], approve_execution: true, approve_external: true,
    });
    expect(modePolicy("agent", "syntax")).toEqual({ showEdits: false, requiresExternal: true, verification: "syntax", locksVerification: false, requiresExecution: false });
  });
  test("all Docker tasks require execution permission", () => {
    expect(() => buildTaskRequest({ ...base, verification: "docker" })).toThrow("Docker");
    expect(buildTaskRequest({ ...base, verification: "docker", approveExecution: true }).approve_execution).toBe(true);
  });
  test("rejects empty objectives, invalid edits, and unsupported selections", () => {
    expect(() => buildTaskRequest({ ...base, objective: "  " })).toThrow("objective");
    for (const edits of ["null", "{}", '[{"path":3}]', "[null]"]) expect(() => buildTaskRequest({ ...base, edits })).toThrow("Exact replacements");
    expect(() => parseMode("unknown")).toThrow();
    expect(() => parseVerification("host")).toThrow();
    for (const mode of ["offline", "model", "agent"] as const) expect(parseMode(mode)).toBe(mode);
    expect(parseVerification("docker")).toBe("docker");
  });
});

describe("evidence-based status", () => {
  test("static checks can complete a change without claiming test verification", () => {
    expect(resultStatus("completed", { verification_level: "checks", tests_verified: false })).toContain("Available project checks passed");
    expect(resultStatus("completed", { verification_level: "checks", tests_verified: false })).toContain("not test-verified");
    expect(resultStatus("blocked", { verification_level: "checks", error: "Typecheck failed" })).toBe("Typecheck failed");
  });
  test("recognizes all stopped states without inventing success", () => {
    for (const state of ["completed", "failed", "blocked", "cancelled", "interrupted"]) {
      expect(isTerminal(state)).toBe(true);
      expect(resultStatus(state)).toContain("without a result");
    }
    expect(isTerminal("running")).toBe(false);
    expect(resultStatus("running")).toContain("Work is running");
  });
  test("completion and syntax checks do not imply tests passed", () => {
    const status = resultStatus("completed", { checks: [{ kind: "syntax", status: "passed" }] });
    expect(status).toContain("NOT TEST-VERIFIED");
    expect(status).toContain("syntax: passed");
    expect(resultStatus("completed", {})).toContain("NOT TEST-VERIFIED");
  });
  test("reports verification only with backend evidence and prioritizes errors", () => {
    expect(resultStatus("completed", { tests_verified: true })).toContain("not proof of every acceptance criterion");
    expect(resultStatus("failed", { error: "Docker unavailable", tests_verified: true })).toBe("Docker unavailable");
  });
});
