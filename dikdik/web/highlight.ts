/** Offline syntax grammars, bundled at build time. No HTML from source is inserted. */
import hljs, { type Emitter } from "highlight.js";

export type TokenKind = "" | "keyword" | "string" | "comment" | "number" | "fn" | "type" | "builtin" | "attr" | "meta" | "tag" | "heading";
export type Token = [TokenKind, string];
export const HIGHLIGHT_LIMIT = 300_000;

function kindFor(scope: string): TokenKind {
  const name = scope.split(".")[0];
  if (["keyword", "operator"].includes(name ?? "")) return "keyword";
  if (["string", "regexp", "char", "subst"].includes(name ?? "")) return "string";
  if (["comment", "quote", "doctag"].includes(name ?? "")) return "comment";
  if (["number", "literal"].includes(name ?? "")) return name === "literal" ? "builtin" : "number";
  if (["built_in", "variable"].includes(name ?? "")) return "builtin";
  if (["title", "function"].includes(name ?? "")) return scope.includes("class") ? "type" : "fn";
  if (["type", "class"].includes(name ?? "")) return "type";
  if (["attr", "attribute", "property", "selector-attr"].includes(name ?? "")) return "attr";
  if (["tag", "name", "selector-tag"].includes(name ?? "")) return "tag";
  if (["section", "strong"].includes(name ?? "")) return "heading";
  if (["meta", "symbol", "bullet", "link"].includes(name ?? "")) return "meta";
  return "";
}

/** Pinned highlight.js Emitter API: produces text tokens instead of HTML markup. */
class TextEmitter implements Emitter {
  tokens: Token[] = [];
  scopes: TokenKind[] = [];
  startScope(name: string): void { this.scopes.push(kindFor(name) || this.scopes.at(-1) || ""); }
  endScope(): void { this.scopes.pop(); }
  openNode(name: string): void { this.startScope(name); }
  closeNode(): void { this.endScope(); }
  addText(text: string): void { this.push(this.scopes.at(-1) ?? "", text); }
  push(kind: TokenKind, text: string): void {
    if (!text) return;
    const last = this.tokens.at(-1);
    if (last?.[0] === kind) last[1] += text;
    else this.tokens.push([kind, text]);
  }
  __addSublanguage(emitter: Emitter): void {
    for (const [kind, text] of (emitter as TextEmitter).tokens) this.push(kind, text);
  }
  finalize(): void { /* tokens are emitted as source is consumed */ }
  toHTML(): string { return ""; }
}
hljs.configure({ __emitter: TextEmitter });

const ALIASES: Record<string, string> = {
  tsx: "typescript", jsx: "javascript", vue: "xml", svelte: "xml", astro: "xml", html: "xml", svg: "xml",
  shell: "bash", console: "shell", ps1: "powershell", cs: "csharp", "c#": "csharp", kt: "kotlin",
  rs: "rust", mdx: "markdown", yml: "yaml", jsonc: "json", h: "c", hpp: "cpp", hh: "cpp", mjs: "javascript", cjs: "javascript",
  tf: "hcl", tfvars: "hcl", ex: "elixir", exs: "elixir", clj: "clojure", cljs: "clojure", fs: "fsharp", fsx: "fsharp",
  v: "verilog", sv: "verilog", vhd: "vhdl", hs: "haskell", lhs: "haskell", jl: "julia", pl: "perl", pm: "perl",
  erl: "erlang", hrl: "erlang", ml: "ocaml", mli: "ocaml", pas: "delphi", pp: "delphi", r: "r", rkt: "scheme",
  f: "fortran", f90: "fortran", f95: "fortran", tex: "latex", proto: "protobuf", mk: "makefile", bat: "dos", cmd: "dos",
};
export function supportedLanguages(): string[] { return hljs.listLanguages(); }
export function languageLabel(id: string): string { return hljs.getLanguage(id)?.name ?? id; }
export function grammarFor(lang: string): string | null {
  const name = lang.toLowerCase().replace(/^language-/, "");
  const found = ALIASES[name] ?? name;
  const grammar = hljs.getLanguage(found);
  if (!grammar) return null;
  return hljs.listLanguages().find(id => hljs.getLanguage(id) === grammar) ?? null;
}
export function highlight(text: string, lang: string): Token[] {
  const name = grammarFor(lang);
  if (!name || !text || text.length > HIGHLIGHT_LIMIT) return [["", text]];
  try {
    const result = hljs.highlight(text, { language: name, ignoreIllegals: true });
    const tokens = (result._emitter as TextEmitter).tokens;
    // Preserve copying even if a future grammar/emitter changes its contract.
    return tokens.map(([, value]) => value).join("") === text ? tokens : [["", text]];
  } catch { return [["", text]]; }
}

/** The same tokens cut at line breaks, for numbered file views (a token may span lines). */
export function highlightLines(text: string, lang: string): Token[][] {
  const lines: Token[][] = [[]];
  for (const [kind, value] of highlight(text, lang)) {
    const parts = value.split("\n");
    parts.forEach((part, index) => {
      if (index > 0) lines.push([]);
      if (part) lines[lines.length - 1]!.push([kind, part]);
    });
  }
  return lines;
}

// --- what a file is, from its name and first line, like an editor's language mode ----
const EXTENSIONS: Record<string, [string, string]> = {
  py: ["python", "Python"], pyi: ["python", "Python"], ts: ["typescript", "TypeScript"], tsx: ["tsx", "TypeScript React"],
  js: ["javascript", "JavaScript"], mjs: ["javascript", "JavaScript"], cjs: ["javascript", "JavaScript"], jsx: ["jsx", "JavaScript React"],
  json: ["json", "JSON"], jsonc: ["jsonc", "JSON with Comments"], md: ["markdown", "Markdown"], markdown: ["markdown", "Markdown"],
  rs: ["rust", "Rust"], go: ["go", "Go"], java: ["java", "Java"], kt: ["kotlin", "Kotlin"], cs: ["csharp", "C#"], swift: ["swift", "Swift"],
  c: ["c", "C"], h: ["c", "C"], cpp: ["cpp", "C++"], cc: ["cpp", "C++"], hpp: ["cpp", "C++"], rb: ["ruby", "Ruby"], php: ["php", "PHP"],
  sh: ["bash", "Shell"], bash: ["bash", "Shell"], zsh: ["bash", "Shell"], ps1: ["powershell", "PowerShell"], bat: ["dos", "Batch"], cmd: ["dos", "Batch"],
  css: ["css", "CSS"], scss: ["css", "SCSS"], less: ["css", "Less"], html: ["html", "HTML"], htm: ["html", "HTML"], xml: ["xml", "XML"],
  svg: ["xml", "SVG"], vue: ["vue", "Vue"], yml: ["yaml", "YAML"], yaml: ["yaml", "YAML"], toml: ["toml", "TOML"], ini: ["ini", "INI"],
  cfg: ["ini", "Config"], env: ["bash", "Environment"], sql: ["sql", "SQL"], txt: ["", "Plain Text"], lock: ["", "Lock file"], csv: ["", "CSV"],
};
const NAMES: Record<string, [string, string]> = {
  dockerfile: ["dockerfile", "Dockerfile"], makefile: ["makefile", "Makefile"], ".gitignore": ["bash", "Ignore"], ".dockerignore": ["bash", "Ignore"],
  ".env": ["bash", "Environment"], ".editorconfig": ["toml", "EditorConfig"], "cargo.lock": ["toml", "TOML"], "license": ["", "Plain Text"],
  "go.mod": ["go", "Go Module"], "requirements.txt": ["bash", "Pip Requirements"], "procfile": ["bash", "Procfile"],
};
const SHEBANGS: [RegExp, [string, string]][] = [[/python/, ["python", "Python"]], [/\b(node|bun|deno)\b/, ["javascript", "JavaScript"]],
  [/\b(ba|z)?sh\b/, ["bash", "Shell"]], [/ruby/, ["ruby", "Ruby"]]];

/** The highlighter language and the name an editor would show for this file. */
export function detectLanguage(path: string, text = ""): { lang: string; label: string } {
  const name = path.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "";
  const special = NAMES[name] ?? ((name === ".env" || name.startsWith(".env.")) ? NAMES[".env"] : name.startsWith("dockerfile") ? NAMES["dockerfile"] : undefined);
  if (special) return { lang: special[0], label: special[1] };
  const ext = name.includes(".") ? name.split(".").pop() ?? "" : "";
  const known = EXTENSIONS[ext];
  if (known) return { lang: known[0], label: known[1] };
  const grammar = grammarFor(ext);
  if (grammar) return { lang: grammar, label: hljs.getLanguage(grammar)?.name ?? grammar };
  const first = text.slice(0, 200).split("\n", 1)[0] ?? "";
  if (first.startsWith("#!")) {
    for (const [pattern, found] of SHEBANGS) if (pattern.test(first)) return { lang: found[0], label: found[1] };
  }
  return { lang: "", label: ext ? ext.toUpperCase() : "Plain Text" };
}

/** Indentation, line endings and size for an editor-style status bar. */
export function fileFacts(text: string): { lines: number; indent: string; eol: string; bytes: number } {
  const lines = text.length ? text.split("\n").length - (text.endsWith("\n") ? 1 : 0) : 0;
  let tabs = 0, spaces = 0;
  const widths = new Map<number, number>();
  for (const line of text.split("\n").slice(0, 2000)) {
    if (line.startsWith("\t")) tabs++;
    else {
      const lead = /^( +)\S/.exec(line)?.[1]?.length ?? 0;
      if (lead) { spaces++; for (const size of [2, 4, 8]) if (lead % size === 0) widths.set(size, (widths.get(size) ?? 0) + 1); }
    }
  }
  const size = [4, 2, 8].find(n => (widths.get(n) ?? 0) >= spaces * 0.9) ?? 4;
  const indent = tabs > spaces ? "Tabs" : spaces ? `Spaces: ${size}` : "—";
  return { lines, indent, eol: text.includes("\r\n") ? "CRLF" : "LF", bytes: new TextEncoder().encode(text).length };
}

/** What kind of project a set of paths looks like, for the explorer header. */
export function projectStack(paths: string[]): string[] {
  const names = new Set(paths.map(p => p.toLowerCase()));
  const has = (...files: string[]) => files.some(f => names.has(f));
  const stack: string[] = [];
  if (has("pyproject.toml", "requirements.txt", "setup.py", "pipfile") || paths.some(p => p.endsWith(".py"))) stack.push("Python");
  if (has("tsconfig.json")) stack.push("TypeScript");
  if (has("package.json")) stack.push(has("bun.lock", "bun.lockb") ? "Bun" : "Node");
  if (has("cargo.toml")) stack.push("Rust");
  if (has("go.mod")) stack.push("Go");
  if (has("pom.xml", "build.gradle", "build.gradle.kts")) stack.push("Java");
  if (has("gemfile")) stack.push("Ruby");
  if (has("composer.json")) stack.push("PHP");
  if (has("dockerfile", "docker-compose.yml", "compose.yaml")) stack.push("Docker");
  return stack;
}
