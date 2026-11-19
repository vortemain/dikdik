// Formulas in replies, rendered with KaTeX (bundled; no network).
//
// KaTeX's render() builds DOM nodes and sets styles through the CSSOM, so it works
// under Dikdik's strict Content-Security-Policy (no inline <style> or style="" markup).
// Output carries MathML too, so screen readers read the formula. Bad TeX never throws:
// the source is shown in place with the error as a tooltip.

import katex from "katex";
import "katex/contrib/mhchem";  // \ce{...} and \pu{...}: chemical formulas, reactions and units

/** Common teaching shorthands, so \R, \N, \E[X], \Var(X) and friends just work. */
const MACROS: Record<string, string> = {
  "\\R": "\\mathbb{R}", "\\N": "\\mathbb{N}", "\\Z": "\\mathbb{Z}", "\\Q": "\\mathbb{Q}", "\\C": "\\mathbb{C}",
  "\\E": "\\mathbb{E}", "\\P": "\\mathbb{P}", "\\Var": "\\operatorname{Var}", "\\Cov": "\\operatorname{Cov}",
  "\\sgn": "\\operatorname{sgn}", "\\argmax": "\\operatorname*{arg\\,max}", "\\argmin": "\\operatorname*{arg\\,min}",
  "\\d": "\\mathrm{d}", "\\dd": "\\,\\mathrm{d}", "\\deg": "^{\\circ}",
};

export function mathAvailable(): boolean {
  return typeof document !== "undefined" && typeof document.createElement === "function" && typeof katex?.render === "function";
}

const ELEMENTS = new Set(("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr " +
  "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir " +
  "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr R X").split(" "));

/** Chemistry written as plain maths ($CH_3-CH_2-OH$, $H_2O$, $Cl$) would come out in italics like
 * algebra. When every letter group is an element symbol and subscripts are plain numbers, typeset
 * it as chemistry instead (mhchem): upright symbols, real bonds. Returns null for anything else. */
export function chemistryTex(tex: string): string | null {
  const source = tex.trim();
  if (!/^[-=]?\d*[A-Z]/.test(source) || !/^[A-Za-z0-9_{}()\-=+\s]+$/.test(source) || /\s/.test(source.replace(/\s*([-=+])\s*/g, "$1"))) return null;
  // General formulas (C_nH_{2n+2}, C_nH_{2n}OH): upright element symbols, the subscripts stay maths.
  if (/_(?!\d|\{\d+\})/.test(source)) {
    const general = /^(?:[A-Z][a-z]?(?:_(?:[a-z0-9]|\{[a-z0-9+\-]+\}))?)+$/;
    const symbols = source.replace(/_(?:\{[^}]*\}|.)/g, "").match(/[A-Z][a-z]?/g) ?? [];
    if (!general.test(source) || symbols.length < 2 || !symbols.every(symbol => ELEMENTS.has(symbol)) || !symbols.some(s => CHEMISTRY_HINTS.has(s))) return null;
    return source.replace(/[A-Z][a-z]?/g, symbol => `\\mathrm{${symbol}}`);
  }
  // Chemists never write a subscript 0 or 1: H_0, H_1, x_1 are statistics or physics.
  if (/_(?:\{0*1?\}|[01])(?!\d)/.test(source)) return null;
  const plain = source.replace(/_\{(\d+)\}|_(\d)/g, "$1$2");
  const symbols = plain.match(/[A-Z][a-z]?/g) ?? [];
  // One symbol with a subscript (H_2, O_2, S_8) is chemistry only for elements that form such molecules.
  if (symbols.length === 1 && /\d/.test(plain) && !/^\d/.test(plain) && !MOLECULAR.has(symbols[0]!)) return null;
  if (plain.replace(/[A-Z][a-z]?|[\d()\-=+]/g, "") !== "" || !symbols.every(symbol => ELEMENTS.has(symbol))) return null;
  // Numbers must be subscripts (after a symbol or bracket) or coefficients before a formula:
  // "H = 2(3) + 2 = 8" is arithmetic, not chemistry.
  if (/(?:^|[^A-Za-z\d)])\d+(?![A-Z\d])/.test(plain)) return null;
  // Variables like P_1V_1 = P_2V_2 or I_1R_1: chemistry needs a common element (C, H, O, N…) or a two-letter symbol.
  if (!symbols.some(s => CHEMISTRY_HINTS.has(s) || s.length === 2)) return null;
  // A lone capital (V, I, R, O…) or a plain sum like A+B is more likely physics or algebra; Cl, Na, -OH are chemistry.
  if (symbols.length < 2 && !/\d/.test(plain) && !(symbols[0]?.length === 2) && !/^[-=]/.test(plain)) return null;
  if (/[=+]/.test(plain.slice(1)) && !/\d|[A-Z][a-z]?[A-Z]/.test(plain)) return null;
  return `\\ce{${plain}}`;
}
const CHEMISTRY_HINTS = new Set(["C", "H", "O", "N", "S"]);
const MOLECULAR = new Set(["H", "O", "N", "F", "Cl", "Br", "I", "S", "P"]);

/** One formula. `display` centres it on its own line, like a textbook equation. */
export function renderMath(tex: string, display: boolean): HTMLElement {
  const holder = document.createElement(display ? "div" : "span");
  holder.className = display ? "math-display" : "math-inline";
  const source = tex.trim();
  holder.dataset.tex = source;
  try {
    katex.render(chemistryTex(source) ?? source, holder, {
      displayMode: display, throwOnError: true, output: "htmlAndMathml", strict: "ignore", trust: false,
      macros: { ...MACROS }, maxSize: 50, maxExpand: 2000,
    });
  } catch (error) {
    holder.className += " math-error";
    holder.textContent = display ? source : `$${source}$`;
    holder.title = `Could not typeset this formula: ${error instanceof Error ? error.message.replace(/^KaTeX parse error: /, "") : String(error)}`;
    return holder;
  }
  if (display) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "math-copy";
    copy.textContent = "Copy LaTeX";
    copy.title = "Copy this equation as LaTeX";
    copy.onclick = () => { void navigator.clipboard?.writeText(source); copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy LaTeX"; }, 1200); };
    holder.append(copy);
  }
  return holder;
}

/** Copying text that contains formulas gives their LaTeX ($...$, $$...$$) instead of KaTeX's
 * doubled visual and MathML text, and leaves out toolbar buttons (Copy LaTeX, Replay…). */
export function copyWithTex(selection: Selection, data: DataTransfer): boolean {
  if (!selection.rangeCount || selection.isCollapsed) return false;
  const range = selection.getRangeAt(0).cloneRange();
  const holderOf = (node: Node) => (node.nodeType === 1 ? node as Element : node.parentElement)?.closest(".math-inline, .math-display");
  const start = holderOf(range.startContainer), end = holderOf(range.endContainer);
  if (start) range.setStartBefore(start);
  if (end) range.setEndAfter(end);
  const fragment = range.cloneContents();
  const formulas = fragment.querySelectorAll<HTMLElement>(".math-inline, .math-display");
  const extras = fragment.querySelectorAll(".math-copy, .sort-indicator, [data-nocopy]");
  const drawings = fragment.querySelectorAll<HTMLImageElement>("img.diagram-image");
  if (!formulas.length && !extras.length && !drawings.length) return false;
  extras.forEach(node => node.remove());
  drawings.forEach(image => image.replaceWith(document.createTextNode(image.alt && image.alt !== "Drawing" ? `[Drawing: ${image.alt}]` : "[Drawing]")));
  formulas.forEach(node => {
    const tex = node.dataset.tex ?? node.querySelector("annotation")?.textContent ?? node.textContent ?? "";
    node.replaceWith(document.createTextNode(node.classList.contains("math-display") ? `\n$$${tex}$$\n` : `$${tex}$`));
  });
  const holder = document.createElement("div");
  holder.append(fragment);
  data.setData("text/html", holder.innerHTML);
  data.setData("text/plain", plainText(holder).replace(/\n{3,}/g, "\n\n").trim());
  return true;
}

const BLOCKS = new Set(["P", "DIV", "DETAILS", "SUMMARY", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "TR", "PRE", "BLOCKQUOTE", "TABLE", "UL", "OL", "FIGURE"]);
function plainText(node: Node): string {
  if (node.nodeType === 3) return node.textContent ?? "";
  if (node.nodeType !== 1 && node.nodeType !== 11) return "";
  const element = node as Element;
  if (element.tagName === "BR") return "\n";
  const cells = element.tagName === "TR";
  const inner = [...node.childNodes].map(plainText).join(cells ? "\t" : "");
  const prefix = element.tagName === "LI" ? "- " : "";
  return BLOCKS.has(element.tagName) ? `${prefix}${inner}\n` : inner;
}

/** Inline formula delimiters: \( \), $$ $$ (inline), and $ $ that cannot be prices. A bare number
 * such as $3$ counts too: a price never has a second $ right after its digits. */
export const INLINE_MATH = String.raw`\\\((.+?)\\\)|\$\$([^$\n]+?)\$\$|(?<![\\$\w])\$(?=[^\s$\d.,])((?:[^$\n\\]|\\.)+?)(?<=[^\s\\])\$(?![\w$])|(?<![\\$\w])\$(?=\d)((?:[^$\n\\]|\\.)*?[\\^_{}=+\-*/<>|][^$\n]*?)(?<=[^\s\\])\$(?![\w$])|(?<![\\$\w])\$(\d+(?:\.\d+)?)\$(?![\w$])`;

/** Is this line the start of a display formula block? Returns its closing rule. */
export function displayStart(line: string): { close: RegExp; first: string } | null {
  const trimmed = line.trim();
  if (trimmed.startsWith("$$")) return { close: /\$\$\s*$/, first: trimmed.slice(2) };
  if (trimmed.startsWith("\\[")) return { close: /\\\]\s*$/, first: trimmed.slice(2) };
  const environment = /^\\begin\{(equation|align|gather|multline|alignat|eqnarray|flalign|cases|matrix|pmatrix|bmatrix|array)(\*?)\}/.exec(trimmed);
  if (environment) {
    const name = environment[1]! + (environment[2] ? "\\*" : "");
    return { close: new RegExp(`\\\\end\\{${name}\\}\\s*$`), first: trimmed };
  }
  return null;
}

/** Collect a display formula starting at lines[index]; null when it never closes (still streaming).
 * `rest` is any text after the closing delimiter on its line (e.g. "$$ x = 1 $$ ✓"). */
export function collectDisplay(lines: string[], index: number): { tex: string; end: number; rest?: string } | null {
  const start = displayStart(lines[index]!);
  if (!start) return null;
  const isEnvironment = start.first.startsWith("\\begin");
  if (!isEnvironment) {
    const closer = lines[index]!.trim().startsWith("$$") ? "$$" : "\\]";
    const parts: string[] = [];
    for (let cursor = index; cursor < lines.length && cursor < index + 200; cursor++) {
      const line = cursor === index ? start.first : lines[cursor]!;
      const at = line.indexOf(closer);
      if (at >= 0) {
        parts.push(line.slice(0, at));
        const rest = line.slice(at + closer.length).trim();
        return { tex: parts.join("\n"), end: cursor, ...(rest ? { rest } : {}) };
      }
      parts.push(line);
    }
    return null;
  }
  const strip = (text: string) => isEnvironment ? text : text.replace(start.close, "");
  // $$ x $$ or \[ x \] on one line
  if (!isEnvironment && start.first.trim() && start.close.test(start.first)) return { tex: strip(start.first), end: index };
  if (isEnvironment && start.close.test(start.first)) return { tex: start.first, end: index };
  const body = [start.first];
  for (let cursor = index + 1; cursor < lines.length && cursor < index + 200; cursor++) {
    const line = lines[cursor]!;
    if (start.close.test(line.trim())) {
      body.push(strip(line.trim()));
      return { tex: body.join("\n"), end: cursor };
    }
    body.push(line);
  }
  return null;
}
