// In-app preview of a file from the conversation: a document the chat created, or one
// the user attached. Opens a large dialog with the right reader (Word pages with
// formatting, pictures and charts; spreadsheet grids; slides; the browser's own PDF
// and image viewers; text), plus Download and, on this computer, Open in its app.

import { docViewer, type Make } from "./doc-viewer";

export interface PreviewSource {
  name: string;
  /** JSON structure for the reader: {kind: "docx"|"xlsx"|"pptx"|"table"|"text"|"pdf"|"image", ...}. */
  viewUrl: string;
  /** The file itself, for the browser's PDF and image readers. */
  rawUrl: string;
  /** An embedded picture (Office media part) of the file. */
  mediaUrl: (part: string) => string;
  downloadUrl: string;
  /** Open the file in its own application (Word, Excel…) on this computer. */
  openInApp?: () => Promise<void>;
}

export interface PreviewDeps {
  make: Make;
  icon: (name: string) => Element;
  api: <T>(url: string) => Promise<T>;
  message: (text: string, error?: boolean) => void;
}

const LABELS: Record<string, string> = {
  docx: "Word document", xlsx: "Spreadsheet", pptx: "Presentation", table: "Data table", pdf: "PDF", image: "Image", text: "Text",
};

let dialog: HTMLDialogElement | null = null;

function ensureDialog(deps: PreviewDeps): HTMLDialogElement {
  if (dialog && dialog.isConnected) return dialog;
  const { make, icon } = deps;
  dialog = make("dialog", "doc-preview-dialog") as HTMLDialogElement;
  dialog.setAttribute("aria-labelledby", "doc-preview-name");
  const head = make("header", "doc-preview-head");
  const badge = make("span", "doc-preview-icon"); badge.append(icon("file"));
  const titles = make("div", "doc-preview-titles");
  const name = make("strong", "doc-preview-name"); name.id = "doc-preview-name";
  titles.append(name, make("span", "doc-preview-kind"));
  const actions = make("div", "doc-preview-actions");
  const open = make("button", "ghost small doc-preview-open") as HTMLButtonElement; open.type = "button";
  open.append(icon("external"), make("span", "", "Open in app"));
  const download = make("a", "doc-preview-download") as HTMLAnchorElement;
  download.append(icon("download"), make("span", "", "Download"));
  const close = make("button", "ghost icon doc-preview-close") as HTMLButtonElement; close.type = "button";
  close.setAttribute("aria-label", "Close preview"); close.append(icon("x"));
  close.onclick = () => dialog?.close();
  actions.append(open, download, close);
  head.append(badge, titles, actions);
  const body = make("div", "doc-view doc-preview-body");
  dialog.append(head, body);
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog?.close(); });  // backdrop click
  document.body.append(dialog);
  return dialog;
}

export async function openDocPreview(source: PreviewSource, deps: PreviewDeps): Promise<void> {
  const box = ensureDialog(deps);
  const { make } = deps;
  const body = box.querySelector<HTMLElement>(".doc-preview-body")!;
  box.querySelector(".doc-preview-name")!.textContent = source.name;
  box.querySelector(".doc-preview-kind")!.textContent = "";
  const download = box.querySelector<HTMLAnchorElement>(".doc-preview-download")!;
  download.href = source.downloadUrl; download.setAttribute("download", source.name);
  const open = box.querySelector<HTMLButtonElement>(".doc-preview-open")!;
  open.hidden = !source.openInApp;
  open.onclick = () => { void source.openInApp?.().catch(error => deps.message(error instanceof Error ? error.message : String(error), true)); };
  docViewer.loading(body, make);
  if (!box.open) box.showModal();
  try {
    const data = await deps.api<Record<string, unknown>>(source.viewUrl);
    const kind = typeof data.kind === "string" ? data.kind : "";
    box.querySelector(".doc-preview-kind")!.textContent = LABELS[kind] ?? "File";
    const media = { media: source.mediaUrl };
    if (kind === "pdf") docViewer.pdf(body, make, source.name, source.rawUrl);
    else if (kind === "image") docViewer.image(body, make, source.rawUrl, source.name);
    else if (kind === "docx") docViewer.word(body, make, data, media);
    else if (kind === "pptx") docViewer.slides(body, make, data, media);
    else if (kind === "xlsx" || kind === "table") docViewer.grid(body, make, data);
    else if (kind === "text") docViewer.text(body, make, data);
    else docViewer.error(body, make, "This file has no preview. Download it to open it.");
  } catch (error) {
    docViewer.error(body, make, error instanceof Error ? error.message : "This file could not be previewed. Download it to open it.");
  }
}
