// Code block, diff and link rendering — pure DOM builders that depend only
// on dom.ts helpers and imported highlight/icon functions. No app state.
import { make, icon, copyButton } from "./dom";
import { detectLanguage, highlight, type Token } from "./highlight";
import { fileIcon } from "./icons";

export function languageFor(path: unknown): string {
  return typeof path === "string" ? detectLanguage(path).lang : "";
}

export function looksLikeDiff(text: string): boolean {
  return /^(diff --git |--- |@@ )/m.test(text) && /^[+-]/m.test(text);
}

/** Diff lines as spans; joined textContent equals the input exactly. */
export function diffLines(text: string): HTMLElement[] {
  const lines = text.split("\n");
  return lines.map((line, index) => {
    const kind = line.startsWith("@@") ? "hunk"
      : /^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(line) ? "meta"
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
    return make("span", `dl dl-${kind}`, line + (index < lines.length - 1 ? "\n" : ""));
  });
}

/** Coloured spans for highlighter tokens; plain text stays a text node. */
export function tokenNodes(tokens: Token[]): Node[] {
  return tokens.map(([kind, value]) => kind ? make("span", `tok tok-${kind}`, value) : document.createTextNode(value));
}

export function codeBlock(text: string, lang = "", options: { wrap?: boolean; filename?: string } = {}): HTMLElement {
  const block = make("div", options.wrap ? "code-block wrap" : "code-block");
  const head = make("div", "code-head");
  const label = make("span", "code-lang", options.filename || lang || "text");
  label.prepend(fileIcon(options.filename || `snippet.${lang}`, lang));
  head.append(label, copyButton(() => text));
  const pre = make("pre"), code = make("code");
  if (lang === "diff" || (!lang && looksLikeDiff(text))) code.append(...diffLines(text));
  else code.append(...tokenNodes(highlight(text, lang)));
  pre.append(code);
  block.append(head, pre);
  return block;
}

/** The address a browser approval wants to open, as a readable link — only if it is a real
 *  public http(s) address; agent-supplied text never becomes an executable href. */
export function safePageLink(url: unknown): HTMLElement | null {
  if (typeof url !== "string" || !url) return null;
  let target: URL;
  try { target = new URL(url); } catch { return null; }
  if (target.protocol !== "https:" && target.protocol !== "http:") return null;
  const link = make("a", "confirm-url", url);
  link.href = target.href;
  link.target = "_blank"; link.rel = "noopener noreferrer";
  link.title = "Open this address in your own browser to see what it is";
  const row = make("div", "confirm-url");
  row.append(icon("globe"), link);
  return row;
}