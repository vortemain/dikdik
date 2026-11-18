// Diagrams in replies, drawn by the real Mermaid library (bundled; loaded on first use).
//
// Every Mermaid diagram type works: flowcharts, sequence, class, state, entity-relationship,
// mind maps, timelines, Gantt, pie, quadrant, XY charts, user journeys, Git graphs, Sankey and
// block diagrams. Mermaid runs with securityLevel "strict" (no HTML labels, no click scripts).
// On the chalkboard and whiteboard styles it uses Mermaid's hand-drawn look and the bundled
// handwriting font, and the diagram is drawn in stroke by stroke the first time it appears.
// The card (style switch, replay, copy, SVG/PNG, full screen) comes from ./board.

import { type BoardStyle, drawingCard, finishSvg, handwritingFont, isDark, palette } from "./board";

type MermaidApi = {
  initialize(config: Record<string, unknown>): void;
  render(id: string, text: string): Promise<{ svg: string }>;
};

declare global { interface Window { mermaid?: MermaidApi } }

const SCRIPT = "/assets/vendor/mermaid.min.js";
let loading: Promise<MermaidApi> | null = null;
let queue: Promise<unknown> = Promise.resolve();
let counter = 0;
let configured = "";
const cache = new Map<string, { svg: string; width: number; height: number }>();
const failures = new Map<string, string>();
const shown = new Set<string>();

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

function configure(mermaid: MermaidApi, style: BoardStyle, dark: boolean): void {
  const key = `${style}:${dark}`;
  if (configured === key) return;
  configured = key;
  const ink = palette(style, dark);
  const hand = style !== "clean";
  const pies = Object.fromEntries(ink.accents.concat(ink.accents).map((colour, index) => [`pie${index + 1}`, colour]));
  // Timelines, mind maps and journeys colour their sections from cScale*; ER tables from attributeBackground*.
  const scale: Record<string, string> = {};
  const sections = style === "chalkboard" ? ["#35574a", "#3d5f52", "#45675a", "#2f5244", "#4a6b5e", "#3a5b4e"]
    : style === "whiteboard" ? ["#e8eefb", "#fef3c7", "#dcfce7", "#fde2e4", "#ede9fe", "#e0f2fe"] : [];
  sections.concat(sections).forEach((colour, index) => {
    scale[`cScale${index}`] = colour;
    scale[`cScaleLabel${index}`] = style === "chalkboard" ? ink.ink : ink.label;
    scale[`cScalePeer${index}`] = style === "chalkboard" ? ink.ink : ink.ink;
  });
  if (sections.length) {
    scale.attributeBackgroundColorOdd = style === "chalkboard" ? "#35574a" : "#ffffff";
    scale.attributeBackgroundColorEven = style === "chalkboard" ? "#2f5244" : "#f3f6fc";
    scale.rowOdd = scale.attributeBackgroundColorOdd;
    scale.rowEven = scale.attributeBackgroundColorEven;
    scale.relationLabelBackground = ink.board;
    scale.relationLabelColor = ink.board;  // the hand-drawn look boxes relationship labels in ink, so write on it in board colour
  }
  const variables: Record<string, string> = style === "chalkboard" ? {
    background: ink.board, primaryColor: "#46695a", primaryTextColor: ink.ink, primaryBorderColor: ink.ink, lineColor: ink.ink,
    secondaryColor: "#35574a", secondaryTextColor: ink.ink, secondaryBorderColor: "#fff3a3", tertiaryColor: "#3b5e50",
    tertiaryTextColor: ink.ink, tertiaryBorderColor: "#9fd8ff", textColor: ink.ink, noteBkgColor: "#3b5e50",
    noteTextColor: "#fff3a3", noteBorderColor: "#fff3a3", edgeLabelBackground: ink.board, clusterBkg: "#31513f",
    clusterBorder: ink.muted, titleColor: ink.ink, actorBkg: ink.board, actorBorder: ink.ink, actorTextColor: ink.ink,
    actorLineColor: ink.muted, signalColor: ink.ink, signalTextColor: ink.ink, labelBoxBkgColor: ink.board,
    labelTextColor: ink.ink, loopTextColor: ink.ink, sequenceNumberColor: ink.board, pieTitleTextColor: ink.ink,
    pieSectionTextColor: "#1d2b24", pieLegendTextColor: ink.ink, pieStrokeColor: ink.ink, pieOuterStrokeColor: ink.ink,
    git0: "#fff3a3", git1: "#9fd8ff", git2: "#ffb3b3", git3: "#b7f0b1",
  } : style === "whiteboard" ? {
    background: ink.board, primaryColor: "#ffffff", primaryTextColor: ink.label, primaryBorderColor: ink.ink, lineColor: ink.ink,
    secondaryColor: "#fff1f2", secondaryTextColor: ink.label, secondaryBorderColor: "#c0262d", tertiaryColor: "#f0fdf4",
    tertiaryTextColor: ink.label, tertiaryBorderColor: "#1f7a3a", textColor: ink.label, noteBkgColor: "#fff7cc",
    noteTextColor: "#3d3a2a", noteBorderColor: "#d97706", edgeLabelBackground: ink.board, clusterBkg: "#f8fafc",
    clusterBorder: "#94a3b8", titleColor: ink.ink, actorBkg: "#ffffff", actorBorder: ink.ink, actorTextColor: ink.label,
    actorLineColor: "#94a3b8", signalColor: ink.ink, signalTextColor: ink.label, pieTitleTextColor: ink.label,
    pieSectionTextColor: "#ffffff", pieStrokeColor: "#ffffff",
  } : dark ? {
    background: ink.board, primaryColor: "#1f3329", primaryTextColor: ink.ink, primaryBorderColor: "#7cc29b", lineColor: ink.muted,
    secondaryColor: "#2a2f3a", tertiaryColor: "#332b22", textColor: ink.ink, noteBkgColor: "#3a3420", noteTextColor: "#f3eed9",
    edgeLabelBackground: "#262625", clusterBkg: "#232322", clusterBorder: "#4a4a46",
  } : {
    background: ink.board, primaryColor: "#e6f0ea", primaryTextColor: ink.ink, primaryBorderColor: "#2f6b4f", lineColor: ink.muted,
    secondaryColor: "#e8eefb", tertiaryColor: "#fff4e0", textColor: ink.ink, noteBkgColor: "#fff8db", noteTextColor: "#3d3a2a",
    edgeLabelBackground: "#ffffff", clusterBkg: "#f7f7f5", clusterBorder: "#d6d6d1",
  };
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", htmlLabels: false, theme: style === "clean" && dark ? "dark" : "base",
    look: hand ? "handDrawn" : "classic", handDrawnSeed: 7,
    fontFamily: ink.font, fontSize: hand ? 19 : 15, deterministicIds: true,
    flowchart: { htmlLabels: false, curve: "basis", padding: 14, nodeSpacing: 46, rankSpacing: 54, useMaxWidth: false },
    sequence: { useMaxWidth: false, mirrorActors: false, actorMargin: 60, noteMargin: 12, messageFontFamily: ink.font,
      actorFontFamily: ink.font, noteFontFamily: ink.font },
    class: { htmlLabels: false, useMaxWidth: false }, state: { useMaxWidth: false }, er: { useMaxWidth: false },
    gantt: { useMaxWidth: false }, journey: { useMaxWidth: false }, timeline: { useMaxWidth: false },
    mindmap: { useMaxWidth: false, padding: 14 }, pie: { useMaxWidth: false, textPosition: 0.62 }, quadrantChart: { useMaxWidth: false },
    xyChart: { useMaxWidth: false }, sankey: { useMaxWidth: false },
    themeVariables: { fontFamily: ink.font, ...variables, ...pies, ...scale },
  });
}

/** Tidy what models commonly get wrong, without changing a valid diagram. */
export function normaliseSource(source: string): string {
  let text = source.replace(/\r\n?/g, "\n").replace(/^\s*```(?:mermaid)?\s*$/gm, "").trim();
  text = text.replace(/^flowchart\s*$/m, "flowchart TD");
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
  return { width: Math.ceil(box ? Number(box[1]) : 600), height: Math.ceil(box ? Number(box[2]) : 400) };
}

async function render(source: string, style: BoardStyle, dark: boolean): Promise<{ svg: string; width: number; height: number }> {
  const mermaid = await load();
  if (style !== "clean") {
    try { await document.fonts?.load('600 19px "Caveat"'); } catch { void 0; }  // measure labels in the font they are drawn with
  }
  const run = queue.then(async () => {
    configure(mermaid, style, dark);
    const id = `dikdik-diagram-${++counter}`;
    try {
      const { svg } = await mermaid.render(id, source);
      const dims = size(svg);
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

async function drawOnce(text: string, style: BoardStyle, dark: boolean): Promise<{ svg: string; width: number; height: number }> {
  const key = `${style}:${dark}:${text}`;
  const cached = cache.get(key);
  if (cached) return cached;
  const failed = failures.get(key);
  if (failed) throw new Error(failed);
  try {
    const result = await render(text, style, dark).catch(error => {
      const fixed = quoteLabels(text);
      if (fixed === text) throw error;
      return render(fixed, style, dark);  // a common model slip: unquoted labels with brackets or punctuation
    });
    cache.set(key, result);
    if (cache.size > 300) cache.delete(cache.keys().next().value!);
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message.split("\n")[0]!.slice(0, 160) : "unknown error";
    if (!/could not be loaded/.test(message)) failures.set(key, message);
    throw new Error(message);
  }
}

/** A diagram card for one ```mermaid block. */
export function renderMermaid(source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement {
  const figure = document.createElement("figure");
  figure.className = "diagram";
  const text = normaliseSource(source);
  if (typeof document.createElementNS !== "function" || typeof window === "undefined") {
    figure.append(fallback(source, "mermaid"));
    return figure;
  }
  const title = (/^\s*(?:---\s*\n\s*title:\s*(.+)|title\s+(.+))$/m.exec(text)?.slice(1).find(Boolean) ?? "").trim().replace(/^["']|["']$/g, "");
  const placeholder = document.createElement("div");
  placeholder.className = "diagram-loading";
  placeholder.textContent = "Drawing…";
  figure.append(placeholder);
  const first = !shown.has(text);
  shown.add(text);
  drawingCard(figure, {
    title, source: text, sourceLabel: "Copy source", name: "diagram", animateFirst: first,
    draw: async (style, animate) => {
      const result = await drawOnce(text, style, isDark());
      const font = style === "clean" ? "" : await handwritingFont();
      return { ...result, svg: finishSvg(result.svg, style, font, animate) };
    },
    onError: (error: unknown) => {
      const note = document.createElement("p");
      note.className = "diagram-error";
      note.textContent = `This diagram could not be drawn (${error instanceof Error ? error.message : "unknown error"}). Its source is shown instead.`;
      figure.className = "diagram";
      figure.replaceChildren(note, fallback(source, "mermaid"));
    },
  });
  return figure;
}
