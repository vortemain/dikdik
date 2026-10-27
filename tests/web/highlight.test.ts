import { expect, test } from "bun:test";
import { grammarFor, highlight, highlightLines, HIGHLIGHT_LIMIT } from "../../dikdik/web/highlight";

const SAMPLES: Record<string, string> = {
  python: 'import os\n\n@cache\ndef add(a, b):\n    """Sum.\n    Two lines."""\n    return a + b  # done\nprint(f"x={add(1, 2)}", None)\n',
  typescript: "// note\nexport async function load(id: string): Promise<Item> {\n  const url = `/api/${id}`; /* multi\n line */\n  return fetch(url) as Item;\n}\n",
  json: '{"name": "dikdik", "count": 3, "ok": true, "none": null, "list": [1.5e3]}',
  bash: "# deploy\nexport PATH=$HOME/bin:$PATH\nif [ -f x ]; then echo \"hi ${USER}\" --flag; fi\n",
  rust: "#[derive(Debug)]\nfn main() { let s: &'static str = \"x\"; println!(\"{}\", s); } // c\n",
  html: '<!-- c --><div class="a" data-x=\'1\'>Tom &amp; Jerry</div>',
  css: ".a, #b:hover { color: #fff; margin: 0 4px; width: calc(100% - 2rem); } /* c */ @media (x) {}",
  yaml: "# c\nname: dikdik\nlist:\n  - one: 'two'\n  enabled: true\n",
  toml: '[package]\nname = "dikdik"\nversion = 1  # c\n',
  markdown: "# Title\n\nSome `code` and **bold** [link](https://x.y).\n- item\n",
  go: 'package main\nfunc main() { fmt.Println("hi", nil) }\n',
  unknown: "anything <b> at all",
};

test("tokens always join back to the exact source", () => {
  for (const [lang, text] of Object.entries(SAMPLES)) {
    expect(highlight(text, lang).map(([, value]) => value).join("")).toBe(text);
    expect(highlightLines(text, lang).map(line => line.map(([, v]) => v).join("")).join("\n")).toBe(text);
  }
});

test("python parts are recognised", () => {
  const kinds = (value: string) => highlight(SAMPLES.python!, "py").filter(([, v]) => v.includes(value)).map(([k]) => k);
  expect(kinds("def")).toContain("keyword");
  expect(kinds('"""Sum.')).toContain("string");
  expect(kinds("# done")).toContain("comment");
  expect(kinds("@cache")).toContain("meta");
  expect(kinds("None")).toContain("builtin");
  expect(highlight("add(1)", "python")[0]).toEqual(["fn", "add"]);
});

test("json keys differ from values, and unknown languages stay plain", () => {
  const tokens = highlight(SAMPLES.json!, "json");
  expect(tokens).toContainEqual(["attr", '"name"']);
  expect(tokens).toContainEqual(["string", '"dikdik"']);
  expect(tokens).toContainEqual(["builtin", "true"]);
  expect(highlight("x", "cobol")).toEqual([["", "x"]]);
  expect(grammarFor("TSX")).toBe("javascript");
  expect(highlight("x".repeat(HIGHLIGHT_LIMIT + 1), "python")).toHaveLength(1);
});

test("a comment spanning lines stays a comment on every line", () => {
  const lines = highlightLines("a /* one\ntwo */ b", "javascript");
  expect(lines[1]![0]).toEqual(["comment", "two */"]);
});
