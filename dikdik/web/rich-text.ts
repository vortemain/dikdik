/** DOM-only response rendering. Model text is never inserted as HTML. */
import { alertKind, parseTable } from './organise';
import { chartCard } from './response-chart';
import { renderMermaid } from './mermaid';
import { collectDisplay, INLINE_MATH, mathAvailable, renderMath } from './math';
import { renderPlot, renderSketch } from './sketch';
import { boardStyle, reducedMotion, type BoardStyle } from './board';

const writtenBoards = new Set<string>();
/** A ```board block: a worked solution written in handwriting on the chalkboard or whiteboard,
 * revealed line by line the first time it appears. Its content is ordinary Markdown and LaTeX. */
/** On a board every line is its own step: keep single line breaks instead of merging lines into
 * one paragraph as Markdown would. Display formulas, fences and tables are left whole. */
export function boardLines(source: string): string {
  const out: string[] = [];
  let fence = false, display = false, environments = 0;
  for (const line of source.replace(/\r\n?/g, '\n').split('\n')) {
    const trimmed = line.trim();
    const previous = out.length ? out[out.length - 1]!.trim() : '';
    const block = fence || display || environments > 0 || trimmed.startsWith('|') && previous.startsWith('|');
    if (!block && trimmed && previous) out.push('');
    out.push(line);
    if (/^(`{3,}|~{3,})/.test(trimmed)) fence = !fence;
    else if (!fence) {
      if ((trimmed.match(/\$\$/g) ?? []).length % 2 === 1) display = !display;
      environments = Math.max(0, environments + (trimmed.match(/\\begin\{/g) ?? []).length - (trimmed.match(/\\end\{/g) ?? []).length);
    }
  }
  return out.join('\n');
}

function boardNote(source: string, options: RichTextOptions, depth: number, language: string, closed: boolean): HTMLElement {
  const chosen = language === 'chalkboard' || language === 'whiteboard' ? language : boardStyle();
  const kind = (style: BoardStyle) => style === 'clean' ? 'board-note board-note-clean' : style === 'whiteboard' ? 'board-note board-note-white' : 'board-note board-note-chalk';
  const box = node('div', undefined, kind(chosen));
  box.setAttribute('role', 'figure');
  box.setAttribute('aria-label', 'Worked on the board');
  const body = renderRichText(boardLines(source), options, depth + 1);
  body.classList?.add('board-writing');
  box.append(body);
  if (closed && !writtenBoards.has(source) && !reducedMotion() && typeof (body as HTMLElement).animate === 'function') {
    writtenBoards.add(source);
    [...body.children].forEach((line, index) => {
      (line as HTMLElement).animate?.([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }],
        { duration: 420, delay: index * 380, easing: 'ease-out', fill: 'backwards' });
    });
  }
  if (language === 'board') {
    const follow = (event: Event) => {
      if (!box.isConnected) { document.removeEventListener('dikdik-board-style', follow); return; }
      box.className = kind((event as CustomEvent<BoardStyle>).detail);
    };
    document.addEventListener?.('dikdik-board-style', follow);
  }
  return box;
}

export interface RichTextOptions {
  codeBlock(text: string, language: string): HTMLElement;
  copyButton(text: () => string, label: string): HTMLButtonElement;
  inlineCode?(text: string): HTMLElement | null;
}
function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
export function responseURL(value: string): string | null {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
const MATH_SPAN = new RegExp(String.raw`(\x60+)[^\n]*?\1|` + INLINE_MATH, "g");
const SLOT = /(\d+)/g;

/** Formulas are lifted out before Markdown parsing (so x_1 or a*b inside them is not read as
 * emphasis), then put back as typeset nodes. Code spans are left alone. */
export function liftMath(text: string): { text: string; formulas: string[] } {
  const formulas: string[] = [];
  if (!text.includes("$") && !text.includes("\\(")) return { text, formulas };
  const lifted = text.replace(MATH_SPAN, (whole: string, ticks: string | undefined, a?: string, b?: string, c?: string, d?: string, e?: string) => {
    if (ticks) return whole;
    const tex = a ?? b ?? c ?? d ?? e ?? "";
    if (!tex.trim()) return whole;
    formulas.push(tex);
    return `${formulas.length - 1}`;
  });
  return { text: lifted, formulas };
}
function restoreMath(nodes: Node[], formulas: string[]): Node[] {
  if (!formulas.length) return nodes;
  const fill = (parent: Node) => {
    for (const child of [...parent.childNodes]) {
      if (child.nodeType === 3) {
        const value = child.textContent ?? "";
        if (!SLOT.test(value)) continue;
        SLOT.lastIndex = 0;
        const parts: Node[] = [];
        let last = 0;
        for (const match of value.matchAll(SLOT)) {
          if (match.index! > last) parts.push(document.createTextNode(value.slice(last, match.index)));
          const tex = formulas[Number(match[1])] ?? "";
          parts.push(mathAvailable() ? renderMath(tex, false) : document.createTextNode(tex));
          last = match.index! + match[0].length;
        }
        if (last < value.length) parts.push(document.createTextNode(value.slice(last)));
        (child as ChildNode).replaceWith(...parts);
      } else fill(child);
    }
  };
  const holder = document.createElement("span");
  holder.append(...nodes);
  fill(holder);
  return [...holder.childNodes];
}

export function renderInline(text: string, options: RichTextOptions, depth = 0): Node[] {
  if (depth === 0) {
    const { text: lifted, formulas } = liftMath(text);
    if (formulas.length) return restoreMath(renderInlineText(lifted, options, 1), formulas);
  }
  return renderInlineText(text, options, depth);
}

function renderInlineText(text: string, options: RichTextOptions, depth = 0): Node[] {
  if (depth > 6) return [document.createTextNode(text)];
  const output: Node[] = [];
  const pattern = /\\[\\`*_{}\[\]()#+.!>~|=-]|(`+)([^\n]*?)\1|!?\[[^\]\n]+\]\([^\s)]+\)|\*\*[^\n]+?\*\*|__[^\n]+?__|~~[^\n]+?~~|==[^\n]+?==|\*[^*\n]+\*|(?<!\w)_[^_\n]+_(?!\w)|\n/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index!;
    if (index > last) output.push(document.createTextNode(text.slice(last, index)));
    const token = match[0];
    if (token === '\n') output.push(node('br'));
    else if (token.startsWith('\\')) output.push(document.createTextNode(token.slice(1)));
    else if (token.startsWith('`')) {
      const value = match[2] ?? '';
      output.push(options.inlineCode?.(value) ?? node('code', value));
    } else if (/^!?\[/.test(token)) {
      const parts = /^(!?)\[([^\]]+)\]\(([^)]+)\)$/.exec(token)!;
      const url = responseURL(parts[3]!);
      if (url) {
        const link = node('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        // Images remain explicit links; a reply never silently contacts an outside image server.
        if (parts[1]) link.append(document.createTextNode('View image: '));
        link.append(...renderInline(parts[2]!, { ...options, inlineCode: value => node('code', value) }, depth + 1));
        output.push(link);
      } else output.push(document.createTextNode(token));
    } else {
      const doubled = /^(\*\*|__|~~|==)/.test(token);
      const element = node(token.startsWith('~~') ? 'del' : token.startsWith('==') ? 'mark' : doubled ? 'strong' : 'em');
      element.append(...renderInline(token.slice(doubled ? 2 : 1, doubled ? -2 : -1), options, depth + 1));
      output.push(element);
    }
    last = index + token.length;
  }
  if (last < text.length) output.push(document.createTextNode(text.slice(last)));
  return output;
}

export function tableTSV(header: readonly string[], rows: readonly (readonly string[])[]): string {
  return [header, ...rows].map(row => row.map(cell => cell.replace(/[\t\r\n]+/g, ' ')).join('\t')).join('\n');
}
const tableCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
export function compareTableCells(a: string, b: string): number {
  const numeric = (text: string) => {
    const value = text.trim().replace(/^(\*\*|__|`)(.*?)\1$/, '$2');
    return /^[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?$/i.test(value) ? Number(value.replace(/,/g, '')) : NaN;
  };
  const left = numeric(a), right = numeric(b);
  return Number.isFinite(left) && Number.isFinite(right) ? left - right : tableCollator.compare(a, b);
}
export function responseTable(table: NonNullable<ReturnType<typeof parseTable>>, options: RichTextOptions, literal = false): HTMLElement {
  const card = node('div', undefined, 'response-table');
  const toolbar = node('div', undefined, 'response-toolbar'); toolbar.setAttribute('data-nocopy', '');
  let rows = table.rows.map((cells, index) => ({ cells, index }));
  const plain = (value: string) => literal ? value : renderInline(value, { codeBlock: options.codeBlock, copyButton: options.copyButton }).map(part => part.textContent ?? '').join('');
  const contents = (value: string) => literal ? [document.createTextNode(value)] : renderInline(value, options);
  const copy = options.copyButton(() => tableTSV(table.header.map(plain), rows.map(row => row.cells.map(plain))), 'Copy table');
  copy.title = 'Copy all rows in the current sort order'; copy.setAttribute('aria-label', 'Copy table to clipboard');
  toolbar.append(node('span', `${rows.length > 500 ? `First 500 of ${rows.length}` : rows.length} rows · ${table.header.length} columns`),
    copy);
  const scroller = node('div', undefined, 'table-scroll'); scroller.tabIndex = 0;
  scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Response table; scroll horizontally if needed');
  const grid = node('table'), head = node('thead'), body = node('tbody'), headRow = node('tr');
  let sorted = -1, direction = 1;
  const headings: HTMLTableCellElement[] = [];
  const draw = () => {
    body.replaceChildren();
    for (const row of rows.slice(0, 500)) {
      const tr = node('tr');
      row.cells.forEach((cell, index) => {
        const td = node('td'); td.append(...contents(cell));
        if (table.align[index]) td.className = `align-${table.align[index]}`;
        tr.append(td);
      });
      body.append(tr);
    }
  };
  table.header.forEach((cell, index) => {
    const th = node('th'); th.scope = 'col'; th.setAttribute('aria-sort', 'none'); headings.push(th);
    const sort = node('button', undefined, 'table-sort'); sort.type = 'button';
    const indicator = node('span', '↕', 'sort-indicator'); indicator.setAttribute('aria-hidden', 'true');
    sort.title = `Sort by ${plain(cell)}`; sort.append(document.createTextNode(plain(cell)), indicator);
    sort.onclick = () => {
      direction = sorted === index ? -direction : 1; sorted = index;
      rows = [...rows].sort((a, b) => direction * compareTableCells(a.cells[index] ?? '', b.cells[index] ?? '') || a.index - b.index);
      headings.forEach((heading, col) => heading.setAttribute('aria-sort', col === index ? direction === 1 ? 'ascending' : 'descending' : 'none'));
      draw();
    };
    th.append(sort); if (table.align[index]) th.className = `align-${table.align[index]}`;
    headRow.append(th);
  });
  head.append(headRow); grid.append(head, body); scroller.append(grid); card.append(toolbar, scroller); draw();
  return card;
}

const listItem = /^( *)([-+*]|\d+[.)])\s+(.*)$/;
/** A shared renderer for replies, streaming text, Markdown files and diff previews. */
const SUB: Record<string, string> = { '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎' };
const SUP: Record<string, string> = { '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ' };
/** Unicode sub/superscript when every character has one; otherwise a typeset formula (\(^{-x}\)). */
const script = (value: string, map: Record<string, string>, mark: '_' | '^') =>
  [...value].every(c => map[c]) ? [...value].map(c => map[c]).join('')
    : /^[\w+\-=().,\s]+$/.test(value) ? `\\(${mark}{\\text{${value}}}\\)` : value;
function tidyPart(part: string, table: boolean): string {
  return part
    .replace(/<br\s*\/?>/gi, table ? '; ' : '  \n')
    .replace(/<\/?(?:b|strong)>/gi, '**')
    .replace(/<\/?(?:i|em)>/gi, '*')
    .replace(/<sub>([^<]{1,20})<\/sub>/gi, (_, value: string) => script(value, SUB, '_'))
    .replace(/<sup>([^<]{1,20})<\/sup>/gi, (_, value: string) => script(value, SUP, '^'))
    .replace(/<kbd>([^<`]{1,40})<\/kbd>/gi, '`$1`')
    .replace(/<\/?(?:u|mark|small|span|font|center|ins)(?:\s[^>]*)?>/gi, '');
}
/** Models often mix a little HTML into Markdown (<b>, <br>, <sub>2</sub> …). Turn the common, harmless
 * tags into their Markdown or Unicode equivalents; HTML is never inserted. Code is left untouched. */
export function tidyHtml(text: string): string {
  if (!text.includes('<')) return text;
  let fence = false;
  return text.split('\n').map(line => {
    if (/^ {0,3}(`{3,}|~{3,})/.test(line)) { fence = !fence; return line; }
    if (fence || !line.includes('<')) return line;
    const table = /^\s*\|/.test(line);
    return line.split(/(`+[^`]*`+)/).map((part, index) => index % 2 ? part : tidyPart(part, table)).join('');
  }).join('\n');
}

/** A <details> block: its <summary> and body, and how many lines it used (to the matching </details>). */
function collectDetails(lines: string[], start: number): { summary: string; body: string; end: number; rest: string; closed: boolean } | null {
  if (!/^\s*<details(?:\s[^>]*)?>/i.test(lines[start]!)) return null;
  let depth = 0;
  const body: string[] = [];
  for (let i = start; i < lines.length; i++) {
    let line = lines[i]!;
    if (i === start) { line = line.replace(/^\s*<details(?:\s[^>]*)?>/i, ''); depth = 1; }
    const tags = /<details(?:\s[^>]*)?>|<\/details\s*>/gi;
    let match: RegExpExecArray | null;
    while ((match = tags.exec(line))) {
      depth += match[0][1] === '/' ? -1 : 1;
      if (depth === 0) {
        body.push(line.slice(0, match.index));
        return { ...splitSummary(body.join('\n')), end: i, rest: line.slice(match.index + match[0].length).trim(), closed: true };
      }
    }
    body.push(line);
  }
  return { ...splitSummary(body.join('\n')), end: lines.length - 1, rest: '', closed: false };  // still streaming in
}
function splitSummary(text: string): { summary: string; body: string } {
  const found = /^\s*<summary(?:\s[^>]*)?>([\s\S]*?)<\/summary\s*>/i.exec(text);
  if (!found) return { summary: 'Details', body: text };
  return { summary: found[1]!.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim() || 'Details', body: text.slice(found[0].length) };
}

export function renderRichText(text: string, options: RichTextOptions, depth = 0): HTMLElement {
  const root = node('div', undefined, 'prose');
  const shortened = text.length > 120000;
  if (shortened) text = text.slice(0, 120000);
  if (depth > 12) { root.append(node('p', text)); return root; }
  if (depth === 0) text = tidyHtml(text);
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  let paragraph: string[] = [];
  const flush = () => {
    if (!paragraph.length) return;
    const value = paragraph.map((line, index) => line.trimEnd() + (index < paragraph.length - 1 ? / {2}$/.test(line) ? '\n' : ' ' : '')).join('');
    const p = node('p'); p.append(...renderInline(value, options)); root.append(p); paragraph = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (!line.trim()) { flush(); continue; }
    const fence = /^ {0,3}(`{3,}|~{3,})\s*([\w.+-]*)[^\n]*$/.exec(line);
    if (fence) {
      flush(); const body: string[] = [];
      const closing = new RegExp(`^ {0,3}${fence[1]![0]}{${fence[1]!.length},}\\s*$`);
      for (i++; i < lines.length && !closing.test(lines[i]!); i++) body.push(lines[i]!);
      const source = body.join('\n'), language = (fence[2] ?? '').toLowerCase();
      const closed = i < lines.length;  // an unclosed fence is still streaming in
      const fallback = (text: string, lang: string) => options.codeBlock(text, lang);
      root.append(language === 'chart' ? chartCard(source, options)
        : language === 'mermaid' ? closed ? renderMermaid(source, fallback) : options.codeBlock(source, 'mermaid')
        : language === 'plot' || language === 'function-plot' ? closed ? renderPlot(source, fallback) : options.codeBlock(source, 'plot')
        : language === 'sketch' || language === 'drawing' ? closed ? renderSketch(source, fallback) : options.codeBlock(source, 'sketch')
        : language === 'board' || language === 'chalkboard' || language === 'whiteboard' ? boardNote(source, options, depth, language, closed)
        : (language === 'math' || language === 'katex') && mathAvailable() ? renderMath(source, true)
        : options.codeBlock(source, language));
      continue;
    }
    // <details><summary>Check your answer</summary> … </details>: a native, script-free reveal.
    const details = collectDetails(lines, i);
    if (details) {
      flush();
      const box = node('details', undefined, 'md-details');
      const summary = node('summary');
      summary.append(...renderInline(details.summary, options));
      box.append(summary, renderRichText(details.body.trim(), options, depth + 1));
      root.append(box);
      if (details.rest) lines.splice(details.end + 1, 0, details.rest);
      i = details.end; continue;
    }
    // Display formulas: $$ … $$, \[ … \] and \begin{align} … \end{align} on their own lines.
    const display = mathAvailable() ? collectDisplay(lines, i) : null;
    if (display) {
      flush(); root.append(renderMath(display.tex, true));
      if (display.rest) { const after = node('p'); after.append(...renderInline(display.rest, options)); root.append(after); }
      i = display.end; continue;
    }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); root.append(node('hr')); continue; }
    const table = parseTable(lines, i);
    if (table) { flush(); root.append(responseTable(table, options)); i = table.end - 1; continue; }
    const alert = alertKind(line);
    if (/^\s*>/.test(line)) {
      flush(); const body: string[] = [];
      for (i += alert ? 1 : 0; i < lines.length && /^\s*>/.test(lines[i]!); i++) body.push(lines[i]!.replace(/^\s*>\s?/, ''));
      i--;
      if (alert) {
        const box = node('aside', undefined, `md-alert md-alert-${alert}`);
        box.append(node('p', alert[0]!.toUpperCase() + alert.slice(1), 'md-alert-title'), renderRichText(body.join('\n'), options, depth + 1)); root.append(box);
      } else {
        const quote = node('blockquote'); quote.append(renderRichText(body.join('\n'), options, depth + 1)); root.append(quote);
      }
      continue;
    }
    const heading = /^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/.exec(line);
    if (heading) {
      flush(); const h = node(`h${Math.min(6, heading[1]!.length + 1)}` as 'h2');
      h.append(...renderInline(heading[2]!, options)); root.append(h); continue;
    }
    const first = listItem.exec(line);
    if (first) {
      flush(); const indent = first[1]!.length, ordered = /^\d/.test(first[2]!);
      const list = node(ordered ? 'ol' : 'ul');
      if (ordered) (list as HTMLOListElement).start = parseInt(first[2]!, 10);
      let cursor = i;
      while (cursor < lines.length) {
        const item = listItem.exec(lines[cursor]!);
        if (!item || item[1]!.length !== indent || /^\d/.test(item[2]!) !== ordered) break;
        const content = [item[3]!]; cursor++;
        while (cursor < lines.length) {
          const next = lines[cursor]!, marker = listItem.exec(next);
          if (marker && marker[1]!.length <= indent) break;
          if (!next.trim()) {
            const upcoming = lines.slice(cursor + 1).find(value => value.trim());
            if (!upcoming || (upcoming.match(/^ */)?.[0].length ?? 0) <= indent) break;
          } else if ((next.match(/^ */)?.[0].length ?? 0) <= indent) break;
          content.push(next.slice(Math.min(indent + 2, next.match(/^ */)![0].length))); cursor++;
        }
        const li = node('li');
        const task = /^\[([ xX])\]\s+(.*)$/.exec(content[0]!);
        if (task) {
          list.className = 'task-list'; li.className = 'task-item';
          const check = node('input'); check.type = 'checkbox'; check.checked = task[1]!.toLowerCase() === 'x'; check.disabled = true;
          check.setAttribute('aria-label', task[2]!); li.append(check); content[0] = task[2]!;
        }
        const body = renderRichText(content.join('\n'), options, depth + 1);
        li.append(body); list.append(li);
        if (!lines[cursor]?.trim()) {
          let next = cursor;
          while (next < lines.length && !lines[next]!.trim()) next++;
          const following = listItem.exec(lines[next] ?? '');
          if (following && following[1]!.length === indent && /^\d/.test(following[2]!) === ordered) cursor = next;
          else break;
        }
      }
      root.append(list); i = cursor - 1; continue;
    }
    paragraph.push(line);
  }
  flush();
  if (shortened) root.append(node('p', 'Display preview shortened to keep the workspace responsive. The original message or file is unchanged.', 'render-notice'));
  return root;
}
