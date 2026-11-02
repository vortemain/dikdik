"""Deterministic accessibility checks (WCAG 2.2 AA subset) for rendered pages and static HTML.

`AUDIT` runs inside a real browser page (the preview and browse sessions) and reads the DOM,
computed styles and layout: names, labels, language, title, duplicate ids, heading order,
text contrast, target size and visible keyboard focus. `check_html` applies the structural
subset to HTML files without a browser.

Impact levels: "serious" rules are high-confidence failures (a control with no accessible name
is unusable with a screen reader) and fail a preview; "moderate" rules are measured but can
have legitimate exceptions (decorative low-contrast text, inline links exempt from target size),
so they are reported for review. Automated checks cover only part of WCAG; passing them is not
a claim of accessibility.
"""

from __future__ import annotations

from html.parser import HTMLParser

SERIOUS = {"image-alt", "control-label", "button-name", "link-name", "html-lang", "document-title"}

AUDIT = r"""() => {
  const LIMIT = 2000, EXAMPLES = 5;
  const found = {};
  const add = (rule, wcag, impact, el, detail) => {
    const entry = found[rule] || (found[rule] = {rule, wcag, impact, count: 0, examples: []});
    entry.count++;
    if (entry.examples.length < EXAMPLES) entry.examples.push({selector: path(el), detail: detail || ''});
  };
  const path = (el) => {
    if (!el || el.nodeType !== 1) return 'document';
    const parts = []; let n = el;
    while (n && n.nodeType === 1 && parts.length < 5) {
      if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
      let i = 1, p = n.previousElementSibling;
      while (p) { if (p.tagName === n.tagName) i++; p = p.previousElementSibling; }
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')'); n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    return el.getClientRects().length > 0 && s.visibility !== 'hidden' && s.display !== 'none' && el.closest('[aria-hidden="true"]') === null;
  };
  const text = (el) => (el.innerText || el.textContent || '').trim();
  const labelledBy = (el) => (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
    .map(id => document.getElementById(id)).filter(Boolean).map(text).join(' ').trim();
  const name = (el) => (el.getAttribute('aria-label') || '').trim() || labelledBy(el) || (el.getAttribute('title') || '').trim();
  const imgNames = (el) => Array.from(el.querySelectorAll('img[alt],svg[aria-label],[role=img][aria-label]'))
    .map(i => (i.getAttribute('alt') || i.getAttribute('aria-label') || '').trim()).join(' ').trim();

  const lang = (document.documentElement.getAttribute('lang') || '').trim();
  if (!lang) add('html-lang', '3.1.1', 'serious', document.documentElement, 'The <html> element has no lang attribute');
  if (!document.title.trim()) add('document-title', '2.4.2', 'serious', document.documentElement, 'The page has no title');

  const all = Array.from(document.querySelectorAll('body *')).slice(0, LIMIT);
  for (const img of all.filter(e => e.tagName === 'IMG')) {
    if (!img.hasAttribute('alt') && img.getAttribute('role') !== 'presentation' && !name(img) && visible(img))
      add('image-alt', '1.1.1', 'serious', img, img.getAttribute('src') || '');
  }
  const controls = all.filter(e => e.matches('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]),select,textarea'));
  for (const c of controls) {
    if (!visible(c)) continue;
    const label = (c.labels && Array.from(c.labels).map(text).join(' ').trim()) || name(c);
    if (!label) add('control-label', '1.3.1 / 4.1.2', 'serious', c, (c.getAttribute('placeholder') ? 'placeholder is not a label' : ''));
  }
  for (const b of all.filter(e => e.matches('button,[role=button],input[type=submit],input[type=button],input[type=reset]'))) {
    if (!visible(b)) continue;
    if (!(text(b) || name(b) || imgNames(b) || (b.value || '').trim())) add('button-name', '4.1.2', 'serious', b);
  }
  for (const a of all.filter(e => e.matches('a[href]'))) {
    if (!visible(a)) continue;
    if (!(text(a) || name(a) || imgNames(a))) add('link-name', '2.4.4 / 4.1.2', 'serious', a, a.getAttribute('href') || '');
  }
  const ids = {};
  for (const e of document.querySelectorAll('[id]')) (ids[e.id] = ids[e.id] || []).push(e);
  for (const [id, list] of Object.entries(ids)) if (list.length > 1) add('duplicate-id', '1.3.1', 'moderate', list[1], '#' + id + ' appears ' + list.length + ' times');

  const headings = all.filter(e => /^H[1-6]$/.test(e.tagName) && visible(e));
  let previous = 0;
  for (const h of headings) {
    const level = Number(h.tagName[1]);
    if (previous && level > previous + 1) add('heading-order', '1.3.1', 'moderate', h, 'h' + previous + ' is followed by h' + level);
    previous = level;
  }
  if (headings.length && !headings.some(h => h.tagName === 'H1')) add('heading-order', '1.3.1', 'moderate', headings[0], 'The page has headings but no h1');

  const rgb = (value) => { const m = value.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const luminance = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const background = (el) => { let n = el;
    while (n && n.nodeType === 1) { const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;  // unknown under an image or gradient
      const c = rgb(s.backgroundColor); if (c && c.a >= 0.99) return c; if (c && c.a > 0.01) return null;
      n = n.parentElement; }
    return {r: 255, g: 255, b: 255, a: 1}; };
  for (const el of all) {
    if (!visible(el) || el.closest('[aria-disabled=true],:disabled')) continue;
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const s = getComputedStyle(el), fg = rgb(s.color), bg = background(el);
    if (!fg || !bg || fg.a < 0.99) continue;
    const ratio = (Math.max(luminance(fg), luminance(bg)) + 0.05) / (Math.min(luminance(fg), luminance(bg)) + 0.05);
    const size = parseFloat(s.fontSize), bold = Number(s.fontWeight) >= 700;
    const needed = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (ratio + 1e-6 < needed) add('color-contrast', '1.4.3', 'moderate', el, ratio.toFixed(2) + ':1, needs ' + needed + ':1');
  }
  const interactive = all.filter(e => e.matches('a[href],button,input:not([type=hidden]),select,textarea,[role=button],[tabindex]:not([tabindex="-1"])') && visible(e));
  for (const el of interactive) {
    const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' && getComputedStyle(el).display === 'inline';  // in-text links are exempt
    if (!inline && (r.width < 24 || r.height < 24)) add('target-size', '2.5.8', 'moderate', el, Math.round(r.width) + 'x' + Math.round(r.height) + ' px');
    const tabindex = Number(el.getAttribute('tabindex'));
    if (tabindex > 0) add('tabindex-positive', '2.4.3', 'moderate', el, 'tabindex=' + tabindex);
  }
  const active = document.activeElement;
  for (const el of interactive.filter(e => e.tabIndex >= 0).slice(0, 25)) {
    const before = getComputedStyle(el); const snapshot = [before.outlineStyle, before.outlineWidth, before.boxShadow, before.borderColor, before.backgroundColor].join('|');
    el.focus({preventScroll: true});
    if (document.activeElement !== el) continue;
    const after = getComputedStyle(el);
    const outline = after.outlineStyle !== 'none' && parseFloat(after.outlineWidth) > 0;
    const changed = [after.outlineStyle, after.outlineWidth, after.boxShadow, after.borderColor, after.backgroundColor].join('|') !== snapshot;
    if (!outline && !changed) add('focus-visible', '2.4.7', 'moderate', el, 'No visible change when focused');
  }
  if (active && active.focus) active.focus({preventScroll: true}); else if (document.activeElement) document.activeElement.blur();
  return {checked_elements: all.length, truncated: document.querySelectorAll('body *').length > LIMIT, violations: Object.values(found)};
}"""


def summarize(audit) -> dict:
    """Counts by impact plus the serious violations that should fail a preview."""
    violations = audit.get("violations", []) if isinstance(audit, dict) else []
    serious = [v for v in violations if v.get("impact") == "serious" and v.get("rule") in SERIOUS]
    return {"serious": sum(v.get("count", 0) for v in serious),
            "moderate": sum(v.get("count", 0) for v in violations if v not in serious),
            "failing_rules": sorted({v["rule"] for v in serious})}


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.violations = [], []
        self.lang = self.title = None
        self.in_title = False
        self.ids, self.label_for, self.controls = {}, set(), []
        self.headings, self.named = [], []
        self.line = 1

    def _add(self, rule, wcag, impact, detail):
        self.violations.append({"rule": rule, "wcag": wcag, "impact": impact, "line": self.getpos()[0], "detail": detail})

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "").strip()
        if tag == "title":
            self.in_title, self.title = True, self.title or ""
        if "id" in a:
            self.ids.setdefault(a["id"], []).append(self.getpos()[0])
        if tag == "label" and a.get("for"):
            self.label_for.add(a["for"])
        hidden = a.get("aria-hidden") == "true" or "hidden" in a or a.get("type") == "hidden"
        if tag == "img" and "alt" not in a and a.get("role") != "presentation" and not a.get("aria-label") and not hidden:
            self._add("image-alt", "1.1.1", "serious", a.get("src", ""))
        if tag in ("input", "select", "textarea") and not hidden and a.get("type") not in ("submit", "button", "reset", "image"):
            inside_label = "label" in [t for t, _ in self.stack]
            self.controls.append((self.getpos()[0], a.get("id", ""), bool(a.get("aria-label") or a.get("aria-labelledby") or a.get("title")) or inside_label))
        if tag in ("button", "a") and not hidden and (tag == "button" or "href" in a):
            self.stack.append((tag, {"line": self.getpos()[0], "named": bool(a.get("aria-label") or a.get("aria-labelledby") or a.get("title")), "text": ""}))
            return
        if tag in ("img", "svg") and (a.get("alt", "").strip() or a.get("aria-label", "").strip()):
            owner = next((info for name, info in reversed(self.stack) if name in ("button", "a") and info), None)
            if owner is not None:
                owner["named"] = True  # an image's text alternative names its button or link
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append((int(tag[1]), self.getpos()[0]))
        if tag not in ("img", "input", "br", "hr", "meta", "link", "source", "area", "base", "col", "embed", "wbr"):
            self.stack.append((tag, {}))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                name, info = self.stack.pop(index)
                if name in ("button", "a") and info and not info["named"] and not info["text"].strip():
                    self.violations.append({"rule": "button-name" if name == "button" else "link-name", "wcag": "4.1.2",
                                            "impact": "serious", "line": info["line"], "detail": ""})
                break

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        for name, info in reversed(self.stack):
            if name in ("button", "a") and info:
                info["text"] += data
                break


def check_html(text: str) -> list[dict]:
    """Structural WCAG checks on one HTML document (no styles or layout; see AUDIT for those)."""
    parser = _Structure()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # malformed markup: report what was collected so far
        pass
    out = list(parser.violations)
    if "<html" in text.lower():
        if not parser.lang:
            out.append({"rule": "html-lang", "wcag": "3.1.1", "impact": "serious", "line": 1, "detail": "No lang attribute"})
        if not (parser.title or "").strip():
            out.append({"rule": "document-title", "wcag": "2.4.2", "impact": "serious", "line": 1, "detail": "No title"})
    for line, ident, named in parser.controls:
        if not named and ident not in parser.label_for:
            out.append({"rule": "control-label", "wcag": "1.3.1 / 4.1.2", "impact": "serious", "line": line, "detail": ""})
    for ident, lines in parser.ids.items():
        if len(lines) > 1:
            out.append({"rule": "duplicate-id", "wcag": "1.3.1", "impact": "moderate", "line": lines[1], "detail": f"#{ident} x{len(lines)}"})
    previous = 0
    for level, line in parser.headings:
        if previous and level > previous + 1:
            out.append({"rule": "heading-order", "wcag": "1.3.1", "impact": "moderate", "line": line, "detail": f"h{previous} then h{level}"})
        previous = level
    return sorted(out, key=lambda v: (v["impact"] != "serious", v["line"]))
