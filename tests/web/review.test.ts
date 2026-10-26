import { describe, expect, test } from "bun:test";
import { fileStatusLabel, reviewDiff } from "../../dikdik/web/contracts";

const added = "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000000..e69de29\n";
const modified = "diff --git a/main.py b/main.py\nindex 1234567..abcdef0 100644\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-print('old')\n+print('🦌 café 漢字')\n";
const deleted = "diff --git a/old.py b/old.py\ndeleted file mode 100644\nindex e69de29..0000000\n";
const changes = [
  { path: "empty.py", status: "added", start_line: 0, end_line: 3 },
  { path: "main.py", status: "modified", start_line: 3, end_line: 10 },
  { path: "old.py", status: "deleted", start_line: 10, end_line: 13 },
];
const result = { diff: added + modified + deleted, file_changes: changes };

describe("validated per-file review", () => {
  test("slices added empty, modified Unicode, and deleted files by LF line ranges", () => {
    const review = reviewDiff(result);
    expect(review.metadata).toBe("available");
    expect(review.files.map(file => file.diff)).toEqual([added, modified, deleted]);
    expect(review.files.map(file => fileStatusLabel(file.status))).toEqual(["Added", "Modified", "Deleted"]);
    expect(review.files[0]?.diff).toContain("new file mode");
    expect(review.files[0]?.diff).not.toContain("@@");
    expect(review.files[1]?.diff).toContain("🦌 café 漢字");
    expect(review.diff).toBe(result.diff);
  });

  test("historical results keep the full combined diff without inventing file metadata", () => {
    for (const legacy of [{ diff: result.diff }, { diff: result.diff, changed_files: ["main.py"] }]) {
      expect(reviewDiff(legacy)).toEqual({ diff: result.diff, files: [], metadata: "legacy" });
    }
    expect(reviewDiff(null)).toEqual({ diff: "", files: [], metadata: "legacy" });
    expect(reviewDiff({ diff: "", file_changes: [] })).toEqual({ diff: "", files: [], metadata: "available" });
  });

  test("rejects malformed, negative, fractional, reversed, unsafe and out-of-range indices", () => {
    for (const range of [
      { start_line: -1 }, { start_line: 0.5 }, { start_line: "0" }, { start_line: NaN },
      { end_line: 0 }, { end_line: -2 }, { end_line: 100 }, { end_line: Infinity },
      { end_line: Number.MAX_SAFE_INTEGER + 1 }, { end_line: "3" },
    ]) {
      const review = reviewDiff({ ...result, file_changes: [{ ...changes[0], ...range }, ...changes.slice(1)] });
      expect(review).toEqual({ diff: result.diff, files: [], metadata: "invalid" });
    }
  });

  test("does not show another file for a mislabeled, overlapping, incomplete or duplicated range", () => {
    const invalid = [
      [{ ...changes[0], path: "wrong.py" }, ...changes.slice(1)],
      [changes[0], { ...changes[1], start_line: 2 }, changes[2]],
      [changes[0]], [],
      [{ ...changes[0], end_line: 10 }, changes[2]],
      [changes[0], { ...changes[1], path: "empty.py" }, changes[2]],
      [{ ...changes[0], status: "modified" }, ...changes.slice(1)],
      [{ ...changes[0], status: "renamed" }, ...changes.slice(1)],
      [{ ...changes[0], path: "" }, ...changes.slice(1)],
    ];
    for (const file_changes of invalid) {
      expect(reviewDiff({ ...result, file_changes })).toEqual({ diff: result.diff, files: [], metadata: "invalid" });
    }
  });

  test("handles untrusted shapes and missing trailing LF without throwing", () => {
    for (const value of [false, 7, "bad", [], { diff: 7 }, { file_changes: {} }, { file_changes: [null] }, { file_changes: ["bad"] }]) {
      expect(() => reviewDiff(value)).not.toThrow();
      expect(reviewDiff(value).files).toEqual([]);
    }
    expect(reviewDiff({ ...result, diff: result.diff.slice(0, -1) }).metadata).toBe("invalid");
  });

  test("validates Unicode paths with default Git quoting or core.quotePath=false", () => {
    for (const header of [
      'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"',
      "diff --git a/café.py b/café.py",
    ]) {
      const diff = header + "\nnew file mode 100644\n";
      const review = reviewDiff({ diff, file_changes: [{ path: "café.py", status: "added", start_line: 0, end_line: 2 }] });
      expect(review.metadata).toBe("available");
      expect(review.files[0]?.diff).toBe(diff);
    }
  });

  test("preserves literal markup in paths and diffs as data", () => {
    const path = "<img src=x onerror=alert(1)>.py";
    const diff = `diff --git "a/${path}" "b/${path}"\nnew file mode 100644\n`;
    const review = reviewDiff({ diff, file_changes: [{ path, status: "added", start_line: 0, end_line: 2 }] });
    expect(review.files[0]?.path).toBe(path);
    expect(review.files[0]?.diff).toBe(diff);
  });

  test("rejects conflicting inner paths or a hidden second unified patch", () => {
    const bodies = [
      "--- a/actual.py\n+++ b/actual.py\n@@ -1 +1 @@\n-old\n+new\n",
      "--- a/shown.py\n+++ b/shown.py\n@@ -1 +1 @@\n-old\n+new\n--- a/actual.py\n+++ b/actual.py\n@@ -1 +1 @@\n-old\n+hidden\n",
      "rename from actual.py\nrename to shown.py\n",
    ];
    for (const body of bodies) {
      const diff = "diff --git a/shown.py b/shown.py\n" + body;
      const review = reviewDiff({ diff, file_changes: [{ path: "shown.py", status: "modified", start_line: 0, end_line: diff.split("\n").length - 1 }] });
      expect(review.metadata).toBe("invalid");
      expect(review.files).toEqual([]);
      expect(review.diff).toBe(diff);
    }
  });
});
