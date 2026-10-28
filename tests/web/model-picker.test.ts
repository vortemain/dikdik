import { expect, test } from "bun:test";
import { matchesModel } from "../../dikdik/web/model-picker";

test("model search matches every term across names, IDs and providers", () => {
  expect(matchesModel("CLAUDE sonnet", "Claude Sonnet", "anthropic/claude-sonnet")).toBe(true);
  expect(matchesModel("qwen local", "qwen3-coder", "Local development")).toBe(true);
  expect(matchesModel("claude vision", "Claude Sonnet", "anthropic/claude-sonnet")).toBe(false);
  expect(matchesModel("   ", "Any model")).toBe(true);
  expect(matchesModel("[test]", "custom/[test]")).toBe(true);
});
