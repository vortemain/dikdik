import { describe, expect, test } from "bun:test";
import { alertKind, fileTree, fuzzyScore, organiseTasks, parentFolders, parseTable, parseTags, rankCommands, workspacePath } from "../../dikdik/web/organise";
import type { TaskSummary } from "../../dikdik/web/contracts";

const now = 1_000_000;
const task = (id: string, extra: Partial<TaskSummary> = {}): TaskSummary => ({ id, state: "completed", objective: id, created: now - 100, updated: now - 100, ...extra });
const view = { query: "", tag: "", sort: "recent" as const, sinceDays: 0, showArchived: false, selected: null, now };

describe("conversation list", () => {
  const tasks = [
    task("b-old", { updated: now - 40 * 86400, created: now - 40 * 86400 }),
    task("a-pinned", { pinned: true, updated: now - 90 * 86400 }),
    task("c-bug", { folder: "Bugs", tags: ["ui"] }),
    task("d-bug", { folder: "Bugs", updated: now - 10 }),
    task("e-docs", { folder: "Docs", tags: ["ui", "docs"] }),
    task("f-archived", { archived: true }),
  ];
  test("groups pinned, folders and the rest; hides archived", () => {
    const out = organiseTasks(tasks, view);
    expect(out.pinned.map(t => t.id)).toEqual(["a-pinned"]);
    expect(out.folders.map(f => [f.name, f.tasks.map(t => t.id)])).toEqual([["Bugs", ["d-bug", "c-bug"]], ["Docs", ["e-docs"]]]);
    expect(out.recent.map(t => t.id)).toEqual(["b-old"]);
    expect(out.tags).toEqual(["docs", "ui"]);
    expect(out.folderNames).toEqual(["Bugs", "Docs"]);
    expect(out.archived).toBe(1);
  });
  test("filters by tag, search and age; pinned survive the age filter", () => {
    expect(organiseTasks(tasks, { ...view, tag: "ui" }).folders.flatMap(f => f.tasks.map(t => t.id))).toEqual(["c-bug", "e-docs"]);
    expect(organiseTasks(tasks, { ...view, query: "docs" }).folders.map(f => f.name)).toEqual(["Docs"]);
    const recent = organiseTasks(tasks, { ...view, sinceDays: 30 });
    expect(recent.older).toBe(1);
    expect(recent.pinned.map(t => t.id)).toEqual(["a-pinned"]);
  });
  test("sorts by title and keeps the open conversation visible", () => {
    expect(organiseTasks(tasks, { ...view, sort: "title" }).folders[0]!.tasks.map(t => t.id)).toEqual(["c-bug", "d-bug"]);
    expect(organiseTasks(tasks, { ...view, selected: "f-archived" }).recent.map(t => t.id)).toContain("f-archived");
  });
  test("tags are normalised and validated", () => {
    expect(parseTags(" UI, ui ,needs review")).toEqual(["needs review", "ui"]);
    expect(parseTags("")).toEqual([]);
    expect(() => parseTags("#nope")).toThrow("not a valid tag");
    expect(() => parseTags(Array.from({ length: 11 }, (_, i) => `t${i}`).join(","))).toThrow("at most 10");
  });
});

describe("command menu ranking", () => {
  test("prefix beats word beats substring beats subsequence", () => {
    expect(fuzzyScore("new", "New task")).toBeGreaterThan(fuzzyScore("task", "New task"));
    expect(fuzzyScore("task", "New task")).toBeGreaterThan(fuzzyScore("ask", "New task"));
    expect(fuzzyScore("nt", "New task")).toBeGreaterThan(0);
    expect(fuzzyScore("zz", "New task")).toBe(-1);
  });
  test("keywords also match", () => {
    const items = [{ label: "Open settings: API keys", keywords: "secrets credentials" }, { label: "New task" }];
    expect(rankCommands("secrets", items).map(i => i.label)).toEqual(["Open settings: API keys"]);
    expect(rankCommands("", items)).toHaveLength(2);
  });
});

describe("file tree", () => {
  const paths = ["src/b.py", "src/a.py", "src/lib/x.ts", "README.md", "a.txt"];
  test("folders first, collapsed until expanded", () => {
    expect(fileTree(paths, new Set()).map(r => r.path)).toEqual(["src", "a.txt", "README.md"]);
    expect(fileTree(paths, new Set(["src"])).map(r => `${r.depth}:${r.path}`)).toEqual(["0:src", "1:src/lib", "1:src/a.py", "1:src/b.py", "0:a.txt", "0:README.md"]);
    expect(parentFolders("src/lib/x.ts")).toEqual(["src", "src/lib"]);
  });
});

describe("markdown extras", () => {
  test("alerts", () => {
    expect(alertKind("> [!WARNING]")).toBe("warning");
    expect(alertKind("> [!note]  ")).toBe("note");
    expect(alertKind("> plain quote")).toBeNull();
  });
  test("tables with alignment and escaped pipes", () => {
    const lines = ["| Name | Size |", "|:-----|-----:|", "| a \\| b | 1 |", "| c |", "", "after"];
    expect(parseTable(lines, 0)).toEqual({ header: ["Name", "Size"], align: ["left", "right"], rows: [["a | b", "1"], ["c", ""]], end: 4 });
    expect(parseTable(["| a |", "not a rule"], 0)).toBeNull();
    expect(parseTable(["| a | b |", "|:-|-:|", "| 1 | 2 |"], 0)?.align).toEqual(["left", "right"]);
  });
  test("workspace paths only for known files", () => {
    const files = new Set(["src/app.py"]);
    expect(workspacePath("src/app.py:12", files)).toEqual({ path: "src/app.py", line: 12 });
    expect(workspacePath("./src/app.py", files)).toEqual({ path: "src/app.py" });
    expect(workspacePath("src/other.py", files)).toBeNull();
    expect(workspacePath("rm -rf /", files)).toBeNull();
  });
});
