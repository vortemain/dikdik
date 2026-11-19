// Board styles for everything Dikdik draws: diagrams, function plots and sketches.
//
//   chalkboard  a green school board, chalk-white and pastel strokes, handwritten labels
//   whiteboard  a white board with marker colours and handwritten labels
//   clean       crisp lines and a plain font, for reports and theses
//
// Drawings are SVG shown as images (Dikdik's Content-Security-Policy forbids inline styles in
// the page; inside an image the SVG's own styles and animations work and nothing can run).
// The handwriting font (Caveat, SIL OFL) is embedded into each SVG so labels look the same
// everywhere, and the first time a drawing appears its strokes are drawn in like a teacher's.

export type BoardStyle = "chalkboard" | "whiteboard" | "clean";
export const BOARD_STYLES: BoardStyle[] = ["chalkboard", "whiteboard", "clean"];
const LABELS: Record<BoardStyle, string> = { chalkboard: "Chalkboard", whiteboard: "Whiteboard", clean: "Clean" };
const STORAGE = "dikdik.board.style";

export interface Palette {
  board: string; ink: string; muted: string; grid: string; accents: string[]; font: string; fill: string; label: string;
}
const HAND = '"Caveat", "Segoe Print", "Bradley Hand", "Comic Sans MS", cursive';
const PLAIN = '"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';

export function palette(style: BoardStyle, dark: boolean): Palette {
  if (style === "chalkboard") {
    return { board: "#2c4a3c", ink: "#f4f1e6", muted: "#c8d3c9", grid: "rgba(244,241,230,0.10)", fill: "rgba(244,241,230,0.08)",
      accents: ["#fff3a3", "#9fd8ff", "#ffb3b3", "#b7f0b1", "#f8c6ff", "#ffd59e"], font: HAND, label: "#f4f1e6" };
  }
  if (style === "whiteboard") {
    return { board: "#fdfdfb", ink: "#1f3a8a", muted: "#5b6478", grid: "rgba(31,58,138,0.08)", fill: "rgba(31,58,138,0.05)",
      accents: ["#c0262d", "#1f7a3a", "#1f3a8a", "#8a3ffc", "#d97706", "#0e7490"], font: HAND, label: "#1f2937" };
  }
  return dark
    ? { board: "#1e1e1d", ink: "#ececea", muted: "#a3a39f", grid: "rgba(236,236,234,0.08)", fill: "rgba(124,194,155,0.12)",
      accents: ["#7cc29b", "#8fb2ee", "#f2a65a", "#e98a9a", "#c3a6f5", "#e8d06b"], font: PLAIN, label: "#ececea" }
    : { board: "#ffffff", ink: "#1d1d1f", muted: "#5f6368", grid: "rgba(0,0,0,0.06)", fill: "rgba(47,107,79,0.08)",
      accents: ["#2f6b4f", "#2b579a", "#c2410c", "#b91c1c", "#7c3aed", "#a16207"], font: PLAIN, label: "#1d1d1f" };
}

/** Tell drawings the light/dark theme changed (Settings → Appearance, or the system theme). */
export function announceTheme(): void {
  document.dispatchEvent?.(new Event("dikdik-theme"));
}
try {
  globalThis.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", announceTheme);
} catch { /* no media queries (tests) */ }

export function isDark(): boolean {
  const root = document.documentElement;
  if (root.dataset.theme === "dark") return true;
  if (root.dataset.theme === "light") return false;
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

/** The style chosen in Settings (or with the switch on any drawing); chalkboard by default. */
// A shared link can pick the board (?board=whiteboard). Read once: the app later rewrites the address.
const LINKED = (() => {
  try { return new URLSearchParams(globalThis.location?.search ?? "").get("board"); } catch { return null; }
})();

export function boardStyle(): BoardStyle {
  if (LINKED === "chalkboard" || LINKED === "whiteboard" || LINKED === "clean") return LINKED;
  try {
    const saved = localStorage.getItem(STORAGE);
    if (saved === "chalkboard" || saved === "whiteboard" || saved === "clean") return saved;
  } catch { void 0; }
  return "chalkboard";
}
export function setBoardStyle(style: BoardStyle): void {
  try { localStorage.setItem(STORAGE, style); } catch { void 0; }
  document.dispatchEvent?.(new CustomEvent("dikdik-board-style", { detail: style }));
}
export function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// --- the handwriting font, embedded into each drawing ----------------------------------

let fontData: string | null = null;
let fontLoading: Promise<string> | null = null;
export function handwritingFont(): Promise<string> {
  if (fontData !== null) return Promise.resolve(fontData);
  fontLoading ??= fetch("/fonts/caveat-latin-600-normal.woff2", { signal: AbortSignal.timeout(15000) })
    .then(response => response.ok ? response.arrayBuffer() : Promise.reject(new Error("font")))
    .then(buffer => {
      let binary = "";
      const bytes = new Uint8Array(buffer);
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      fontData = `data:font/woff2;base64,${btoa(binary)}`;
      return fontData;
    })
    .catch(() => { fontData = ""; return ""; });
  return fontLoading;
}

/** Add the handwriting font and (optionally) the drawing-in animation to an SVG document string. */
export function finishSvg(svg: string, style: BoardStyle, font: string, animate: boolean): string {
  const css: string[] = [];
  if (style !== "clean" && font) css.push(`@font-face{font-family:"Caveat";src:url(${font}) format("woff2");font-weight:400 700;}`);
  if (animate && !reducedMotion()) {
    let index = 0;
    svg = svg.replace(/<(?:path|line|polyline|polygon|rect|circle|ellipse)\b[^>]*>/g, (tag: string) => {
      if (/\bdata-static\b/.test(tag)) return tag;  // backgrounds and grids appear at once
      const order = index++;
      return /\sstyle="/.test(tag) ? tag.replace(/\sstyle="/, ` style="--i:${order};`) : tag.replace(/^<(\w+)/, `<$1 style="--i:${order}"`);
    });
    const total = Math.max(1, index);
    const step = Math.min(70, 1800 / total);
    // Fail-safe: nothing is hidden by default. Only the animation's first frame (applied during
    // its delay through fill-mode "backwards") hides a stroke, so if animations do not run at all
    // (some viewers, printing, exports) the finished drawing is simply shown.
    css.push(`path,line,polyline,polygon,rect,circle,ellipse{stroke-dasharray:9999;` +
      `animation:dk-draw 1.1s ease-out backwards;animation-delay:calc(var(--i,0) * ${step.toFixed(1)}ms);}` +
      `text{animation:dk-fade .5s ease-out backwards;animation-delay:${Math.round(step * total * 0.6 + 300)}ms;}` +
      `@keyframes dk-draw{from{stroke-dashoffset:9999;}to{stroke-dashoffset:0;}}@keyframes dk-fade{from{opacity:0;}to{opacity:1;}}`);
  }
  if (!css.length) return svg;
  const block = `<style>${css.join("")}</style>`;
  return svg.replace(/(<svg\b[^>]*>)/, `$1${block}`);
}

export function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** A chalk-dust texture for the chalkboard card behind the drawing. */
export function boardBackground(style: BoardStyle): string {
  return style === "chalkboard" ? "board-chalk" : style === "whiteboard" ? "board-white" : "board-clean";
}

// --- the card around a drawing ---------------------------------------------------------

function action(label: string, title: string, onclick: () => void): HTMLButtonElement {
  const control = document.createElement("button");
  control.type = "button";
  control.className = "diagram-action";
  control.textContent = label;
  control.title = title;
  control.onclick = onclick;
  return control;
}

function downloadPng(svg: string, width: number, height: number, background: string, name: string): void {
  const image = new Image();
  image.onload = () => {
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = width * scale; canvas.height = height * scale;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.fillStyle = background;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.scale(scale, scale);
    context.drawImage(image, 0, 0, width, height);
    const link = document.createElement("a");
    link.href = canvas.toDataURL("image/png");
    link.download = `${name}.png`;
    link.click();
  };
  image.src = svgUrl(svg);
}

export function openFull(svg: string, width: number, height: number, title: string, boardClass: string): void {
  const dialog = document.createElement("dialog");
  dialog.className = "diagram-full";
  const bar = document.createElement("div");
  bar.className = "diagram-full-bar";
  const name = document.createElement("strong");
  name.textContent = title || "Drawing";
  let zoom = 1;
  const image = document.createElement("img");
  image.src = svgUrl(svg);
  image.alt = title || "Drawing";
  const apply = () => { image.style.width = `${Math.round(width * zoom)}px`; image.style.height = `${Math.round(height * zoom)}px`; };
  const fit = () => {
    const room = Math.max(200, (dialog.clientWidth || innerWidth) - 48), tall = Math.max(200, (dialog.clientHeight || innerHeight) - 96);
    zoom = Math.min(4, room / width, tall / height);
    apply();
  };
  bar.append(name,
    action("−", "Zoom out", () => { zoom = Math.max(0.2, zoom / 1.25); apply(); }),
    action("Fit", "Fit to screen", fit),
    action("+", "Zoom in", () => { zoom = Math.min(8, zoom * 1.25); apply(); }),
    action("Close", "Close full screen", () => dialog.close()));
  const stage = document.createElement("div");
  stage.className = `diagram-full-stage ${boardClass}`;
  stage.append(image);
  dialog.append(bar, stage);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  document.body.append(dialog);
  dialog.showModal();
  fit();
}

export interface Drawn { svg: string; width: number; height: number }

/**
 * Fill `figure` with a drawing card. `draw(style, animate)` returns the finished SVG; the card
 * re-draws when the user switches style, and Replay draws it in again.
 */
export function drawingCard(figure: HTMLElement, options: {
  title: string; source: string; sourceLabel: string; name: string;
  draw: (style: BoardStyle, animate: boolean) => Promise<Drawn>;
  onError: (error: unknown) => void;
  animateFirst: boolean;
}): void {
  let style = boardStyle();
  const paint = (animate: boolean) => {
    options.draw(style, animate).then(result => {
      const theme = palette(style, isDark());
      const boardClass = boardBackground(style);
      figure.className = `diagram ${boardClass}`;
      const image = document.createElement("img");
      image.className = "diagram-image";
      image.src = svgUrl(result.svg);
      image.alt = options.title || "Drawing";
      image.width = result.width;
      image.height = result.height;
      image.title = "Open full screen";
      // Drawings fit the card; only very wide ones (shrunk below ~62%) scroll sideways to stay readable.
      image.style.minWidth = `${Math.min(result.width, Math.round(result.width * 0.62))}px`;
      image.onclick = () => openFull(result.svg, result.width, result.height, options.title, boardClass);
      const canvas = document.createElement("div");
      canvas.className = "diagram-canvas";
      canvas.append(image);
      const tools = document.createElement("div");
      tools.className = "diagram-tools";
      tools.setAttribute("data-nocopy", "");
      const styles = document.createElement("div");
      styles.className = "board-switch";
      styles.setAttribute("role", "group");
      styles.setAttribute("aria-label", "Board style");
      for (const option of BOARD_STYLES) {
        const choice = action(LABELS[option], `Draw on a ${LABELS[option].toLowerCase()} (this becomes your default)`, () => {
          if (option === style) return;
          style = option;
          setBoardStyle(option);
          paint(true);
        });
        choice.setAttribute("aria-pressed", String(option === style));
        styles.append(choice);
      }
      const copy = action(options.sourceLabel, "Copy the source of this drawing", () => {
        void navigator.clipboard?.writeText(options.source);
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = options.sourceLabel; }, 1200);
      });
      const svgLink = document.createElement("a");
      svgLink.className = "diagram-action";
      svgLink.textContent = "SVG";
      svgLink.title = "Download as SVG";
      svgLink.href = svgUrl(result.svg);
      svgLink.download = `${options.name}.svg`;
      tools.append(styles,
        action("Replay", "Draw it again, stroke by stroke", () => paint(true)),
        copy, svgLink,
        action("PNG", "Download as PNG", () => downloadPng(result.svg, result.width, result.height, theme.board, options.name)),
        action("Full screen", "Open full screen with zoom", () => openFull(result.svg, result.width, result.height, options.title, boardClass)));
      figure.replaceChildren(canvas, tools);
    }).catch(options.onError);
  };
  // Another drawing switched the default style: follow it, without animating.
  const follow = (event: Event) => {
    if (!figure.isConnected) { document.removeEventListener("dikdik-board-style", follow); return; }
    const next = (event as CustomEvent<BoardStyle>).detail;
    if (next !== style) { style = next; paint(false); }
  };
  document.addEventListener?.("dikdik-board-style", follow);
  // Clean drawings take the app's light or dark colours: redraw them when the theme changes,
  // so text never ends up light-on-light or dark-on-dark. Boards keep their own colours.
  const retheme = () => {
    if (!figure.isConnected) { document.removeEventListener("dikdik-theme", retheme); return; }
    if (style === "clean") paint(false);
  };
  document.addEventListener?.("dikdik-theme", retheme);
  paint(options.animateFirst);
}
