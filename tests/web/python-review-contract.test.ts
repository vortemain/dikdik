import { expect, test } from "bun:test";
import { reviewDiff } from "../../dikdik/web/contracts";

const python = Bun.which("python") ?? Bun.which("python3");

test.skipIf(!python)("reviews actual Python candidate diffs with spaces, Unicode paths and empty additions", () => {
  const script = `
import json
from dikdik.engine import candidate_diff, change_summary
original = {"app.py": "value = 1\\n"}
candidate = {"app.py": "value = 2\\n", "new feature.py": "# 🦌\\n", "café.py": "# text\\n", "package/__init__.py": ""}
diff = candidate_diff(original, candidate)
print(json.dumps({"diff": diff, "file_changes": change_summary(original, candidate, diff)}))
`;
  const result = Bun.spawnSync([python!, "-c", script], { cwd: process.cwd() });
  expect(result.exitCode).toBe(0);
  const data = JSON.parse(new TextDecoder().decode(result.stdout));
  const review = reviewDiff(data);
  expect(review.metadata).toBe("available");
  expect(review.files.map(file => file.path)).toEqual(["app.py", "café.py", "new feature.py", "package/__init__.py"]);
  expect(review.files.find(file => file.path === "new feature.py")?.diff).toContain("+# 🦌");
  expect(review.files.find(file => file.path === "package/__init__.py")?.diff).not.toContain("@@");
});
