import { expect, test } from "bun:test";
import { filterMcp, parseEnvKeys } from "../../dikdik/web/mcp-pane";

test("extra environment keys are explicit key references", () => {
  expect(parseEnvKeys("GITHUB_TOKEN=github\n SENTRY_TOKEN = sentry.key \n\n")).toEqual({
    GITHUB_TOKEN: "github", SENTRY_TOKEN: "sentry.key",
  });
  expect(() => parseEnvKeys("lower=key")).toThrow("Line 1");
  expect(() => parseEnvKeys("TOKEN=")).toThrow("Line 1");
  expect(() => parseEnvKeys("TOKEN=a\nTOKEN=b")).toThrow("listed twice");
  expect(() => parseEnvKeys(Array.from({ length: 11 }, (_, n) => `KEY_${n}=ref-${n}`).join("\n"))).toThrow("at most 10");
});

test("server and library search includes targets and descriptions", () => {
  const rows = [
    { name: "github", description: "Issues and pull requests", url: "https://example.test/mcp" },
    { name: "local-docs", description: "", command: "uvx docs-server" },
  ];
  expect(filterMcp(rows, "pull").map(row => row.name)).toEqual(["github"]);
  expect(filterMcp(rows, "uvx").map(row => row.name)).toEqual(["local-docs"]);
  expect(filterMcp(rows, "")).toEqual(rows);
});
