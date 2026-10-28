import { expect, test } from "bun:test";
import { detectLanguage, fileFacts, projectStack } from "../../dikdik/web/highlight";
import { contextLabel, priceLabel } from "../../dikdik/web/providers-pane";

test("languages come from names, extensions and #! lines", () => {
  expect(detectLanguage("src/app.tsx")).toEqual({ lang: "tsx", label: "TypeScript React" });
  expect(detectLanguage("Dockerfile")).toEqual({ lang: "bash", label: "Dockerfile" });
  expect(detectLanguage("deploy/Dockerfile.prod").label).toBe("Dockerfile");
  expect(detectLanguage(".env.local").label).toBe("Environment");
  expect(detectLanguage("bin/run", "#!/usr/bin/env python3\nprint(1)")).toEqual({ lang: "python", label: "Python" });
  expect(detectLanguage("notes").label).toBe("Plain Text");
  expect(detectLanguage("data.parquet").label).toBe("PARQUET");
});

test("file facts for the status bar", () => {
  expect(fileFacts("def f():\n    return 1\n")).toEqual({ lines: 2, indent: "Spaces: 4", eol: "LF", bytes: 22 });
  expect(fileFacts("a {\r\n  b: 1;\r\n}\r\n")).toMatchObject({ lines: 3, indent: "Spaces: 2", eol: "CRLF" });
  expect(fileFacts("x\n\ty\n").indent).toBe("Tabs");
  expect(fileFacts("").lines).toBe(0);
});

test("project stack from its files", () => {
  expect(projectStack(["pyproject.toml", "package.json", "tsconfig.json", "bun.lock", "Dockerfile"])).toEqual(["Python", "TypeScript", "Bun", "Docker"]);
  expect(projectStack(["Cargo.toml", "src/main.rs"])).toEqual(["Rust"]);
  expect(projectStack(["README.md"])).toEqual([]);
});

test("OpenRouter-style prices and context sizes", () => {
  expect(priceLabel({ id: "m", name: "m", context: null, prompt_price: "0.00000015", completion_price: "0.0000006" })).toBe("$0.15 in · $0.60 out per 1M");
  expect(priceLabel({ id: "m", name: "m", context: null, prompt_price: "0", completion_price: "0" })).toBe("Free");
  expect(priceLabel({ id: "m", name: "m", context: null, prompt_price: null, completion_price: null })).toBe("");
  expect(contextLabel(128000)).toBe("128K context");
  expect(contextLabel(1048576)).toBe("1M context");
  expect(contextLabel(null)).toBe("");
});
