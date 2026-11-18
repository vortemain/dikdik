// Formulas in replies, rendered with KaTeX (bundled; no network).
//
// KaTeX's render() builds DOM nodes and sets styles through the CSSOM, so it works
// under Dikdik's strict Content-Security-Policy (no inline <style> or style="" markup).
// Output carries MathML too, so screen readers read the formula. Bad TeX never throws:
// the source is shown in place with the error as a tooltip.

import katex from "katex";

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

/** One formula. `display` centres it on its own line, like a textbook equation. */
export function renderMath(tex: string, display: boolean): HTMLElement {
  const holder = document.createElement(display ? "div" : "span");
  holder.className = display ? "math-display" : "math-inline";
  const source = tex.trim();
  holder.dataset.tex = source;
  try {
    katex.render(source, holder, {
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

/** Inline formula delimiters: \( \), $$ $$ (inline), and $ $ that cannot be prices. */
export const INLINE_MATH = String.raw`\\\((.+?)\\\)|\$\$([^$\n]+?)\$\$|(?<![\\$\w])\$(?=[^\s$\d.,])((?:[^$\n\\]|\\.)+?)(?<=[^\s\\])\$(?![\w$])|(?<![\\$\w])\$(?=\d)((?:[^$\n\\]|\\.)*?[\\^_{}=+\-*/<>|][^$\n]*?)(?<=[^\s\\])\$(?![\w$])`;

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
