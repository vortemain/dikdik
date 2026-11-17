// Document readers: Word, Excel, PowerPoint, PDF and delimited tables.
//
// Pure rendering helpers — no app state. The orchestration (which file is
// open, hiding #file-view, breadcrumbs, renderFileTabs) stays in app.ts;
// this module only puts content into the #doc-view element it is handed.
// Server-provided text is always textContent, never parsed as HTML.

export type DocKind = "docx" | "xlsx" | "pptx" | "pdf" | "table";

/** Maps a workspace path to a document kind, or null if it is not a document. */
export function docKind(path: string): DocKind | null {
  const suffix = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (suffix === "docx") return "docx";
  if (suffix === "xlsx" || suffix === "xlsm") return "xlsx";
  if (suffix === "pptx") return "pptx";
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

export interface DocViewerView {
  /** Replace the viewer's content with a "reading…" placeholder. */
  loading(view: HTMLElement, make: Make): void;
  /** Render the browser's native PDF reader in an same-origin iframe. */
  pdf(view: HTMLElement, make: Make, path: string): void;
  /** Render extracted Word document text, headings and tables. */
  word(view: HTMLElement, make: Make, data: Record<string, unknown>): void;
  /** Render slide text as a stack of slide cards. */
  slides(view: HTMLElement, make: Make, data: Record<string, unknown>): void;
  /** Render a spreadsheet or CSV/TSV as a grid with sheet tabs. */
  grid(view: HTMLElement, make: Make, data: Record<string, unknown>): void;
  /** Render an error message in place of document content. */
  error(view: HTMLElement, make: Make, message: string): void;
}

export const docViewer: DocViewerView = {
  loading(view, make) {
    view.replaceChildren(make("p", "hint", "Reading the document…"));
  },
  pdf(view, make, path) {
    const frame = make("iframe", "pdf-frame");
    frame.title = `PDF reader for ${path}`;
    frame.src = `/api/files/raw?path=${encodeURIComponent(path)}`;
    view.replaceChildren(
      make("p", "doc-note", "Rendered by your browser's PDF reader. Select a passage, then Ask about it below."),
      frame);
  },
  word(view, make, data) {
    const page = make("article", "doc-page word-page");
    const blocks = Array.isArray(data.blocks) ? data.blocks as Record<string, unknown>[] : [];
    if (!blocks.length) page.append(make("p", "muted", "This document has no readable paragraphs."));
    const headingTags = ["h2", "h3", "h4"] as const;
    for (const block of blocks) {
      const text = typeof block.text === "string" ? block.text : "";
      if (block.type === "h") {
        const level = Math.min(Math.max(Number(block.level) || 1, 1), 3);
        page.append(make(headingTags[level - 1] ?? "h2", `doc-heading doc-h${level}`, text));
      } else if (block.type === "li") {
        page.append(make("p", "doc-li", text));
      } else if (block.type === "table") {
        page.append(docTable(make, block.rows as string[][], true));
      } else if (text) page.append(make("p", "doc-para", text));
    }
    const truncated = data.truncated === true;
    view.replaceChildren(
      make("p", "doc-note", truncated
        ? "Extracted from the document (some parts shortened). Images and advanced layout are simplified."
        : "Extracted from the document: text, headings and tables. Images and advanced layout are simplified."),
      page);
  },
  slides(view, make, data) {
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
      deck.append(card);
    });
    if (!slides.length) deck.append(make("p", "muted", "No readable slide text was found."));
    view.replaceChildren(make("p", "doc-note", "Slide text as it was extracted from the presentation."), deck);
  },
  grid(view, make, data) {
    const bar = make("div", "doc-sheetbar");
    const gridHolder = make("div", "doc-grid-holder");
    const sheets = Array.isArray(data.sheets) ? data.sheets as { name?: unknown; rows?: unknown; truncated?: unknown }[] : [];
    const csvRows = Array.isArray(data.rows) ? data.rows as string[][] : [];
    const hasHeader = data.has_header === true;
    const status = make("p", "doc-note", "");
    const show = (rows: string[][], header: boolean, name: string, truncated: boolean) => {
      const cap = 300;
      const shown = rows.slice(0, cap);
      gridHolder.replaceChildren(docTable(make, shown, header));
      status.textContent = `${name} · ${rows.length.toLocaleString()} row${rows.length === 1 ? "" : "s"}`
        + (truncated || rows.length > cap ? ` · showing the first ${Math.min(rows.length, cap).toLocaleString()}` : "")
        + (header ? " · first row read as the header" : "");
    };
    if (sheets.length) {
      for (const sheet of sheets) {
        const name = typeof sheet.name === "string" ? sheet.name : "Sheet";
        const tab = make("button", "doc-sheet-tab");
        tab.type = "button";
        tab.append(make("span", "", name));
        tab.onclick = () => {
          for (const other of bar.querySelectorAll<HTMLButtonElement>(".doc-sheet-tab")) other.classList.remove("on");
          tab.classList.add("on");
          show(Array.isArray(sheet.rows) ? sheet.rows as string[][] : [], true, name, sheet.truncated === true);
        };
        bar.append(tab);
      }
      (bar.firstElementChild as HTMLElement | null)?.classList.add("on");
      const first = sheets[0];
      show(Array.isArray(first?.rows) ? first.rows as string[][] : [], true,
        typeof first?.name === "string" ? first.name : "Sheet", first?.truncated === true);
    } else {
      show(csvRows, hasHeader, "CSV", false);
    }
    view.replaceChildren(bar, gridHolder, status);
  },
  error(view, make, message) {
    view.replaceChildren(make("p", "muted", message));
  },
};

/** A bounded HTML table from extracted rows; the first row is a header when `header` is true. */
function docTable(make: Make, rows: string[][], header: boolean): HTMLElement {
  const wrap = make("div", "doc-grid");
  const table = make("table", "doc-grid-table");
  if (rows.length && header) {
    const thead = make("thead");
    const head = make("tr");
    head.append(make("th", "row-gutter", ""));
    for (const cell of rows[0] ?? []) head.append(make("th", "", cell));
    thead.append(head);
    table.append(thead);
  }
  const tbody = make("tbody");
  const body = header && rows.length ? rows.slice(1) : rows;
  body.forEach((row, rowIndex) => {
    const tr = make("tr");
    tr.append(make("th", "row-gutter", String(header ? rowIndex + 2 : rowIndex + 1)));
    for (const cell of row) tr.append(make("td", "", cell));
    tbody.append(tr);
  });
  table.append(tbody);
  wrap.append(table);
  return wrap;
}