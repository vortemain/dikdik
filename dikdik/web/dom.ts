// Small DOM and storage helpers shared across the app. Pure utilities —
// no app state, no side effects beyond the DOM/localStorage they touch.
// Re-exported by app.ts so existing call sites are unchanged.

export function make<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function icon(name: string): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "i"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function setClass(node: HTMLElement, name: string, on: boolean): void {
  const names = new Set((node.className || "").split(/\s+/).filter(Boolean));
  if (on) names.add(name); else names.delete(name);
  node.className = [...names].join(" ");
}

export function storageGet(key: string): string | null {
  try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; }
}

export function storageSet(key: string, value: string): void {
  try { globalThis.localStorage?.setItem(key, value); } catch { /* private mode: preference just isn't remembered */ }
}

export function wide(query: string): boolean {
  return typeof globalThis.matchMedia === "function" && globalThis.matchMedia(query).matches;
}

export function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- copy to clipboard ----------------------------------------------------
export async function copyText(text: string, control: HTMLButtonElement): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = make("textarea"); area.value = text; area.className = "sr-only";
    document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
  }
  const label = control.querySelector("span");
  control.replaceChildren(icon("check"), make("span", "", "Copied"));
  setClass(control, "copied", true);
  setTimeout(() => { control.replaceChildren(icon("copy"), label ?? make("span", "", "Copy")); setClass(control, "copied", false); }, 1600);
}
export function copyButton(getText: () => string, label = "Copy"): HTMLButtonElement {
  const control = make("button", "copy");
  control.type = "button";
  control.setAttribute("aria-label", "Copy to clipboard");
  control.append(icon("copy"), make("span", "", label));
  control.onclick = () => { void copyText(getText(), control); };
  return control;
}