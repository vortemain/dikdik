// The reply appears as if typed: streamed text arrives from models in bursts (often ~100+
// characters at a time, after a pause), so it is revealed at a steady, adaptive pace.
// Finished paragraphs are rendered once and kept, so drawings and formulas do not flicker;
// only the paragraph being typed is redrawn. Formulas, emphasis, links and drawings are
// revealed whole: raw "$\frac{" or "**" never flashes on screen.

/** Characters per millisecond at the slowest (a comfortable reading pace when the model is slow). */
const MIN_RATE = 0.06;
/** Time constant of the catch-up: a burst of a few hundred characters is typed out in well under a second. */
const CATCH_UP_MS = 350;

/** How many characters to show after `elapsed` ms, given `shown` already and `available` in total. */
export function nextReveal(shown: number, available: number, elapsed: number): number {
  if (shown >= available) return available;
  const backlog = available - shown;
  const rate = Math.max(MIN_RATE, backlog / CATCH_UP_MS);
  return Math.min(available, shown + Math.max(1, Math.round(rate * Math.max(0, elapsed))));
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/;

/** Where text is unfinished at `cut`, extend the cut past the construct when `text` already
 * closes it, or pull it back to where the construct starts. Keeps Markdown and LaTeX whole. */
export function safeCut(text: string, cut: number): number {
  if (cut <= 0 || cut >= text.length) return Math.max(0, Math.min(cut, text.length));
  // Block level: inside a code/drawing fence or a $$ display formula, show it whole or not at all.
  const lineStarts: number[] = [0];
  for (let i = text.indexOf("\n"); i !== -1 && i < cut; i = text.indexOf("\n", i + 1)) lineStarts.push(i + 1);
  let open: { start: number; closer: RegExp } | null = null;
  for (const start of lineStarts) {
    const end = text.indexOf("\n", start);
    const line = text.slice(start, end === -1 ? text.length : end);
    if (open) {
      if (open.closer.test(line)) open = null;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) { open = { start, closer: new RegExp(`^ {0,3}${fence[1]![0] === "`" ? "`" : "~"}{${fence[1]!.length},}\\s*$`) }; continue; }
    const trimmed = line.trim();
    if (trimmed.startsWith("$$") && ((trimmed.match(/\$\$/g) ?? []).length % 2 === 1)) open = { start, closer: /\$\$/ };
    else if (trimmed.startsWith("\\[") && !trimmed.includes("\\]")) open = { start, closer: /\\\]/ };
  }
  if (open) {
    // Find where the block closes in the text we already have.
    let position = open.start;
    let first = true;
    while (position < text.length) {
      const end = text.indexOf("\n", position);
      const line = text.slice(position, end === -1 ? text.length : end);
      if (!first && open.closer.test(line)) return end === -1 ? text.length : end;
      if (first && open.closer.source === "\\$\\$" && (line.trim().match(/\$\$/g) ?? []).length >= 2) return end === -1 ? text.length : end;
      first = false;
      if (end === -1) break;
      position = end + 1;
    }
    return open.start;  // not finished yet: wait at the start of the block
  }
  // Inline: an unclosed $…$, \(…\), **…**, `…` or [link](…) on the current line.
  const lineStart = lineStarts[lineStarts.length - 1]!;
  const lineEnd = text.indexOf("\n", cut) === -1 ? text.length : text.indexOf("\n", cut);
  const before = text.slice(lineStart, cut);
  const pairs: [RegExp, string][] = [[/`/g, "`"], [/\*\*/g, "**"], [/(?<!\\)\$(?!\$)/g, "$"]];
  for (const [pattern, closer] of pairs) {
    const count = (before.replace(/\$\$/g, "").match(pattern) ?? []).length;
    if (count % 2 === 1) {
      const close = text.indexOf(closer, cut);
      if (close !== -1 && close < lineEnd) return close + closer.length;
      const opened = before.lastIndexOf(closer);
      return lineStart + Math.max(0, opened);
    }
  }
  const paren = before.lastIndexOf("\\("), shut = before.lastIndexOf("\\)");
  if (paren > shut) {
    const close = text.indexOf("\\)", cut);
    return close !== -1 && close < lineEnd ? close + 2 : lineStart + paren;
  }
  const bracket = before.lastIndexOf("["), bracketEnd = before.lastIndexOf(")");
  if (bracket > bracketEnd && /\[[^\]]*(\]\([^)]*)?$/.test(before.slice(bracket))) {
    const close = /\[[^\]\n]*\]\([^)\n]*\)/y;
    close.lastIndex = lineStart + bracket;
    const match = close.exec(text);
    if (match) return match.index + match[0].length;
  }
  // Finish the word being typed (a few letters at most) so words do not appear in pieces.
  const word = /^[^\s$`*\\[]{1,12}/.exec(text.slice(cut, lineEnd));
  return word ? cut + word[0].length : cut;
}

/** End of the finished part of `text`: just after the last blank line outside fences and
 * display formulas. Everything before it is complete Markdown that will not change. */
export function stableEnd(text: string): number {
  let fence: RegExp | null = null, display = false, details = 0, end = 0, position = 0;
  const lines = text.split("\n");
  for (let index = 0; index < lines.length - 1; index++) {  // the last line may still be growing
    const line = lines[index]!;
    position += line.length + 1;
    const trimmed = line.trim();
    if (fence) { if (fence.test(line)) fence = null; continue; }
    const opener = FENCE.exec(line);
    if (opener) { fence = new RegExp(`^ {0,3}${opener[1]![0] === "`" ? "`" : "~"}{${opener[1]!.length},}\\s*$`); continue; }
    if ((trimmed.match(/\$\$/g) ?? []).length % 2 === 1) display = !display;
    details += (line.match(/<details(?:\s[^>]*)?>/gi) ?? []).length - (line.match(/<\/details\s*>/gi) ?? []).length;
    if (!trimmed && !display && details <= 0 && !isListContinuation(lines, index)) end = position;
  }
  return end;
}
/** A blank line inside a list (more items or indented content follow) is not a safe break. */
function isListContinuation(lines: string[], blank: number): boolean {
  const previous = lines.slice(0, blank).reverse().find(line => line.trim());
  const next = lines.slice(blank + 1).find(line => line.trim());
  if (!previous || !next) return false;
  const item = /^\s*(?:[-+*]|\d+[.)])\s/;
  return (item.test(previous) || /^\s{2,}\S/.test(previous)) && (item.test(next) || /^\s{2,}\S/.test(next));
}

/** Reveals a growing reply in `root` at a typing pace. `set()` gives it the text so far;
 * `finish()` gives the final text and a callback for when it has all been shown. */
export class Typewriter {
  private target = "";
  private raw = 0;       // how far the typing has got
  private shown = 0;     // how much is on screen (never mid-formula or mid-word)
  private stable = 0;    // characters already rendered into finished blocks
  private tail = "";
  private frame: number | null = null;
  private last = 0;
  private done: (() => void) | null = null;
  private readonly finished: HTMLElement;
  private readonly typing: HTMLElement;

  /** `around` wraps each paint, e.g. to keep the newest line in view when the reader is at the bottom. */
  constructor(private readonly root: HTMLElement, private readonly render: (text: string) => HTMLElement,
              private readonly animate: () => boolean, private readonly around: (paint: () => void) => void = paint => paint()) {
    this.finished = document.createElement("div");
    this.typing = document.createElement("div");
    this.typing.className = "typing";
    root.replaceChildren(this.finished, this.typing);
  }

  /** The reply so far. A reply that does not continue what is shown starts over. */
  set(text: string): void {
    if (!text.startsWith(this.target.slice(0, this.shown))) this.reset();
    this.target = text;
    this.schedule();
  }

  /** The final reply; `done` runs once all of it is on screen. */
  finish(text: string, done: () => void): void {
    this.done = done;
    this.set(text);
  }

  get behind(): boolean { return this.shown < this.target.length; }

  /** Show everything now (the tab is hidden, or the conversation is changing). */
  flush(): void {
    this.raw = this.shown = this.target.length;
    this.paint();
    this.complete();
  }

  stop(): void {
    if (this.frame !== null) (globalThis.cancelAnimationFrame ?? globalThis.clearTimeout)(this.frame);
    this.frame = null;
    this.done = null;
  }

  private reset(): void {
    this.raw = this.shown = this.stable = 0;
    this.tail = "";
    this.finished.replaceChildren();
    this.typing.replaceChildren();
  }

  private schedule(): void {
    if (this.frame !== null) return;
    this.last = this.last || now();
    const request = globalThis.requestAnimationFrame ?? ((callback: FrameRequestCallback) => globalThis.setTimeout(() => callback(now()), 16) as unknown as number);
    this.frame = request(() => { this.frame = null; this.tick(); });
  }

  private tick(): void {
    const time = now(), elapsed = Math.min(250, time - this.last);
    this.last = time;
    const instant = !this.animate() || (globalThis.document as Document | undefined)?.hidden === true;
    this.raw = instant ? this.target.length : nextReveal(this.raw, this.target.length, elapsed);
    // The final text is complete: never wait on a construct that will not close.
    const cut = this.done && this.raw >= this.target.length ? this.target.length : safeCut(this.target, this.raw);
    this.shown = Math.max(this.shown, cut);
    this.paint();
    if (this.raw < this.target.length || (this.done && this.shown < this.target.length)) this.schedule();
    else if (this.shown >= this.target.length) this.complete();
  }

  private complete(): void {
    const done = this.done;
    this.done = null;
    done?.();
  }

  private paint(): void {
    this.around(() => this.draw());
  }

  private draw(): void {
    const visible = this.target.slice(0, this.shown);
    const end = stableEnd(visible);
    if (end > this.stable) {
      this.finished.append(this.render(visible.slice(this.stable, end)));
      this.stable = end;
    }
    const tail = visible.slice(this.stable);
    if (tail === this.tail) return;
    this.tail = tail;
    const caret = document.createElement("span");
    caret.className = "typing-caret";
    caret.setAttribute("aria-hidden", "true");
    if (!tail.trim()) { this.typing.replaceChildren(caret); return; }
    const rendered = this.render(tail);
    // The caret sits at the end of the last line being written, not on a line of its own.
    let host: Element = rendered;
    while (host.lastElementChild && /^(DIV|UL|OL|LI|BLOCKQUOTE|P|H[1-6]|DETAILS)$/.test(host.lastElementChild.tagName)
           && !host.lastElementChild.classList.contains("diagram")) host = host.lastElementChild;
    host.append(caret);
    this.typing.replaceChildren(rendered);
  }
}
const now = () => globalThis.performance?.now?.() ?? Date.now();
