/**
 * Syntax colouring without dependencies: a small tokenizer per language.
 *
 * It returns [kind, text] pairs whose texts join back to the input exactly, so the
 * caller builds text nodes (never HTML) and copying a block still copies the source.
 * It is a highlighter, not a parser: a construct it does not know stays plain.
 */

export type TokenKind = "" | "keyword" | "string" | "comment" | "number" | "fn" | "type" | "builtin" | "attr" | "meta" | "tag" | "heading";
export type Token = [TokenKind, string];

interface Grammar {
  rules: [string, TokenKind | "word"][];  // regex sources; only non-capturing groups inside
  keywords?: Set<string>;
  builtins?: Set<string>;
  typesCapitalised?: boolean;
}

const words = (text: string) => new Set(text.split(/\s+/).filter(Boolean));
const NUMBER = String.raw`\b(?:0[xX][\da-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)[a-zA-Z]*\b`;
const DQ = String.raw`"(?:[^"\\\n]|\\.)*"?`;
const SQ = String.raw`'(?:[^'\\\n]|\\.)*'?`;
const WORD = String.raw`[A-Za-z_$][\w$]*`;
const C_COMMENTS: [string, TokenKind][] = [[String.raw`//[^\n]*`, "comment"], [String.raw`/\*[\s\S]*?(?:\*/|(?![\s\S]))`, "comment"]];

const JS_KEYWORDS = words(`break case catch class const continue debugger default delete do else export extends finally for from
  function if import in instanceof let new of return static super switch this throw try typeof var void while with yield async await
  as interface type enum implements private protected public readonly declare namespace abstract keyof satisfies`);
const C_LIKE = (keywords: string, builtins = ""): Grammar => ({
  rules: [...C_COMMENTS, [DQ, "string"], [SQ, "string"], [String.raw`#\s*\w+`, "meta"], [NUMBER, "number"], [WORD, "word"]],
  keywords: words(keywords), builtins: words(builtins), typesCapitalised: true,
});

const GRAMMARS: Record<string, Grammar> = {
  python: {
    rules: [[String.raw`#[^\n]*`, "comment"],
      [String.raw`(?:[rRbBuUfF]{1,2})?(?:"""[\s\S]*?(?:"""|(?![\s\S]))|'''[\s\S]*?(?:'''|(?![\s\S])))`, "string"],
      [String.raw`(?:[rRbBuUfF]{1,2})?(?:${DQ}|${SQ})`, "string"], [String.raw`@[\w.]+`, "meta"], [NUMBER, "number"], [WORD, "word"]],
    keywords: words(`and as assert async await break class continue def del elif else except finally for from global if import in is
      lambda nonlocal not or pass raise return try while with yield match case`),
    builtins: words(`True False None self cls print len range str int float list dict set tuple bool open super isinstance type object Exception`),
    typesCapitalised: true,
  },
  javascript: {
    rules: [...C_COMMENTS, [String.raw`\`(?:[^\`\\]|\\[\s\S])*\`?`, "string"], [DQ, "string"], [SQ, "string"], [NUMBER, "number"], [WORD, "word"]],
    keywords: JS_KEYWORDS, builtins: words(`true false null undefined NaN Infinity console window document globalThis Promise JSON Math`),
    typesCapitalised: true,
  },
  json: { rules: [[String.raw`"(?:[^"\\\n]|\\.)*"(?=\s*:)`, "attr"], [DQ, "string"], [NUMBER, "number"], [String.raw`\b(?:true|false|null)\b`, "builtin"]] },
  bash: {
    rules: [[String.raw`(?:^|(?<=\s))#[^\n]*`, "comment"], [DQ, "string"], [SQ, "string"], [String.raw`\$(?:\{[^}\n]*\}|\w+|[@#?$!*\d])`, "attr"],
      [String.raw`(?<=\s)--?[\w-]+`, "meta"], [NUMBER, "number"], [WORD, "word"]],
    keywords: words(`if then else elif fi for in do done while until case esac function return export local readonly set unset shift exit break continue`),
    builtins: words(`echo cd ls cat grep sed awk git npm npx bun python pip cargo node mkdir rm cp mv touch chmod sudo curl docker make source test`),
  },
  rust: {
    rules: [...C_COMMENTS, [String.raw`r#*"[\s\S]*?"#*`, "string"], [DQ, "string"], [String.raw`'[a-zA-Z_]\w*\b(?!')`, "meta"], [SQ, "string"],
      [String.raw`#!?\[[^\]\n]*\]`, "meta"], [String.raw`\b[a-z_]\w*!`, "fn"], [NUMBER, "number"], [WORD, "word"]],
    keywords: words(`as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut pub ref
      return self Self static struct super trait type unsafe use where while`),
    builtins: words(`true false Some None Ok Err Vec String Option Result Box usize isize u8 u16 u32 u64 i8 i16 i32 i64 f32 f64 bool str char`),
    typesCapitalised: true,
  },
  go: C_LIKE(`break case chan const continue default defer else fallthrough for func go goto if import interface map package range
    return select struct switch type var`, `true false nil iota string int int64 float64 bool error byte rune make len append new panic`),
  java: C_LIKE(`abstract boolean break byte case catch char class const continue default do double else enum extends final finally float
    for if implements import instanceof int interface long native new package private protected public return short static super switch
    synchronized this throw throws try void volatile while var record`, `true false null String System`),
  c: C_LIKE(`auto break case char const continue default do double else enum extern float for goto if inline int long register return
    short signed sizeof static struct switch typedef union unsigned void volatile while class namespace template typename public private
    protected virtual override new delete using try catch throw nullptr bool true false auto constexpr`, `NULL std size_t printf`),
  ruby: {
    rules: [[String.raw`#[^\n]*`, "comment"], [DQ, "string"], [SQ, "string"], [String.raw`:[A-Za-z_]\w*`, "attr"], [String.raw`@{1,2}\w+`, "meta"], [NUMBER, "number"], [WORD, "word"]],
    keywords: words(`alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue
      retry return self super then true undef unless until when while yield require attr_accessor`),
    typesCapitalised: true,
  },
  html: {
    rules: [[String.raw`<!--[\s\S]*?(?:-->|(?![\s\S]))`, "comment"], [String.raw`</?[A-Za-z][\w:-]*|/?>`, "tag"],
      [String.raw`\b[\w:-]+(?==)`, "attr"], [DQ, "string"], [SQ, "string"], [String.raw`&[#\w]+;`, "builtin"]],
  },
  css: {
    rules: [[String.raw`/\*[\s\S]*?(?:\*/|(?![\s\S]))`, "comment"], [DQ, "string"], [SQ, "string"], [String.raw`@[\w-]+`, "keyword"],
      [String.raw`#[\da-fA-F]{3,8}\b`, "number"], [String.raw`-?\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|dvh|s|ms|deg|fr)?\b`, "number"],
      [String.raw`[\w-]+(?=\s*:(?!:))`, "attr"], [String.raw`[.#][\w-]+|::?[\w-]+`, "type"], [String.raw`\b(?:var|calc|rgba?|hsla?|min|max|clamp|url)(?=\()`, "fn"]],
  },
  yaml: {
    rules: [[String.raw`#[^\n]*`, "comment"], [String.raw`^[ \t]*-?[ \t]*[\w.-]+(?=\s*:)`, "attr"], [DQ, "string"], [SQ, "string"],
      [String.raw`\b(?:true|false|null|yes|no|on|off)\b`, "builtin"], [NUMBER, "number"], [String.raw`[&*][\w-]+`, "meta"]],
  },
  toml: {
    rules: [[String.raw`#[^\n]*`, "comment"], [String.raw`^\s*\[\[?[^\]\n]*\]\]?`, "type"], [String.raw`^[ \t]*[\w.-]+(?=\s*=)`, "attr"],
      [String.raw`"""[\s\S]*?(?:"""|(?![\s\S]))`, "string"], [DQ, "string"], [SQ, "string"], [String.raw`\b(?:true|false)\b`, "builtin"], [NUMBER, "number"]],
  },
  markdown: {
    rules: [[String.raw`^#{1,6} [^\n]*`, "heading"], [String.raw`^\`\`\`[^\n]*`, "meta"], [String.raw`\`[^\`\n]+\``, "string"],
      [String.raw`\*\*[^*\n]+\*\*|__[^_\n]+__`, "keyword"], [String.raw`\[[^\]\n]*\]\([^)\n]*\)`, "attr"], [String.raw`^[ \t]*(?:[-*+]|\d+\.)(?= )`, "meta"]],
  },
};
const ALIASES: Record<string, string> = {
  py: "python", typescript: "javascript", ts: "javascript", tsx: "javascript", jsx: "javascript", js: "javascript", mjs: "javascript",
  sh: "bash", shell: "bash", zsh: "bash", console: "bash", powershell: "bash", ps1: "bash", rs: "rust", golang: "go", cpp: "c", "c++": "c",
  h: "c", hpp: "c", cs: "java", csharp: "java", kotlin: "java", kt: "java", swift: "java", rb: "ruby", xml: "html", svg: "html",
  vue: "html", scss: "css", less: "css", yml: "yaml", md: "markdown", jsonc: "json",
};
const compiled = new Map<string, RegExp>();
export const HIGHLIGHT_LIMIT = 300_000;  // larger texts stay plain so the page stays responsive

export function grammarFor(lang: string): string | null {
  const name = ALIASES[lang.toLowerCase()] ?? lang.toLowerCase();
  return name in GRAMMARS ? name : null;
}

export function highlight(text: string, lang: string): Token[] {
  const name = grammarFor(lang);
  if (!name || !text || text.length > HIGHLIGHT_LIMIT) return [["", text]];
  const grammar = GRAMMARS[name]!;
  let pattern = compiled.get(name);
  if (!pattern) {
    pattern = new RegExp(grammar.rules.map(([source]) => `(${source})`).join("|"), "gm");
    compiled.set(name, pattern);
  }
  pattern.lastIndex = 0;
  const tokens: Token[] = [];
  let last = 0;
  const push = (kind: TokenKind, value: string) => {
    if (!value) return;
    const previous = tokens[tokens.length - 1];
    if (previous && previous[0] === kind) previous[1] += value;
    else tokens.push([kind, value]);
  };
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    if (match[0] === "") { pattern.lastIndex++; continue; }
    const group = match.findIndex((value, index) => index > 0 && value !== undefined);
    const rule = grammar.rules[group - 1]!;
    push("", text.slice(last, match.index));
    let kind: TokenKind = rule[1] === "word" ? "" : rule[1];
    if (rule[1] === "word") {
      const word = match[0];
      const next = text.slice(match.index + word.length).match(/^\s*(.)/)?.[1];
      kind = grammar.keywords?.has(word) ? "keyword" : grammar.builtins?.has(word) ? "builtin"
        : next === "(" ? "fn" : grammar.typesCapitalised && /^[A-Z][a-z0-9]/.test(word) ? "type" : "";
    }
    push(kind, match[0]);
    last = match.index + match[0].length;
  }
  push("", text.slice(last));
  return tokens;
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
