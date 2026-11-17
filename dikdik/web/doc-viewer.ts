// Document readers: Word, Excel, PowerPoint, PDF, images, delimited tables and text.
//
// Pure rendering helpers — no app state. The orchestration (which file is
// open, hiding #file-view, breadcrumbs, renderFileTabs, the preview dialog) stays
// in the callers; this module only puts content into the element it is handed.
// Server-provided text is always textContent, never parsed as HTML; pictures load
// from same-origin URLs the caller builds, and charts are drawn as SVG from their
// own data.

import { spreadsheet, type SheetData } from "./spreadsheet";

export type DocKind = "docx" | "xlsx" | "pptx" | "pdf" | "table";

/** Maps a workspace path to a document kind, or null if it is not a document. */
export function docKind(path: string): DocKind | null {
  const suffix = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  // OpenDocument files come back from the server in the same shapes as their Office cousins.
  if (suffix === "docx" || suffix === "odt") return "docx";
  if (suffix === "xlsx" || suffix === "xlsm" || suffix === "ods" || suffix === "xls") return "xlsx";
  if (suffix === "pptx" || suffix === "odp") return "pptx";
  if (suffix === "pdf") return "pdf";
  if (suffix === "csv" || suffix === "tsv") return "table";
  return null;
}

export const DOC_KIND_LABELS: Record<DocKind, string> = {
  docx: "Word document", xlsx: "Spreadsheet", pptx: "Presentation", pdf: "PDF", table: "Data table",
};

// Matches the make() helper in app.ts: a thin wrapper over document.createElement
// that sets className/textContent safely. Re-declared here so this module has no
// import cycle with app.ts.
export type Make = <K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) => HTMLElementTagNameMap[K];

/** Where a document's embedded pictures load from (an Office "media" part), when the caller can serve them. */
export interface DocMedia { media?: (part: string) => string }

export interface DocViewerView {
  /** Replace the viewer's content with a "reading…" placeholder. */
  loading(view: HTMLElement, make: Make): void;
  /** Render the browser's native PDF reader in a same-origin iframe (`url` overrides the workspace route). */
  pdf(view: HTMLElement, make: Make, path: string, url?: string): void;
  /** Render a Word document: headings, formatted text, lists, tables, pictures and charts. */
  word(view: HTMLElement, make: Make, data: Record<string, unknown>, options?: DocMedia): void;
  /** Render slide text (and pictures) as a stack of slide cards. */
  slides(view: HTMLElement, make: Make, data: Record<string, unknown>, options?: DocMedia): void;
  /** Render a spreadsheet or CSV/TSV as a grid with sheet tabs. */
  grid(view: HTMLElement, make: Make, data: Record<string, unknown>): void;
  /** Render an image file. */
  image(view: HTMLElement, make: Make, url: string, name: string): void;
  /** Render plain text (RTF, HTML, Markdown, code) as a page of preformatted text. */
  text(view: HTMLElement, make: Make, data: Record<string, unknown>): void;
  /** Render an error message in place of document content. */
  error(view: HTMLElement, make: Make, message: string): void;
}

type Block = Record<string, unknown>;
type Run = { t?: string; b?: number; i?: number; u?: number; s?: number; sup?: number; sub?: number; href?: string };

/** Formatted runs as inline elements; links open in a new tab and only http(s)/mailto are allowed. */
function inlineRuns(make: Make, host: HTMLElement, runs: Run[]): void {
  for (const run of runs) {
    const text = typeof run.t === "string" ? run.t : "";
    if (!text) continue;
    let node: HTMLElement = make("span", "", text);
    if (run.b) { const wrap = make("strong"); wrap.append(node); node = wrap; }
    if (run.i) { const wrap = make("em"); wrap.append(node); node = wrap; }
    if (run.u) node.style.textDecoration = "underline";
    if (run.s) node.style.textDecoration = "line-through";
    if (run.sup) { const wrap = make("sup"); wrap.append(node); node = wrap; }
    if (run.sub) { const wrap = make("sub"); wrap.append(node); node = wrap; }
    if (typeof run.href === "string" && /^(https?:|mailto:)/i.test(run.href)) {
      const link = make("a", "doc-link");
      link.href = run.href; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.append(node); node = link;
    }
    host.append(node);
  }
}

const SVG = "http://www.w3.org/2000/svg";
const PALETTE = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47", "#264478", "#9E480E"];

function svgNode(tag: string, attributes: Record<string, string | number>, text?: string): SVGElement {
  const node = document.createElementNS(SVG, tag) as SVGElement;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = Math.pow(10, Math.floor(Math.log10(value)));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(candidate => candidate * power >= value) ?? 10;
  return step * power;
}

function shortLabel(text: string, max = 16): string { return text.length > max ? `${text.slice(0, max - 1)}…` : text; }

/** A chart drawn from its cached data: column, bar, line, pie or doughnut. */
export function chartFigure(make: Make, chart: Record<string, unknown>): HTMLElement {
  const figure = make("figure", "doc-chart");
  const kind = typeof chart.type === "string" ? chart.type : "column";
  const title = typeof chart.title === "string" ? chart.title : "";
  const categories = Array.isArray(chart.categories) ? (chart.categories as unknown[]).map(String) : [];
  const series = (Array.isArray(chart.series) ? chart.series as { name?: unknown; values?: unknown }[] : []).map(item => ({
    name: typeof item.name === "string" ? item.name : "",
    values: (Array.isArray(item.values) ? item.values : []).map(value => typeof value === "number" && Number.isFinite(value) ? value : 0),
  }));
  if (title) figure.append(make("figcaption", "doc-chart-title", title));
  if (typeof document.createElementNS !== "function" || !series.length || !categories.length) {
    figure.append(make("p", "muted", "Chart data could not be read."));
    return figure;
  }
  const width = 640, height = 340;
  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": title || "Chart", class: "doc-chart-svg" });
  if (kind === "pie" || kind === "doughnut") {
    const values = series[0]!.values;
    const total = values.reduce((sum, value) => sum + Math.max(value, 0), 0) || 1;
    const cx = 200, cy = 170, r = 140, hole = kind === "doughnut" ? 70 : 0;
    let angle = -Math.PI / 2;
    values.forEach((value, index) => {
      const slice = Math.max(value, 0) / total * Math.PI * 2;
      const end = angle + slice;
      const large = slice > Math.PI ? 1 : 0;
      const point = (radius: number, theta: number) => `${cx + radius * Math.cos(theta)} ${cy + radius * Math.sin(theta)}`;
      const path = hole
        ? `M ${point(r, angle)} A ${r} ${r} 0 ${large} 1 ${point(r, end)} L ${point(hole, end)} A ${hole} ${hole} 0 ${large} 0 ${point(hole, angle)} Z`
        : `M ${cx} ${cy} L ${point(r, angle)} A ${r} ${r} 0 ${large} 1 ${point(r, end)} Z`;
      svg.append(svgNode(slice >= Math.PI * 2 - 1e-6 ? "circle" : "path", slice >= Math.PI * 2 - 1e-6
        ? { cx, cy, r, fill: PALETTE[index % PALETTE.length]! } : { d: path, fill: PALETTE[index % PALETTE.length]!, stroke: "#fff", "stroke-width": 1.5 }));
      if (slice > 0.25) {
        const middle = angle + slice / 2, at = hole ? (r + hole) / 2 : r * 0.62;
        svg.append(svgNode("text", { x: cx + at * Math.cos(middle), y: cy + at * Math.sin(middle) + 4, "text-anchor": "middle", class: "doc-chart-onbar" },
          `${Math.round(value / total * 100)}%`));
      }
      angle = end;
    });
    categories.forEach((category, index) => {
      const y = 40 + index * 24;
      if (y > height - 10) return;
      svg.append(svgNode("rect", { x: 380, y: y - 11, width: 12, height: 12, rx: 2, fill: PALETTE[index % PALETTE.length]! }));
      svg.append(svgNode("text", { x: 400, y, class: "doc-chart-label" }, `${shortLabel(category, 26)} (${series[0]!.values[index] ?? 0})`));
    });
    figure.append(svg);
    return figure;
  }
  const horizontal = kind === "bar";
  const left = horizontal ? 130 : 48, right = 16, top = 16, bottom = series.length > 1 ? 70 : 52;
  const plotW = width - left - right, plotH = height - top - bottom;
  const all = series.flatMap(item => item.values);
  const max = niceMax(Math.max(...all, 0)), min = Math.min(0, ...all);
  const span = max - min || 1;
  const ticks = 5;
  for (let tick = 0; tick <= ticks; tick++) {
    const value = min + span * tick / ticks;
    const label = Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    if (horizontal) {
      const x = left + plotW * tick / ticks;
      svg.append(svgNode("line", { x1: x, x2: x, y1: top, y2: top + plotH, class: "doc-chart-grid" }));
      svg.append(svgNode("text", { x, y: top + plotH + 16, "text-anchor": "middle", class: "doc-chart-axis" }, label));
    } else {
      const y = top + plotH - plotH * tick / ticks;
      svg.append(svgNode("line", { x1: left, x2: left + plotW, y1: y, y2: y, class: "doc-chart-grid" }));
      svg.append(svgNode("text", { x: left - 6, y: y + 4, "text-anchor": "end", class: "doc-chart-axis" }, label));
    }
  }
  const band = (horizontal ? plotH : plotW) / categories.length;
  const scale = (value: number) => (value - min) / span;
  if (kind === "line") {
    series.forEach((item, index) => {
      const points = item.values.map((value, position) => `${left + band * (position + 0.5)},${top + plotH - plotH * scale(value)}`);
      svg.append(svgNode("polyline", { points: points.join(" "), fill: "none", stroke: PALETTE[index % PALETTE.length]!, "stroke-width": 2.5 }));
      item.values.forEach((value, position) => svg.append(svgNode("circle", {
        cx: left + band * (position + 0.5), cy: top + plotH - plotH * scale(value), r: 3.5, fill: PALETTE[index % PALETTE.length]! })));
    });
  } else {
    const inner = band * 0.72, each = inner / series.length;
    series.forEach((item, index) => item.values.forEach((value, position) => {
      const start = band * position + (band - inner) / 2 + each * index;
      const length = (horizontal ? plotW : plotH) * Math.abs(scale(value) - scale(0));
      const fill = PALETTE[index % PALETTE.length]!;
      const label = Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
      if (horizontal) {
        const x = left + plotW * scale(Math.min(value, 0));
        svg.append(svgNode("rect", { x, y: top + start, width: Math.max(length, 0.5), height: Math.max(each - 2, 1), fill, rx: 1.5 }));
        svg.append(svgNode("text", { x: x + length + 4, y: top + start + each / 2 + 4, class: "doc-chart-value" }, label));
      } else {
        const y = top + plotH - plotH * scale(Math.max(value, 0));
        svg.append(svgNode("rect", { x: left + start, y, width: Math.max(each - 2, 1), height: Math.max(length, 0.5), fill, rx: 1.5 }));
        svg.append(svgNode("text", { x: left + start + each / 2 - 1, y: y - 5, "text-anchor": "middle", class: "doc-chart-value" }, label));
      }
    }));
  }
  categories.forEach((category, position) => {
    if (horizontal) svg.append(svgNode("text", { x: left - 8, y: top + band * (position + 0.5) + 4, "text-anchor": "end", class: "doc-chart-label" }, shortLabel(category, 20)));
    else svg.append(svgNode("text", { x: left + band * (position + 0.5), y: top + plotH + 18, "text-anchor": "middle", class: "doc-chart-label" },
      shortLabel(category, Math.max(6, Math.floor(band / 7)))));
  });
  svg.append(svgNode("line", { x1: left, x2: horizontal ? left : left + plotW, y1: top + plotH, y2: horizontal ? top : top + plotH, class: "doc-chart-baseline" }));
  if (series.length > 1) {
    let x = left;
    series.forEach((item, index) => {
      svg.append(svgNode("rect", { x, y: height - 22, width: 12, height: 12, rx: 2, fill: PALETTE[index % PALETTE.length]! }));
      svg.append(svgNode("text", { x: x + 17, y: height - 12, class: "doc-chart-label" }, shortLabel(item.name, 22)));
      x += 30 + Math.min(item.name.length, 22) * 7;
    });
  }
  figure.append(svg);
  return figure;
}

function picture(make: Make, url: string, label: string): HTMLElement {
  const holder = make("figure", "doc-picture");
  const image = make("img") as HTMLImageElement;
  image.src = url; image.alt = label; image.loading = "lazy";
  image.onerror = () => { holder.replaceChildren(make("p", "muted", `Picture (${label.split("/").pop()}) — this format cannot be shown in the browser.`)); };
  holder.append(image);
  return holder;
}

export const docViewer: DocViewerView = {
  loading(view, make) {
    view.replaceChildren(make("p", "hint", "Reading the document…"));
  },
  pdf(view, make, path, url) {
    const frame = make("iframe", "pdf-frame");
    frame.title = `PDF reader for ${path}`;
    frame.src = url ?? `/api/files/raw?path=${encodeURIComponent(path)}`;
    view.replaceChildren(
      make("p", "doc-note", "Rendered by your browser's PDF reader."),
      frame);
  },
  word(view, make, data, options) {
    const page = make("article", "doc-page word-page");
    const blocks = Array.isArray(data.blocks) ? data.blocks as Block[] : [];
    if (!blocks.length) page.append(make("p", "muted", "This document has no readable paragraphs."));
    const headingTags = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;
    let pages = 1;
    for (const block of blocks) {
      const text = typeof block.text === "string" ? block.text : "";
      const runs = Array.isArray(block.runs) ? block.runs as Run[] : null;
      const fill = (node: HTMLElement) => { if (runs) inlineRuns(make, node, runs); else node.textContent = text; return node; };
      const align = typeof block.align === "string" && ["center", "right", "justify"].includes(block.align) ? block.align : "";
      let node: HTMLElement | null = null;
      if (block.type === "pagebreak") { page.append(make("div", "doc-pagebreak", `Page ${++pages}`)); continue; }
      if (block.type === "image" && typeof block.part === "string") {
        if (options?.media) page.append(picture(make, options.media(block.part), block.part));
        else page.append(make("p", "doc-picture-missing", "[Picture]"));
        continue;
      }
      if (block.type === "chart" && block.chart && typeof block.chart === "object") { page.append(chartFigure(make, block.chart as Record<string, unknown>)); continue; }
      if (block.type === "h") {
        const level = Math.min(Math.max(Number(block.level) || 1, 1), 6);
        node = fill(make(headingTags[level - 1] ?? "h2", `doc-heading doc-h${Math.min(level, 4)}${block.style === "title" ? " doc-title" : ""}`));
      } else if (block.type === "li") {
        node = make("p", "doc-li doc-li-marked");
        const level = Math.min(Number(block.level) || 0, 6);
        node.style.paddingLeft = `${22 + level * 22}px`;
        node.append(make("span", "doc-li-marker", typeof block.marker === "string" ? block.marker : "•"));
        fill(node);
      } else if (block.type === "table") {
        page.append(docTable(make, block.rows as string[][], true, false));
        continue;
      } else if (text) {
        const style = typeof block.style === "string" ? block.style : block.caption ? "caption" : "";
        node = fill(make("p", `doc-para${style ? ` doc-${style}` : ""}`));
      }
      if (node) { if (align) node.style.textAlign = align; page.append(node); }
    }
    const notes = Array.isArray(data.notes) ? data.notes.filter(note => typeof note === "string") as string[] : [];
    if (notes.length) {
      page.append(make("hr", "doc-notes-rule"));
      notes.forEach((note, index) => page.append(make("p", "doc-footnote", `${index + 1}. ${note}`)));
    }
    const rich = data.rich === true;
    view.replaceChildren(
      make("p", "doc-note", rich
        ? "Formatting, lists, tables, pictures and charts are shown; exact page layout may differ from Word."
        : data.truncated === true
          ? "Extracted from the document (some parts shortened). Images and advanced layout are simplified."
          : "Extracted from the document: text, headings and tables. Images and advanced layout are simplified."),
      page);
  },
  slides(view, make, data, options) {
    const deck = make("div", "doc-slides");
    const slides = Array.isArray(data.slides) ? data.slides as Record<string, unknown>[] : [];
    slides.forEach((slide, index) => {
      const card = make("article", "doc-slide");
      card.append(make("p", "doc-slide-number", `Slide ${index + 1}`));
      if (typeof slide.title === "string" && slide.title) card.append(make("h3", "doc-heading", slide.title));
      const points = Array.isArray(slide.points) ? slide.points.filter(point => typeof point === "string") as string[] : [];
      if (points.length) {
        const list = make("ul", "doc-li-list");
        for (const point of points) list.append(make("li", "", point));
        card.append(list);
      }
      const images = Array.isArray(slide.images) ? slide.images.filter(item => typeof item === "string") as string[] : [];
      if (images.length && options?.media) {
        const strip = make("div", "doc-slide-images");
        for (const part of images) strip.append(picture(make, options.media(part), part));
        card.append(strip);
      }
      if (typeof slide.notes === "string" && slide.notes) card.append(make("p", "doc-slide-notes", `Speaker notes: ${slide.notes}`));
      deck.append(card);
    });
    if (!slides.length) deck.append(make("p", "muted", "No readable slide text was found."));
    view.replaceChildren(make("p", "doc-note", "Slide text and pictures as they were extracted from the presentation."), deck);
  },
  grid(view, make, data) {
    const sheets = Array.isArray(data.sheets) ? data.sheets as { name?: unknown; rows?: unknown; truncated?: unknown }[] : [];
    const list: SheetData[] = sheets.length
      ? sheets.map(sheet => ({ name: typeof sheet.name === "string" ? sheet.name : "Sheet", rows: Array.isArray(sheet.rows) ? sheet.rows as string[][] : [],
        truncated: sheet.truncated === true, header: true }))
      : [{ name: data.delimiter === "tab" ? "TSV" : "CSV", rows: Array.isArray(data.rows) ? data.rows as string[][] : [], truncated: false,
        header: data.has_header === true || looksLikeHeader(Array.isArray(data.rows) ? data.rows as string[][] : []) }];
    view.replaceChildren(spreadsheet(make, list));
  },
  image(view, make, url, name) {
    const holder = make("div", "doc-image-view");
    holder.append(picture(make, url, name));
    view.replaceChildren(holder);
  },
  text(view, make, data) {
    const page = make("article", "doc-page text-page");
    page.append(make("pre", "doc-text", typeof data.text === "string" ? data.text : ""));
    view.replaceChildren(page);
  },
  error(view, make, message) {
    view.replaceChildren(make("p", "muted", message));
  },
};

/** A CSV whose first row is all text while later rows hold numbers reads as a header row. */
function looksLikeHeader(rows: string[][]): boolean {
  if (rows.length < 2) return false;
  const first = rows[0] ?? [];
  const numeric = (cell: string) => /^[-+]?[\d,.]+%?$/.test(cell.trim());
  return first.length > 0 && first.every(cell => cell.trim() && !numeric(cell)) && rows.slice(1, 20).some(row => row.some(cell => numeric(cell)) || row.length === first.length);
}

/** A bounded HTML table from extracted rows; the first row is a header when `header` is true. */
function docTable(make: Make, rows: string[][], header: boolean, gutter = true): HTMLElement {
  const wrap = make("div", gutter ? "doc-grid" : "doc-grid doc-word-table");
  const table = make("table", "doc-grid-table");
  if (rows.length && header) {
    const thead = make("thead");
    const head = make("tr");
    if (gutter) head.append(make("th", "row-gutter", ""));
    for (const cell of rows[0] ?? []) head.append(make("th", "", cell));
    thead.append(head);
    table.append(thead);
  }
  const tbody = make("tbody");
  const body = header && rows.length ? rows.slice(1) : rows;
  body.forEach((row, rowIndex) => {
    const tr = make("tr");
    if (gutter) tr.append(make("th", "row-gutter", String(header ? rowIndex + 2 : rowIndex + 1)));
    for (const cell of row) tr.append(make("td", "", cell));
    tbody.append(tr);
  });
  table.append(tbody);
  wrap.append(table);
  return wrap;
}
