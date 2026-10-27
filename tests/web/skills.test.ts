import { expect, test } from "bun:test";
import { facetCounts, filterSkills, isNetworkSource, type SkillRow } from "../../dikdik/web/skills-pane";

const rows: SkillRow[] = [
  { id: "tdd", name: "tdd", description: "Test first", scope: "installed", enabled: true, path: "", category: "testing" },
  { id: "style", name: "python-style", description: "Type hints", scope: "project", enabled: false, path: "", triggers: "lint" },
  { id: "notes", name: "notes", description: "How I like notes", scope: "personal", enabled: true, path: "" },
];
const none = { query: "", state: "", source: "", category: "" } as const;

test("search covers name, description, category and triggers", () => {
  expect(filterSkills(rows, { ...none, query: "TEST" }).map(r => r.id)).toEqual(["tdd"]);
  expect(filterSkills(rows, { ...none, query: "lint" }).map(r => r.id)).toEqual(["style"]);
  expect(filterSkills(rows, { ...none, query: "nothing" })).toEqual([]);
});
test("facets narrow by state, source and category, with counts", () => {
  expect(filterSkills(rows, { ...none, state: "off" }).map(r => r.id)).toEqual(["style"]);
  expect(filterSkills(rows, { ...none, source: "installed", category: "testing" }).map(r => r.id)).toEqual(["tdd"]);
  expect(facetCounts(rows)).toEqual({ source: { installed: 1, project: 1, personal: 1 }, category: { testing: 1 }, on: 2, off: 1 });
});
test("internet sources are recognised so they ask for approval", () => {
  expect(isNetworkSource("github:owner/repo")).toBe(true);
  expect(isNetworkSource(" https://example.com/x.git")).toBe(true);
  expect(isNetworkSource("file:///C:/skills")).toBe(false);
  expect(isNetworkSource("C:\\skills\\tdd")).toBe(false);
});
