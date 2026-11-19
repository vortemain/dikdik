import { expect, test } from 'bun:test';
import { compareTableCells, renderRichText, responseURL, tableTSV, tidyHtml, type RichTextOptions } from '../../dikdik/web/rich-text';
import { parseChart } from '../../dikdik/web/response-chart';
import { parseTable } from '../../dikdik/web/organise';

// DOM-only renderer tests. No HTML parser or innerHTML exists in this double.
class Element {
  children: Element[] = []; attributes = new Map<string, string>(); className = ''; text = '';
  type = ''; checked = false; disabled = false; start = 1; onclick?: () => void;
  listeners = new Map<string, () => void>();
  constructor(readonly tagName: string) {}
  set textContent(value: string) { this.text = value; this.children = []; }
  get textContent(): string { return this.text + this.children.map(child => child.textContent).join(''); }
  append(...nodes: Element[]) { this.children.push(...nodes); }
  replaceChildren(...nodes: Element[]) { this.text = ''; this.children = nodes; }
  setAttribute(key: string, value: string) { this.attributes.set(key, value); }
  addEventListener(key: string, listener: () => void) { this.listeners.set(key, listener); }
  getAttribute(key: string) { return this.attributes.get(key) ?? null; }
  focus() { this.listeners.get('focus')?.(); }
}
function find(root: Element, tag: string): Element[] {
  return [...(root.tagName === tag ? [root] : []), ...root.children.flatMap(child => find(child, tag))];
}
function withDOM(run: (render: (text: string) => Element, copied: string[]) => void) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'document'), copied: string[] = [];
  Object.defineProperty(globalThis, 'document', { configurable: true, value: {
    createElement: (tag: string) => new Element(tag), createTextNode: (text: string) => { const node = new Element('#text'); node.text = text; return node; },
    createElementNS: (_ns: string, tag: string) => new Element(tag),
  } });
  const options = {
    codeBlock(text: string, language: string) { const pre = new Element('pre'); pre.text = text; pre.className = language; return pre; },
    copyButton(text: () => string) { const button = new Element('button'); button.onclick = () => copied.push(text()); return button; },
  } as unknown as RichTextOptions;
  try { run(text => renderRichText(text, options) as unknown as Element, copied); }
  finally { if (previous) Object.defineProperty(globalThis, 'document', previous); else Reflect.deleteProperty(globalThis, 'document'); }
}

test('paragraph spacing ignores repeated blank lines and soft wraps; explicit hard breaks remain', () => withDOM(render => {
  const root = render('\n\nFirst line\nsoft wrap\n\n\n\nSecond line  \nhard break\n\n');
  expect(root.children.map(child => child.tagName)).toEqual(['p', 'p']);
  expect(root.children[0]!.textContent).toBe('First line soft wrap');
  expect(find(root, 'br')).toHaveLength(1);
  expect(find(root, 'p').every(item => item.textContent.length)).toBe(true);
}));

test('headings retain hierarchy; nested and ordered lists preserve structure and starting number', () => withDOM(render => {
  const root = render('# Heading\n## Subheading\n- Parent\n  - Child\n    3. Grandchild\n- Next\n\n5. Fifth\n6. Sixth');
  expect(root.children[0]!.tagName).toBe('h2'); expect(root.children[1]!.tagName).toBe('h3');
  expect(find(root, 'ul')).toHaveLength(2); expect(find(root, 'ol')).toHaveLength(2);
  expect(find(root, 'ol').map(list => list.start)).toEqual([3, 5]);
  expect(find(root, 'li')).toHaveLength(6);
}));

test('task lists, emphasis, highlights, quotes and alerts have semantic elements', () => withDOM(render => {
  const root = render('- [x] **Ready**\n- [ ] *Next* and ~~old~~ and ==important==\n\n> [!WARNING]\n> Keep **this** safe\n\n> A quote\n\n---');
  expect(find(root, 'input').map(item => [item.checked, item.disabled])).toEqual([[true, true], [false, true]]);
  expect(find(root, 'strong')).toHaveLength(2); expect(find(root, 'em')).toHaveLength(1);
  expect(find(root, 'mark')).toHaveLength(1); expect(find(root, 'del')).toHaveLength(1);
  expect(find(root, 'blockquote')).toHaveLength(1); expect(find(root, 'aside')[0]!.className).toContain('warning');
  expect(find(root, 'hr')).toHaveLength(1);
}));

test('fenced code preserves all blank lines and indentation, including incomplete streamed fences', () => withDOM(render => {
  const root = render('Before\n\n~~~python\n  one\n\n\n    two\n~~~\n\n```js\nunfinished();');
  expect(find(root, 'pre').map(item => item.textContent)).toEqual(['  one\n\n\n    two', 'unfinished();']);
}));

test('HTML and dangerous URLs never become executable markup or automatic images', () => withDOM(render => {
  const root = render('<img src=x onerror=alert(1)>\n\n[bad](javascript:alert) ![picture](https://example.com/photo.png) [safe](https://example.com/)');
  expect(find(root, 'img')).toHaveLength(0); expect(find(root, 'script')).toHaveLength(0);
  expect(find(root, 'a')).toHaveLength(2); expect(root.textContent).toContain('javascript:alert');
  expect(responseURL('https://user:secret@example.com/')).toBeNull(); expect(responseURL('data:text/html,x')).toBeNull();
}));

test('tables sort naturally with accessible sort state and copy the displayed order', () => withDOM((render, copied) => {
  const root = render('| Name | Value |\n| :--- | ---: |\n| **B** | 10 |\n| A | 2 |');
  const headings = find(root, 'th'), buttons = headings.map(th => find(th, 'button')[0]!);
  buttons[1]!.onclick!(); expect(headings[1]!.attributes.get('aria-sort')).toBe('ascending');
  expect(find(root, 'tbody')[0]!.children[0]!.textContent).toBe('A2');
  find(root, 'button')[0]!.onclick!(); expect(copied[0]).toBe('Name\tValue\nA\t2\nB\t10');
  buttons[1]!.onclick!(); expect(headings[1]!.attributes.get('aria-sort')).toBe('descending');
  expect(tableTSV(['a'], [['one\ntwo']])).toBe('a\none two');
}));

test('table code spans can contain pipes without creating extra columns', () => {
  expect(parseTable(['| Code | Value |', '| --- | --- |', '| `a|b` | yes |'], 0)?.rows).toEqual([['`a|b`', 'yes']]);
});

test('table numeric sorting handles negative numbers, decimals and thousands', () => {
  expect(['10', '-5', '-10', '2.5', '1,000'].sort(compareTableCells)).toEqual(['-10', '-5', '2.5', '10', '1,000']);
  expect(compareTableCells('**-10**', '**2**')).toBeLessThan(0);
});

test('chart cards use supplied numbers, keyboard-focusable points and a data table', () => withDOM(render => {
  const source = JSON.stringify({ type: 'bar', title: '<script>Not HTML</script>', labels: ['A', 'B'], values: [-5, 10] });
  const root = render('```chart\n' + source + '\n```');
  expect(find(root, 'figure')).toHaveLength(1); expect(find(root, 'rect')).toHaveLength(2);
  expect(find(root, 'rect')[0]!.attributes.get('tabindex')).toBe('0');
  expect(find(root, 'rect')[0]!.attributes.get('aria-label')).toContain('-5');
  const keyboard = find(root, 'rect')[0]!.listeners.get('keydown') as unknown as (event: KeyboardEvent) => void;
  keyboard({ key: 'ArrowRight', preventDefault() {} } as KeyboardEvent);
  expect(find(root, 'rect')[1]!.attributes.get('tabindex')).toBe('0');
  expect(find(root, 'p').some(item => item.textContent === 'B · Value: 10')).toBe(true);
  expect(find(root, 'table')).toHaveLength(1); expect(find(root, 'script')).toHaveLength(0);
  expect(render('```chart\n{"type":"bar"}\n```').textContent).toContain('Chart not rendered');
  expect(parseChart('{"type":"pie","labels":["A"],"values":[2]}')).toBeNull();
  expect(parseChart('{"type":"bar","labels":["A"],"values":["2"]}')).toBeNull();
  expect(parseChart('{"type":"bar","labels":["A"],"values":[1e999]}')).toBeNull();
}));

test('large display previews are explicitly bounded without altering their source', () => withDOM(render => {
  const text = 'long '.repeat(25000);
  expect(render(text).textContent).toContain('Display preview shortened'); expect(text.length).toBe(125000);
}));

test('<details> reveals become a native collapsible card; the summary and body render as Markdown', () => withDOM(render => {
  const reply = "Try it first.\n\n<details> <summary><b>Click here to check your answer when you're ready!</b></summary>\n\n1. Brackets first: (8-5) = 3\n2. Addition last: 15 + 3 = 18\n\nDid you get **18**? Tell me which step felt tricky! </details>\n\nNext question.";
  const root = render(reply);
  const details = find(root, 'details');
  expect(details).toHaveLength(1);
  const summary = find(details[0]!, 'summary')[0]!;
  expect(summary.textContent).toBe("Click here to check your answer when you're ready!");
  expect(find(summary, 'strong')).toHaveLength(1);
  expect(find(details[0]!, 'li')).toHaveLength(2);
  expect(details[0]!.textContent).toContain('Tell me which step felt tricky!');
  expect(root.textContent).not.toContain('<');           // no raw tags anywhere
  expect(root.children.at(-1)!.textContent).toBe('Next question.');
}));

test('a reply still streaming in shows an unfinished <details> without its raw tags', () => withDOM(render => {
  const root = render('<details><summary>Answer</summary>\n\nStep 1: so far');
  expect(find(root, 'details')).toHaveLength(1);
  expect(root.textContent).not.toContain('<');
}));

test('common inline HTML from models becomes Markdown or Unicode; code is untouched', () => {
  expect(tidyHtml('A <b>bold</b> and <i>slanted</i> word')).toBe('A **bold** and *slanted* word');
  expect(tidyHtml('H<sub>2</sub>O and x<sup>2</sup> and e<sup>-x</sup>')).toBe('H₂O and x² and e\\(^{\\text{-x}}\\)');
  expect(tidyHtml('v<sub>max</sub>')).toBe('v\\(_{\\text{max}}\\)');
  expect(tidyHtml('line one<br>line two')).toBe('line one  \nline two');
  expect(tidyHtml('| a<br>b | c |')).toBe('| a; b | c |');
  expect(tidyHtml('Use `<b>` tags')).toBe('Use `<b>` tags');
  expect(tidyHtml('```html\n<b>x</b>\n```')).toBe('```html\n<b>x</b>\n```');
  expect(tidyHtml('if a < b and c > d')).toBe('if a < b and c > d');
});
