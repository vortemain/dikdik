/** Themed, asynchronous dialogs. Cancellation never authorizes an action. */
export interface DialogField {
  name: string; label: string; value?: string; placeholder?: string; help?: string;
  type?: "text" | "url" | "password" | "textarea" | "select";
  required?: boolean; readonly?: boolean;
  options?: { value: string; label: string }[];
  showWhen?: (values: Record<string, string>) => boolean;
}
export interface DialogOptions {
  title: string; description?: string; fields?: DialogField[]; submit?: string; cancel?: string;
  danger?: boolean; copyValue?: string;
  validate?: (values: Record<string, string>) => string | null;
}
let serial = 0;
export function validateDialogFields(fields: DialogField[], values: Record<string, string>): string | null {
  for (const field of fields) {
    if (field.showWhen && !field.showWhen(values)) continue;
    const value = values[field.name] ?? "";
    if (field.required && !value.trim()) return `${field.label} is required.`;
    if (field.type === "url" && value) {
      try { if (!["http:", "https:"].includes(new URL(value).protocol)) return `${field.label} must start with http:// or https://.`; }
      catch { return `Enter a valid ${field.label.toLowerCase()}.`; }
    }
    if (field.type === "select" && !field.options?.some(option => option.value === value)) return `Choose ${field.label.toLowerCase()}.`;
  }
  return null;
}
export function formDialog(options: DialogOptions): Promise<Record<string, string> | null> {
  const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = "") => {
    const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
  };
  const id = `app-dialog-${++serial}`, opener = document.activeElement as HTMLElement | null;
  const dialog = make("dialog", "app-dialog"); dialog.setAttribute("aria-labelledby", id + "-title");
  const form = make("form", "app-dialog-form"), head = make("header", "app-dialog-head");
  form.noValidate = true;
  const mark = make("img", "dialog-brand"); mark.src = "/icon.svg"; mark.alt = "";
  const title = make("h2", "", options.title); title.id = id + "-title";
  const close = make("button", "ghost dialog-dismiss", "×"); close.type = "button"; close.setAttribute("aria-label", "Close dialog");
  head.append(mark, title, close);
  const body = make("div", "app-dialog-body");
  if (options.description) {
    const description = make("p", "app-dialog-description", options.description); description.id = id + "-description";
    dialog.setAttribute("aria-describedby", description.id); body.append(description);
  }
  const fields = new Map<string, { field: DialogField; wrap: HTMLElement; input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement }>();
  for (const field of options.fields ?? []) {
    const wrap = make("div", "dialog-field"), label = make("label", "", field.label);
    const input = field.type === "textarea" ? make("textarea") : field.type === "select" ? make("select") : make("input");
    input.id = id + "-" + field.name; label.htmlFor = input.id;
    if (input instanceof HTMLInputElement) { input.type = field.type === "url" ? "url" : field.type === "password" ? "password" : "text"; input.autocomplete = "off"; }
    if (input instanceof HTMLSelectElement) for (const option of field.options ?? []) {
      const choice = make("option", "", option.label); choice.value = option.value; input.append(choice);
    }
    if (!(input instanceof HTMLSelectElement)) { input.placeholder = field.placeholder ?? ""; input.readOnly = !!field.readonly; input.spellcheck = false; }
    input.value = field.value ?? ""; wrap.append(label, input);
    if (field.help) { const help = make("small", "", field.help); help.id = input.id + "-help"; input.setAttribute("aria-describedby", help.id); wrap.append(help); }
    fields.set(field.name, { field, wrap, input }); body.append(wrap);
  }
  const values = () => Object.fromEntries([...fields].filter(([, entry]) => !entry.wrap.hidden).map(([name, entry]) => [name, entry.input.value]));
  const sync = () => { const current = values(); for (const { field, wrap, input } of fields.values()) { wrap.hidden = field.showWhen ? !field.showWhen(current) : false; input.disabled = wrap.hidden; input.required = !wrap.hidden && !!field.required; } };
  form.addEventListener("input", sync); form.addEventListener("change", sync); sync();
  const error = make("p", "dialog-error"); error.hidden = true; error.setAttribute("role", "alert"); body.append(error);
  const footer = make("footer", "app-dialog-actions");
  if (options.copyValue !== undefined) {
    const copy = make("button", "secondary small dialog-copy", "Copy key"); copy.type = "button";
    copy.onclick = async () => { try { await navigator.clipboard.writeText(options.copyValue!); copy.textContent = "Copied"; } catch { error.hidden = false; error.textContent = "Select the key and copy it with your keyboard."; } }; footer.append(copy);
  }
  const cancel = make("button", "secondary small", options.cancel ?? "Cancel"); cancel.type = "button";
  const submit = make("button", options.danger ? "danger small" : "primary small", options.submit ?? "Save"); submit.type = "submit";
  footer.append(cancel, submit); form.append(head, body, footer); dialog.append(form); document.body.append(dialog);
  return new Promise(resolve => {
    let settled = false;
    const finish = (result: Record<string, string> | null) => {
      if (settled) return; settled = true;
      for (const entry of fields.values()) entry.input.value = "";
      dialog.close(); dialog.remove(); if (opener?.isConnected) opener.focus(); resolve(result);
    };
    close.onclick = cancel.onclick = () => finish(null);
    dialog.addEventListener("cancel", event => { event.preventDefault(); finish(null); });
    dialog.addEventListener("close", () => finish(null));
    form.onsubmit = event => {
      event.preventDefault(); const result = values();
      const problem = validateDialogFields(options.fields ?? [], result) ?? options.validate?.(result);
      if (problem) { error.hidden = false; error.textContent = problem; return; }
      finish(result);
    };
    dialog.showModal();
    const first = [...fields.values()].find(entry => !entry.wrap.hidden && !entry.field.readonly)?.input;
    (first ?? cancel).focus();
  });
}
export async function confirmDialog(title: string, description = "", submit = "Continue", danger = false): Promise<boolean> {
  return await formDialog({ title, description, submit, danger }) !== null;
}
export async function inputDialog(title: string, label: string, value = ""): Promise<string | null> {
  const result = await formDialog({ title, fields: [{ name: "value", label, value }], submit: "Continue" });
  return result?.value ?? null;
}
export async function showSecret(title: string, value: string): Promise<void> {
  await formDialog({ title, description: "Copy this key now. It is shown only once.", fields: [{ name: "key", label: "API key", value, readonly: true, type: "textarea" }], copyValue: value, submit: "Done", cancel: "Close" });
}
