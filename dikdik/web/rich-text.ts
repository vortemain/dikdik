/** DOM-only response rendering. Model text is never inserted as HTML. */
import { alertKind, parseTable } from './organise';
import { chartCard } from './response-chart';

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
export function renderInline(text: string, options: RichTextOptions, depth = 0): Node[] {
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
  const toolbar = node('div', undefined, 'response-toolbar');
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
export function renderRichText(text: string, options: RichTextOptions, depth = 0): HTMLElement {
  const root = node('div', undefined, 'prose');
  const shortened = text.length > 120000;
  if (shortened) text = text.slice(0, 120000);
  if (depth > 12) { root.append(node('p', text)); return root; }
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
      root.append(language === 'chart' ? chartCard(source, options) : options.codeBlock(source, language));
      continue;
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
