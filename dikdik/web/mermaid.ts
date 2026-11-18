// Diagrams in replies, drawn by the real Mermaid library (bundled; loaded on first use).
//
// Every Mermaid diagram type works: flowcharts, sequence, class, state, entity-relationship,
// mind maps, timelines, Gantt, pie, quadrant, XY charts, user journeys, Git graphs, Sankey and
// block diagrams. Mermaid runs with securityLevel "strict" (no HTML labels, no click scripts).
// The finished SVG is shown as an image: that keeps Mermaid's own styles working under
// Dikdik's strict Content-Security-Policy and isolates the diagram from the page. Each
// diagram gets Copy source, Download (SVG/PNG) and a full-screen view with zoom.

type MermaidApi = {
  initialize(config: Record<string, unknown>): void;
  render(id: string, text: string): Promise<{ svg: string }>;
  parse(text: string, options?: { suppressErrors?: boolean }): Promise<unknown>;
};

declare global { interface Window { mermaid?: MermaidApi } }

const SCRIPT = "/assets/vendor/mermaid.min.js";
// System fonts: the page measures labels and the image draws them, so both must use the same font.
const FONT = '"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';
let loading: Promise<MermaidApi> | null = null;
let queue: Promise<unknown> = Promise.resolve();
let counter = 0;
let configuredTheme = "";
const cache = new Map<string, { svg: string; width: number; height: number } | { error: string }>();

function isDark(): boolean {
  const root = document.documentElement;
  if (root.dataset.theme === "dark") return true;
  if (root.dataset.theme === "light") return false;
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

function load(): Promise<MermaidApi> {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  loading ??= new Promise<MermaidApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = () => window.mermaid ? resolve(window.mermaid) : reject(new Error("The diagram engine did not start"));
    script.onerror = () => { loading = null; reject(new Error("The diagram engine could not be loaded")); };
    document.head.append(script);
  });
  return loading;
}

function configure(mermaid: MermaidApi, dark: boolean): void {
  const theme = dark ? "dark" : "light";
  if (configuredTheme === theme) return;
  configuredTheme = theme;
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", htmlLabels: false, theme: dark ? "dark" : "base",
    fontFamily: FONT, fontSize: 15, deterministicIds: true,
    flowchart: { htmlLabels: false, curve: "basis", padding: 14, nodeSpacing: 46, rankSpacing: 54, useMaxWidth: false },
    sequence: { useMaxWidth: false, mirrorActors: false, actorMargin: 60, noteMargin: 12 },
    class: { htmlLabels: false, useMaxWidth: false }, state: { useMaxWidth: false }, er: { useMaxWidth: false },
    gantt: { useMaxWidth: false }, journey: { useMaxWidth: false }, timeline: { useMaxWidth: false },
    mindmap: { useMaxWidth: false, padding: 14 }, pie: { useMaxWidth: false }, quadrantChart: { useMaxWidth: false },
    xyChart: { useMaxWidth: false }, sankey: { useMaxWidth: false },
    themeVariables: dark ? {
      fontFamily: FONT, background: "#1e1e1d", primaryColor: "#1f3329", primaryTextColor: "#ececea", primaryBorderColor: "#7cc29b",
      secondaryColor: "#2a2f3a", tertiaryColor: "#332b22", lineColor: "#a3a39f", textColor: "#ececea", noteBkgColor: "#3a3420",
      noteTextColor: "#f3eed9", edgeLabelBackground: "#262625", clusterBkg: "#232322", clusterBorder: "#4a4a46",
    } : {
      fontFamily: FONT, background: "#ffffff", primaryColor: "#e6f0ea", primaryTextColor: "#1d1d1f", primaryBorderColor: "#2f6b4f",
      secondaryColor: "#e8eefb", tertiaryColor: "#fff4e0", lineColor: "#5f6368", textColor: "#1d1d1f", noteBkgColor: "#fff8db",
      noteTextColor: "#3d3a2a", edgeLabelBackground: "#ffffff", clusterBkg: "#f7f7f5", clusterBorder: "#d6d6d1",
    },
  });
}

/** Tidy what models commonly get wrong, without changing a valid diagram. */
export function normaliseSource(source: string): string {
  let text = source.replace(/\r\n?/g, "\n").replace(/^\s*```(?:mermaid)?\s*$/gm, "").trim();
  // "graph" without a direction is valid; "flowchart" alone is not.
  text = text.replace(/^flowchart\s*$/m, "flowchart TD");
  // Smart quotes and long dashes break the parser.
  text = text.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/–|—/g, "-");
  return text;
}

/** Second attempt for flowcharts: quote node labels that contain characters Mermaid treats as syntax. */
export function quoteLabels(source: string): string {
  if (!/^\s*(?:flowchart|graph)\b/m.test(source)) return source;
  return source
    .replace(/(\b[\w-]+)\[(?![(\[/\\"])([^\]"\n]*[(){}:;,#&<>|=+*%][^\]"\n]*)\](?!\))/g, (_all, id: string, label: string) => `${id}["${label.trim()}"]`)
    .replace(/(\b[\w-]+)\{(?!\{)([^}"\n]*[()[\]:;,#&<>|=+*%][^}"\n]*)\}(?!\})/g, (_all, id: string, label: string) => `${id}{"${label.trim()}"}`)
    .replace(/\|([^|"\n]*[()[\]{}:;#&<>][^|"\n]*)\|/g, (_all, label: string) => `|"${label.trim()}"|`);
}

function size(svg: string): { width: number; height: number } {
  const box = /viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"/.exec(svg);
  const width = box ? Number(box[1]) : 600, height = box ? Number(box[2]) : 400;
  return { width: Math.ceil(width), height: Math.ceil(height) };
}

async function draw(source: string, dark: boolean): Promise<{ svg: string; width: number; height: number }> {
  const mermaid = await load();
  const run = queue.then(async () => {
    configure(mermaid, dark);
    const id = `dikdik-diagram-${++counter}`;
    try {
      const { svg } = await mermaid.render(id, source);
      const dims = size(svg);
      // A fixed pixel size keeps the image crisp; the page scales it down to fit.
      const sized = svg.replace(/<svg\b[^>]*>/, tag =>
        tag.replace(/\s(?:width|height|style)="[^"]*"/g, "").replace(/^<svg/, `<svg width="${dims.width}" height="${dims.height}"`));
      return { svg: sized, ...dims };
    } finally {
      document.getElementById(id)?.remove();
      document.getElementById(`d${id}`)?.remove();
    }
  });
  queue = run.catch(() => undefined);
  return run;
}

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function button(label: string, title: string, onclick: () => void): HTMLButtonElement {
  const control = document.createElement("button");
  control.type = "button";
  control.className = "diagram-action";
  control.textContent = label;
  control.title = title;
  control.onclick = onclick;
  return control;
}

function downloadPng(svg: string, width: number, height: number): void {
  const image = new Image();
  image.onload = () => {
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = width * scale; canvas.height = height * scale;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.fillStyle = isDark() ? "#1e1e1d" : "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.scale(scale, scale);
    context.drawImage(image, 0, 0, width, height);
    const link = document.createElement("a");
    link.href = canvas.toDataURL("image/png");
    link.download = "diagram.png";
    link.click();
  };
  image.src = svgUrl(svg);
}

function openFull(svg: string, width: number, height: number, title: string): void {
  const dialog = document.createElement("dialog");
  dialog.className = "diagram-full";
  const bar = document.createElement("div");
  bar.className = "diagram-full-bar";
  const name = document.createElement("strong");
  name.textContent = title || "Diagram";
  let zoom = 1;
  const image = document.createElement("img");
  image.src = svgUrl(svg);
  image.alt = title || "Diagram";
  const apply = () => { image.style.width = `${Math.round(width * zoom)}px`; image.style.height = `${Math.round(height * zoom)}px`; };
  const fit = () => {
    const room = Math.max(200, (dialog.clientWidth || innerWidth) - 48), tall = Math.max(200, (dialog.clientHeight || innerHeight) - 96);
    zoom = Math.min(3, room / width, tall / height);
    apply();
  };
  bar.append(name,
    button("−", "Zoom out", () => { zoom = Math.max(0.2, zoom / 1.25); apply(); }),
    button("Fit", "Fit to screen", fit),
    button("+", "Zoom in", () => { zoom = Math.min(6, zoom * 1.25); apply(); }),
    button("Close", "Close full screen", () => dialog.close()));
  const stage = document.createElement("div");
  stage.className = "diagram-full-stage";
  stage.append(image);
  dialog.append(bar, stage);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  document.body.append(dialog);
  dialog.showModal();
  fit();
}

/** A diagram card for one ```mermaid block. Renders immediately from cache, else when ready. */
export function renderMermaid(source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement {
  const figure = document.createElement("figure");
  figure.className = "diagram";
  const text = normaliseSource(source);
  if (typeof document.createElementNS !== "function" || typeof window === "undefined") {
    figure.append(fallback(source, "mermaid"));
    return figure;
  }
  const dark = isDark();
  const key = `${dark ? "d" : "l"}:${text}`;
  const title = (/^\s*(?:---\s*\n\s*title:\s*(.+)|title\s+(.+))$/m.exec(text)?.slice(1).find(Boolean) ?? "").trim().replace(/^["']|["']$/g, "");
  const show = (result: { svg: string; width: number; height: number } | { error: string }) => {
    if ("error" in result) {
      const note = document.createElement("p");
      note.className = "diagram-error";
      note.textContent = `This diagram could not be drawn (${result.error}). Its source is shown instead.`;
      figure.replaceChildren(note, fallback(source, "mermaid"));
      return;
    }
    const image = document.createElement("img");
    image.className = "diagram-image";
    image.src = svgUrl(result.svg);
    image.alt = title || "Diagram";
    image.width = result.width;
    image.height = result.height;
    image.title = "Open full screen";
    image.onclick = () => openFull(result.svg, result.width, result.height, title);
    const scroller = document.createElement("div");
    scroller.className = "diagram-canvas";
    scroller.append(image);
    const tools = document.createElement("div");
    tools.className = "diagram-tools";
    const copy = button("Copy source", "Copy the Mermaid source", () => {
      void navigator.clipboard?.writeText(text);
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy source"; }, 1200);
    });
    const svgLink = document.createElement("a");
    svgLink.className = "diagram-action";
    svgLink.textContent = "SVG";
    svgLink.title = "Download as SVG";
    svgLink.href = svgUrl(result.svg);
    svgLink.download = "diagram.svg";
    tools.append(copy, svgLink, button("PNG", "Download as PNG", () => downloadPng(result.svg, result.width, result.height)),
      button("Full screen", "Open full screen with zoom", () => openFull(result.svg, result.width, result.height, title)));
    figure.replaceChildren(scroller, tools);
  };
  const cached = cache.get(key);
  if (cached) { show(cached); return figure; }
  const placeholder = document.createElement("div");
  placeholder.className = "diagram-loading";
  placeholder.textContent = "Drawing diagram…";
  figure.append(placeholder);
  draw(text, dark).catch((error: unknown) => {
    const fixed = quoteLabels(text);
    if (fixed === text) throw error;
    return draw(fixed, dark);  // a common model slip: unquoted labels with brackets or punctuation
  }).then(result => {
    cache.set(key, result);
    if (cache.size > 200) cache.delete(cache.keys().next().value!);
    show(result);
  }).catch((error: unknown) => {
    const message = error instanceof Error ? error.message.split("\n")[0]!.slice(0, 160) : "unknown error";
    const result = { error: message };
    if (!/could not be loaded/.test(message)) cache.set(key, result);
    show(result);
  });
  return figure;
}
