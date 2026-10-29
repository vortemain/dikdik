import { expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { validateDialogFields, type DialogField } from "../../dikdik/web/dialogs";

test("application confirmations never fall back to blocking browser dialogs", () => {
  for (const name of readdirSync("dikdik/web").filter(name => name.endsWith(".ts") && name !== "icon-manifest.ts")) {
    const source = readFileSync("dikdik/web/" + name, "utf8");
    expect(source, name).not.toMatch(/\b(?:alert|confirm|prompt)\s*\(/);
  }
});

test("themed forms validate required inputs, URLs and conditional custom-agent fields", () => {
  const fields: DialogField[] = [
    { name: "name", label: "Name", required: true },
    { name: "url", label: "Backend URL", type: "url", required: true },
    { name: "preset", label: "Agent", type: "select", options: [{ value: "built-in", label: "Built in" }, { value: "", label: "Custom" }] },
    { name: "command", label: "Command", required: true, showWhen: values => !values.preset },
  ];
  const valid = { name: "Agent", url: "http://localhost:9000", preset: "built-in" };
  expect(validateDialogFields(fields, valid)).toBeNull();
  expect(validateDialogFields(fields, { ...valid, name: "  " })).toBe("Name is required.");
  expect(validateDialogFields(fields, { ...valid, url: "javascript:alert" })).toContain("http://");
  expect(validateDialogFields(fields, { ...valid, url: "bad url" })).toContain("valid");
  expect(validateDialogFields(fields, { ...valid, preset: "" })).toBe("Command is required.");
  expect(validateDialogFields(fields, { ...valid, preset: "", command: "agent" })).toBeNull();
  expect(validateDialogFields(fields, { ...valid, preset: "unknown" })).toBe("Choose agent.");
});
