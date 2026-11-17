// A spreadsheet view like Excel's, for every screen size.
//
// Sheet tabs; a cell bar that shows the selected cell's full value (long answers are
// clipped in the grid, never squeezed into one-letter columns); column letters and row
// numbers that stay in place while the grid scrolls both ways; a frozen header row;
// numbers aligned right; a search box that keeps matching rows; and rows shown in pages
// of 500 so a large sheet stays fast. Text is always textContent, never HTML.

import type { Make } from "./doc-viewer";

export interface SheetData { name: string; rows: string[][]; truncated: boolean; header: boolean }

/** Spreadsheet column letters: 0 -> A, 25 -> Z, 26 -> AA. */
export function columnName(index: number): string {
  let name = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) name = String.fromCharCode(65 + (n - 1) % 26) + name;
  return name;
}

const NUMBER = /^[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?([eE][-+]?\d+)?%?$/;
const PAGE = 500;

/** Which columns hold numbers (at least 80% of their filled cells), so they align right. */
export function numericColumns(rows: string[][], width: number): boolean[] {
  return Array.from({ length: width }, (_, column) => {
    let numbers = 0, filled = 0;
    for (const row of rows.slice(0, 300)) {
      const cell = String(row[column] ?? "").trim();
      if (!cell) continue;
      filled++;
      if (NUMBER.test(cell) && /\d/.test(cell)) numbers++;
    }
    return filled > 0 && numbers / filled >= 0.8;
  });
}

export function spreadsheet(make: Make, sheets: SheetData[]): HTMLElement {
  const root = make("div", "sheet");
  const toolbar = make("div", "sheet-toolbar");
  const tabs = make("div", "sheet-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Sheets");
  const search = make("input", "sheet-search") as HTMLInputElement;
  search.type = "search";
  search.placeholder = "Find in sheet";
  search.setAttribute("aria-label", "Find in sheet");
  toolbar.append(tabs, search);
  const cellBar = make("div", "sheet-cellbar");
  const reference = make("span", "sheet-ref", "");
  const value = make("span", "sheet-value", "Tap or click a cell to see its full value");
  cellBar.append(reference, value);
  const scroller = make("div", "sheet-scroll");
  scroller.tabIndex = 0;
  scroller.setAttribute("aria-label", "Sheet cells");
  const status = make("div", "sheet-status");
  const statusText = make("span", "sheet-status-text", "");
  const more = make("button", "ghost small sheet-more") as HTMLButtonElement;
  more.type = "button";
  status.append(statusText, more);
  root.append(toolbar, cellBar, scroller, status);

  let current = sheets[0] ?? { name: "Sheet", rows: [], truncated: false, header: false };
  let limit = PAGE, query = "";
  let selected: HTMLElement | null = null;

  const render = () => {
    const rows = current.rows;
    const width = rows.reduce((most, row) => Math.max(most, row.length), 0);
    const headerRow = current.header && rows.length > 1 ? rows[0] ?? [] : null;
    const body = (headerRow ? rows.slice(1) : rows).map((row, index) => ({ row, number: index + (headerRow ? 2 : 1) }));
    const needle = query.trim().toLowerCase();
    const matching = needle ? body.filter(item => item.row.some(cell => String(cell).toLowerCase().includes(needle))) : body;
    const shown = matching.slice(0, limit);
    const numeric = numericColumns(body.map(item => item.row), width);

    const table = make("table", "sheet-table");
    const head = make("thead");
    const letters = make("tr", "sheet-letters");
    letters.append(make("th", "sheet-corner", ""));
    for (let column = 0; column < width; column++) letters.append(make("th", "sheet-col", columnName(column)));
    head.append(letters);
    if (headerRow) {
      const names = make("tr", "sheet-header");
      names.append(make("th", "sheet-rownum", "1"));
      for (let column = 0; column < width; column++) {
        const text = String(headerRow[column] ?? "");
        const cell = make("th", numeric[column] ? "num" : "");
        cell.append(make("div", "sheet-head-text", text));
        cell.title = text;
        cell.dataset.ref = `${columnName(column)}1`;
        names.append(cell);
      }
      head.append(names);
    }
    const tbody = make("tbody");
    for (const item of shown) {
      const tr = make("tr");
      tr.append(make("th", "sheet-rownum", String(item.number)));
      for (let column = 0; column < width; column++) {
        const text = String(item.row[column] ?? "");
        const cell = make("td", numeric[column] ? "num" : "");
        if (text) {
          cell.append(make("div", "sheet-cell", text));
          if (needle && text.toLowerCase().includes(needle)) cell.classList.add("hit");
        }
        cell.dataset.ref = `${columnName(column)}${item.number}`;
        tr.append(cell);
      }
      tbody.append(tr);
    }
    table.append(head, tbody);
    scroller.replaceChildren(width ? table : make("p", "sheet-empty", "This sheet is empty."));
    selected = null;
    reference.textContent = "";
    value.textContent = "Tap or click a cell to see its full value";
    const total = body.length;
    statusText.textContent = `${current.name} · ${total.toLocaleString()} row${total === 1 ? "" : "s"} × ${width} column${width === 1 ? "" : "s"}`
      + (needle ? ` · ${matching.length.toLocaleString()} matching` : "")
      + (current.truncated ? " · more rows in the file: download it for all of them" : "");
    more.hidden = matching.length <= shown.length;
    more.textContent = `Show ${Math.min(PAGE, matching.length - shown.length).toLocaleString()} more rows`;
  };

  scroller.addEventListener("click", event => {
    const target = event.target as HTMLElement | null;
    const cell = target?.closest?.("td, .sheet-header th") as HTMLElement | null;
    if (!cell?.dataset.ref) return;
    selected?.classList.remove("selected");
    selected = cell;
    cell.classList.add("selected");
    reference.textContent = cell.dataset.ref;
    value.textContent = cell.textContent || "(empty)";
  });
  more.onclick = () => { limit += PAGE; render(); };
  let timer: ReturnType<typeof setTimeout> | undefined;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { query = search.value; limit = PAGE; render(); }, 150);
  });
  sheets.forEach((sheet, index) => {
    const tab = make("button", "sheet-tab", sheet.name) as HTMLButtonElement;
    tab.type = "button";
    tab.title = sheet.name;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(index === 0));
    tab.onclick = () => {
      for (const other of tabs.querySelectorAll("button")) other.setAttribute("aria-selected", String(other === tab));
      current = sheet;
      limit = PAGE;
      render();
      scroller.scrollTo?.(0, 0);
    };
    tabs.append(tab);
  });
  tabs.hidden = sheets.length < 2;
  render();
  return root;
}
