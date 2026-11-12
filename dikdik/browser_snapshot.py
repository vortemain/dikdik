"""The page as an accessibility tree with stable element refs, for agents to read and act on.

`SNAPSHOT` runs inside the page. It lists roles, accessible names, values and states
(checked, disabled, expanded, invalid, focused), the way a screen reader would describe
the page, and gives every control a ref such as ``e12``. A ref stays on its element for the
page's lifetime, so a ref read in one snapshot still names the same element after a re-render
that keeps the node. Acting by ref needs no CSS selector and survives layout changes.
"""

REF_ATTRIBUTE = "data-dikdik-ref"

SNAPSHOT = r"""(opts) => {
  const MAX_LINES = opts.maxLines || 900, MAX_CHARS = opts.maxChars || 24000;
  const ATTR = 'data-dikdik-ref';
  if (!window.__dikdikRefs) Object.defineProperty(window, '__dikdikRefs', {value: {n: 0}, enumerable: false});
  const counter = window.__dikdikRefs;
  const lines = []; let chars = 0, truncated = false;
  const INTERACTIVE = new Set(['button','link','textbox','searchbox','checkbox','radio','combobox','listbox','option',
    'menuitem','menuitemcheckbox','menuitemradio','tab','switch','slider','spinbutton','treeitem']);
  const NAMED_BY_CONTENT = new Set(['button','link','heading','option','tab','menuitem','menuitemcheckbox','menuitemradio',
    'treeitem','cell','columnheader','rowheader','listitem','paragraph','checkbox','radio','switch','tooltip','status','alert']);
  const CONTROL_SELECTOR = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],' +
    '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=switch],[role=option],[role=combobox],[role=textbox],' +
    '[role=slider],[contenteditable=""],[contenteditable=true],[tabindex]:not([tabindex="-1"])';
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','HEAD','META','LINK','BASE','TITLE']);
  const clean = (s, n) => { s = (s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const textOf = el => clean(el.innerText !== undefined ? el.innerText : el.textContent, 400);
  const byIds = ids => (ids || '').split(/\s+/).filter(Boolean).map(id => document.getElementById(id)).filter(Boolean).map(textOf).join(' ');
  const implicitRole = el => {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (/^h[1-6]$/.test(tag)) return 'heading';
    switch (tag) {
      case 'a': case 'area': return el.hasAttribute('href') ? 'link' : '';
      case 'button': case 'summary': return 'button';
      case 'input':
        if (['button','submit','reset','image','file'].includes(type)) return 'button';
        return {checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton', search: 'searchbox', hidden: ''}[type] ?? 'textbox';
      case 'textarea': return 'textbox';
      case 'select': return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
      case 'option': return 'option';
      case 'img': return el.getAttribute('alt') === '' ? '' : 'img';
      case 'svg': return el.getAttribute('aria-label') || el.querySelector('title') ? 'img' : '';
      case 'nav': return 'navigation';
      case 'main': return 'main';
      case 'header': return el.closest('article,aside,main,nav,section') ? '' : 'banner';
      case 'footer': return el.closest('article,aside,main,nav,section') ? '' : 'contentinfo';
      case 'aside': return 'complementary';
      case 'form': return 'form';
      case 'dialog': return 'dialog';
      case 'ul': case 'ol': return 'list';
      case 'li': return 'listitem';
      case 'table': return 'table';
      case 'tr': return 'row';
      case 'td': return 'cell';
      case 'th': return 'columnheader';
      case 'p': return 'paragraph';
      case 'details': return 'group';
      case 'fieldset': return 'group';
      case 'progress': return 'progressbar';
      case 'iframe': return 'iframe';
      case 'video': return 'video';
      case 'section': return el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') ? 'region' : '';
    }
    if (el.isContentEditable && el.hasAttribute('contenteditable')) return 'textbox';
    return '';
  };
  const roleOf = el => {
    const explicit = (el.getAttribute('role') || '').trim().split(/\s+/)[0];
    if (explicit === 'presentation' || explicit === 'none') return '';
    return explicit || implicitRole(el);
  };
  const nameOf = (el, role) => {
    const aria = clean(el.getAttribute('aria-label'), 160); if (aria) return aria;
    const labelled = clean(byIds(el.getAttribute('aria-labelledby')), 160); if (labelled) return labelled;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      if (['button','submit','reset'].includes(el.type)) return clean(el.value, 160) || (el.type === 'submit' ? 'Submit' : el.type === 'reset' ? 'Reset' : '');
      if (el.type === 'image') return clean(el.alt, 160);
      if (el.labels && el.labels.length) { const t = clean(Array.from(el.labels).map(textOf).join(' '), 160); if (t) return t; }
      return clean(el.getAttribute('placeholder') || el.getAttribute('title'), 160);
    }
    if (tag === 'img' || tag === 'area') return clean(el.getAttribute('alt') || el.getAttribute('title'), 160);
    if (tag === 'svg') { const t = el.querySelector('title'); return clean(t ? t.textContent : '', 160); }
    if (tag === 'fieldset') { const legend = el.querySelector(':scope > legend'); if (legend) return textOf(legend); }
    if (tag === 'table') { const caption = el.querySelector(':scope > caption'); if (caption) return textOf(caption); }
    if (NAMED_BY_CONTENT.has(role)) return clean(textOf(el), role === 'paragraph' || role === 'listitem' || role === 'cell' ? 300 : 160);
    return clean(el.getAttribute('title'), 160);
  };
  const hidden = el => {
    if (el.getAttribute('aria-hidden') === 'true' || el.hidden) return true;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') return true;
    return s.display !== 'contents' && el.getClientRects().length === 0 && el.tagName !== 'OPTION';
  };
  const userInvalid = el => { try { return el.matches(':user-invalid'); } catch (e) { return false; } };
  const clickable = el => {
    if (el.matches(CONTROL_SELECTOR)) return true;
    if (el.hasAttribute('onclick')) return true;
    const cursor = getComputedStyle(el).cursor;
    return cursor === 'pointer' && !(el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer');
  };
  const refOf = el => {
    let ref = el.getAttribute(ATTR);
    if (!ref) { ref = 'e' + (++counter.n); el.setAttribute(ATTR, ref); }
    return ref;
  };
  const quote = s => JSON.stringify(s);
  const describe = (el, role) => {
    const parts = [];
    const name = nameOf(el, role);
    parts.push(role + (name ? ' ' + quote(name) : ''));
    const tag = el.tagName.toLowerCase();
    if (role === 'heading') parts.push('[level=' + (Number(el.getAttribute('aria-level')) || Number(tag.slice(1)) || 2) + ']');
    if (role === 'checkbox' || role === 'radio' || role === 'switch' || role === 'menuitemcheckbox' || role === 'menuitemradio') {
      const aria = el.getAttribute('aria-checked');
      if (aria === 'mixed' || el.indeterminate) parts.push('[checked=mixed]');
      else if (el.checked === true || aria === 'true') parts.push('[checked]');
    }
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') parts.push('[disabled]');
    const expanded = el.getAttribute('aria-expanded') ?? (tag === 'summary' && el.parentElement ? String(el.parentElement.open) : null);
    if (expanded === 'true') parts.push('[expanded]'); else if (expanded === 'false') parts.push('[collapsed]');
    if (el.getAttribute('aria-pressed') === 'true') parts.push('[pressed]');
    if (el.getAttribute('aria-selected') === 'true' || (tag === 'option' && el.selected)) parts.push('[selected]');
    if (el.required || el.getAttribute('aria-required') === 'true') parts.push('[required]');
    if (el.getAttribute('aria-invalid') === 'true' || userInvalid(el)) parts.push('[invalid]');
    if (document.activeElement === el) parts.push('[focused]');
    if (INTERACTIVE.has(role) || role === 'iframe' || clickable(el)) parts.push('[ref=' + refOf(el) + ']');
    if (['textbox','searchbox','spinbutton','slider'].includes(role) && 'value' in el && el.value !== undefined) {
      const value = el.type === 'password' ? (el.value ? '••••' : '') : clean(String(el.value), 120);
      if (value) parts.push('value=' + quote(value));
    } else if (role === 'textbox' && el.isContentEditable) {
      const value = clean(el.innerText, 120); if (value) parts.push('value=' + quote(value));
    }
    if (tag === 'select') {
      const chosen = Array.from(el.selectedOptions || []).map(o => clean(o.textContent, 60));
      if (chosen.length) parts.push('value=' + quote(chosen.join(', ')));
      const options = Array.from(el.options || []).slice(0, 12).map(o => clean(o.textContent, 40));
      if (options.length) parts.push('options=' + JSON.stringify(options) + (el.options.length > 12 ? '+' + (el.options.length - 12) : ''));
    }
    if (tag === 'a' && el.getAttribute('href') && !el.getAttribute('href').startsWith('javascript:')) parts.push('url=' + quote(clean(el.getAttribute('href'), 160)));
    const message = (userInvalid(el) || el.getAttribute('aria-invalid') === 'true') && el.validationMessage ? el.validationMessage : '';
    const described = clean(byIds(el.getAttribute('aria-describedby')) || message, 160);
    if (described) parts.push('description=' + quote(described));
    return parts.join(' ');
  };
  const emit = (depth, text) => {
    if (truncated) return false;
    const line = '  '.repeat(Math.min(depth, 20)) + '- ' + text;
    if (lines.length >= MAX_LINES || chars + line.length > MAX_CHARS) { truncated = true; return false; }
    lines.push(line); chars += line.length + 1; return true;
  };
  const childrenOf = node => {
    const host = node.shadowRoot || node;
    const out = [];
    for (const child of host.childNodes) {
      if (child.nodeType === 1 && child.tagName === 'SLOT') {
        const assigned = child.assignedNodes({flatten: true});
        out.push(...(assigned.length ? assigned : child.childNodes));
      } else out.push(child);
    }
    return out;
  };
  const hasControls = el => !!(el.querySelector(CONTROL_SELECTOR) || (el.shadowRoot && el.shadowRoot.querySelector(CONTROL_SELECTOR)));
  const walk = (node, depth) => {
    for (const child of childrenOf(node)) {
      if (truncated) return;
      if (child.nodeType === 3) {
        const text = clean(child.textContent, 300);
        if (text && child.parentElement && !hidden(child.parentElement)) emit(depth, 'text: ' + quote(text));
        continue;
      }
      if (child.nodeType !== 1 || SKIP.has(child.tagName) || hidden(child)) continue;
      const role = roleOf(child), tag = child.tagName;
      if (tag === 'svg' && !role) continue;
      if (role) {
        emit(depth, describe(child, role));
        if (role === 'combobox' && tag === 'SELECT') continue;  // its options are listed on the line
        if (role === 'iframe' || role === 'img' || role === 'video' || tag === 'TEXTAREA' || tag === 'INPUT') continue;
        if (NAMED_BY_CONTENT.has(role) && !hasControls(child)) continue;  // the name already holds the text
        walk(child, depth + 1);
      } else if (clickable(child)) {
        const text = clean(textOf(child), 160);
        emit(depth, 'clickable' + (text ? ' ' + quote(text) : '') + ' [ref=' + refOf(child) + ']');
        if (hasControls(child)) walk(child, depth + 1);
      } else if (!hasControls(child) && !child.shadowRoot && !child.querySelector('img,svg,[role],h1,h2,h3,h4,h5,h6,p,li,table')) {
        const text = clean(textOf(child), 300);
        if (text) emit(depth, 'text: ' + quote(text));
      } else walk(child, depth);
    }
  };
  if (document.body) walk(document.body, 0);
  const dialogs = Array.from(document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]')).filter(d => !hidden(d)).length;
  return {snapshot: lines.join('\n'), truncated, lines: lines.length, refs: counter.n, open_dialogs: dialogs,
          scroll: {x: Math.round(scrollX), y: Math.round(scrollY), height: document.documentElement.scrollHeight}};
}"""
