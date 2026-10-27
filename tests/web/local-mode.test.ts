import { expect, test } from "bun:test";
import { buildTaskRequest, modePolicy, parseVerification } from "../../dikdik/web/contracts";

const base = { objective: "Fix add", edits: "[]", approveExternal: true };

test("local mode is agent work on this computer and needs its own approval", () => {
  expect(parseVerification("local")).toBe("local");
  expect(modePolicy("agent", "local")).toMatchObject({ verification: "local", requiresExecution: true });
  expect(() => buildTaskRequest({ ...base, mode: "agent", verification: "local", approveExecution: false }))
    .toThrow("running commands on this computer");
  const request = buildTaskRequest({ ...base, mode: "agent", verification: "local", approveExecution: true, confirmation: "ask" });
  expect(request).toMatchObject({ mode: "agent", verification: "local", approve_execution: true, approve_external: true, confirmation: "ask" });
});

test("other modes never run in the folder", () => {
  for (const mode of ["model", "plan"] as const) {
    const request = buildTaskRequest({ ...base, mode, verification: "local", approveExecution: true, confirmation: "ask" });
    expect(request.verification).toBe("syntax");
    expect(request.approve_execution).toBe(false);
    expect(request.confirmation).toBeUndefined();
  }
});
