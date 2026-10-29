"""Short-lived, static HTML previews. No scripts, navigation, forms or remote assets."""
import html
import re
import threading
import time
import uuid
from html.parser import HTMLParser

PREVIEW_CSP = ("sandbox; default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
               "img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
TAGS = set(('a abbr address article aside b blockquote br button caption code col colgroup dd del details div dl dt em '
            'figcaption figure footer h1 h2 h3 h4 h5 h6 header hr i img input label li main mark nav ol p pre s section '
            'small span strong style sub summary sup table tbody td textarea tfoot th thead time tr u ul').split())
VOID = {'br', 'col', 'hr', 'img', 'input'}
DROP = {'script', 'iframe', 'object', 'embed', 'template', 'noscript', 'svg', 'math'}
ATTRS = {'id', 'class', 'style', 'title', 'lang', 'dir', 'role', 'colspan', 'rowspan', 'scope', 'width', 'height',
         'alt', 'open', 'checked', 'disabled', 'placeholder', 'value', 'type', 'start', 'reversed'}


class _StaticHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skipping, self.in_style = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            if tag not in {'embed'}:
                self.skipping.append(tag)
            return
        if self.skipping or tag not in TAGS:
            return
        kept = []
        for name, value in attrs:
            if name in ATTRS or name.startswith('aria-'):
                kept.append(f' {name}="{html.escape(value or "", quote=True)}"')
            elif tag == 'a' and name == 'href' and value and value.startswith('#'):
                kept.append(f' href="{html.escape(value, quote=True)}"')
            elif tag == 'img' and name == 'src' and value and re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+', value):
                kept.append(f' src="{html.escape(value, quote=True)}"')
        if tag in {'input', 'button', 'textarea'}:
            kept.append(' disabled')
        self.parts.append(f'<{tag}{"".join(kept)}>')
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
            return
        if tag in TAGS and tag not in VOID:
            self.parts.append(f'</{tag}>')
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data if self.in_style else html.escape(data))


def static_preview(source):
    if not isinstance(source, str) or not source.strip() or len(source.encode('utf-8')) > 50000:
        raise ValueError('HTML previews require 1–50 KB of HTML')
    parser = _StaticHTML()
    parser.feed(source)
    parser.close()
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Isolated design preview</title><style>body{margin:16px;font-family:system-ui,sans-serif;overflow-wrap:anywhere}*{box-sizing:border-box}</style>'
            '</head><body>' + ''.join(parser.parts) + '</body></html>').encode('utf-8')


class HTMLPreviews:
    def __init__(self):
        self.lock, self.entries = threading.Lock(), {}

    def create(self, source, workspace):
        page, identity, now = static_preview(source), uuid.uuid4().hex, time.monotonic()
        with self.lock:
            self.entries = {key: value for key, value in self.entries.items() if now - value[0] < 600}
            while len(self.entries) >= 16:
                self.entries.pop(next(iter(self.entries)))
            self.entries[identity] = (now, workspace, page)
        return {'url': '/api/previews/' + identity}

    def get(self, identity, workspace):
        with self.lock:
            entry = self.entries.get(identity)
            if entry is None or entry[1] != workspace or time.monotonic() - entry[0] >= 600:
                raise KeyError(identity)
            return entry[2]
