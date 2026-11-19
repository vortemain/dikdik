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

/** Relative luminance (WCAG) of a #rrggbb colour. */
function luminance(hex: string): number {
  const value = /^#([0-9a-f]{6})$/i.exec(hex)?.[1];
  if (!value) return 0;
  const [r, g, b] = [0, 2, 4].map(i => parseInt(value.slice(i, i + 2), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
/** Of two text colours, the one that reads best on `background`. */
export function readableOn(background: string, light: string, dark: string): string {
  const contrast = (a: string, b: string) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x! + 0.05) / (y! + 0.05); };
  return contrast(background, light) >= contrast(background, dark) ? light : dark;
}

/** Colours for every Mermaid diagram type, per board style: each fill gets text that contrasts with it. */
export function themeFor(style: BoardStyle, dark: boolean): Record<string, string> {
  const ink = palette(style, dark);
  const light = style === "chalkboard" || (style === "clean" && dark) ? ink.ink : "#ffffff";
  const deep = style === "chalkboard" ? "#1d2b24" : style === "whiteboard" ? "#1f2937" : dark ? "#141413" : ink.ink;
  // Section fills (timelines, mind-map branches, journeys, Gantt rows) and strong data colours (charts).
  const sections = style === "chalkboard" ? ["#3f6253", "#4a5f3a", "#3d566b", "#5e4a3a", "#56456a", "#33605f"]
    : style === "whiteboard" ? ["#e8eefb", "#fef3c7", "#dcfce7", "#fde2e4", "#ede9fe", "#e0f2fe"]
    : dark ? ["#22352b", "#232b3a", "#3a3020", "#3a2426", "#2e2840", "#1f3536"]
    : ["#e6f0ea", "#e8eefb", "#fff4e0", "#fde8e8", "#efe9fb", "#e3f4f4"];
  const data = style === "chalkboard" ? ["#fff3a3", "#9fd8ff", "#ffb3b3", "#b7f0b1", "#f8c6ff", "#ffd59e"]
    : style === "whiteboard" ? ["#1f4fa8", "#c0262d", "#1f7a3a", "#d97706", "#7c3aed", "#0e7490"]
    : dark ? ["#7cc29b", "#8fb2ee", "#f2a65a", "#e98a9a", "#c3a6f5", "#e8d06b"]
    : ["#2f6b4f", "#2b579a", "#c2410c", "#b91c1c", "#7c3aed", "#a16207"];
  const text = (fill: string) => readableOn(fill, light, deep);
  const variables: Record<string, string> = {
    fontFamily: ink.font, fontSize: style === "clean" ? "15px" : "18px",
    background: ink.board, textColor: style === "whiteboard" ? ink.label : ink.ink, lineColor: style === "clean" ? ink.muted : ink.ink,
    titleColor: style === "whiteboard" ? ink.label : ink.ink,
    // Labels on lines sit on the board colour, fully opaque, so lines never run through their text.
    edgeLabelBackground: ink.board, labelBackgroundColor: ink.board, labelBackground: ink.board,
    transitionLabelColor: style === "whiteboard" ? ink.label : ink.ink,
  };
  sections.concat(sections).forEach((fill, index) => {
    variables[`cScale${index}`] = fill;
    variables[`cScaleLabel${index}`] = text(fill);
    variables[`cScalePeer${index}`] = style === "chalkboard" ? ink.muted : ink.ink;
    variables[`cScaleInv${index}`] = data[index % data.length]!;
  });
  data.concat(data).slice(0, 8).forEach((fill, index) => {
    variables[`git${index}`] = fill;
    variables[`gitBranchLabel${index}`] = text(fill);
    variables[`gitInv${index}`] = text(fill);
  });
  // The mind map's central topic is styled by two rules at once (git0 and the first section):
  // give both the same fill so its text colour always matches its background.
  variables.git0 = sections[0]!;
  variables.gitBranchLabel0 = text(sections[0]!);
  data.concat(data).forEach((fill, index) => { variables[`pie${index + 1}`] = fill; });
  Object.assign(variables, {
    pieOpacity: "1", pieStrokeColor: ink.board, pieStrokeWidth: "2px", pieOuterStrokeColor: style === "clean" ? ink.muted : ink.ink,
    pieSectionTextColor: text(data[0]!), pieTitleTextColor: variables.titleColor, pieLegendTextColor: variables.textColor,
    pieSectionTextSize: style === "clean" ? "15px" : "19px", pieTitleTextSize: style === "clean" ? "19px" : "26px", pieLegendTextSize: style === "clean" ? "15px" : "18px",
    attributeBackgroundColorOdd: style === "chalkboard" ? "#35574a" : style === "whiteboard" ? "#ffffff" : dark ? "#1f2b25" : "#ffffff",
    attributeBackgroundColorEven: style === "chalkboard" ? "#2f5244" : style === "whiteboard" ? "#f3f6fc" : dark ? "#242424" : "#f5f8f6",
    relationLabelBackground: ink.board, relationLabelColor: variables.textColor,
    rowOdd: style === "chalkboard" ? "#35574a" : style === "whiteboard" ? "#ffffff" : dark ? "#1f2b25" : "#ffffff",
    rowEven: style === "chalkboard" ? "#2f5244" : style === "whiteboard" ? "#f3f6fc" : dark ? "#242424" : "#f5f8f6",
    // Gantt: readable bars on every board.
    sectionBkgColor: sections[0]!, altSectionBkgColor: "transparent", sectionBkgColor2: sections[1]!,
    taskBkgColor: sections[2]!, taskBorderColor: data[1]!, taskTextColor: text(sections[2]!), taskTextLightColor: text(sections[2]!),
    taskTextDarkColor: text(sections[2]!), taskTextOutsideColor: variables.textColor, taskTextClickableColor: variables.textColor,
    activeTaskBkgColor: sections[3]!, activeTaskBorderColor: data[0]!, doneTaskBkgColor: sections[4]!, doneTaskBorderColor: ink.muted,
    critBkgColor: sections[3]!, critBorderColor: data[2]!, gridColor: style === "chalkboard" ? "rgba(244,241,230,0.18)" : ink.muted,
    todayLineColor: data[2]!, excludeBkgColor: "transparent",
    // Quadrant charts.
    quadrant1Fill: sections[0]!, quadrant2Fill: sections[1]!, quadrant3Fill: sections[2]!, quadrant4Fill: sections[3]!,
    quadrant1TextFill: text(sections[0]!), quadrant2TextFill: text(sections[1]!), quadrant3TextFill: text(sections[2]!), quadrant4TextFill: text(sections[3]!),
    quadrantPointFill: data[0]!, quadrantPointTextFill: variables.textColor, quadrantXAxisTextFill: variables.textColor,
    quadrantYAxisTextFill: variables.textColor, quadrantTitleFill: variables.titleColor,
    quadrantInternalBorderStrokeFill: style === "clean" ? ink.muted : ink.ink, quadrantExternalBorderStrokeFill: style === "clean" ? ink.muted : ink.ink,
  });
  if (style === "chalkboard") Object.assign(variables, {
    primaryColor: "#46695a", primaryTextColor: ink.ink, primaryBorderColor: ink.ink,
    secondaryColor: "#35574a", secondaryTextColor: ink.ink, secondaryBorderColor: "#fff3a3", tertiaryColor: "#3b5e50",
    tertiaryTextColor: ink.ink, tertiaryBorderColor: "#9fd8ff", noteBkgColor: "#3b5e50",
    noteTextColor: "#fff3a3", noteBorderColor: "#fff3a3", clusterBkg: "#31513f",
    clusterBorder: ink.muted, actorBkg: ink.board, actorBorder: ink.ink, actorTextColor: ink.ink,
    actorLineColor: ink.muted, signalColor: ink.ink, signalTextColor: ink.ink, labelBoxBkgColor: ink.board,
    labelTextColor: ink.ink, loopTextColor: ink.ink, sequenceNumberColor: ink.board,
  });
  else if (style === "whiteboard") Object.assign(variables, {
    primaryColor: "#ffffff", primaryTextColor: ink.label, primaryBorderColor: ink.ink,
    secondaryColor: "#fff1f2", secondaryTextColor: ink.label, secondaryBorderColor: "#c0262d", tertiaryColor: "#f0fdf4",
    tertiaryTextColor: ink.label, tertiaryBorderColor: "#1f7a3a", noteBkgColor: "#fff7cc",
    noteTextColor: "#3d3a2a", noteBorderColor: "#d97706", clusterBkg: "#f8fafc",
    clusterBorder: "#94a3b8", actorBkg: "#ffffff", actorBorder: ink.ink, actorTextColor: ink.label,
    actorLineColor: "#94a3b8", signalColor: ink.ink, signalTextColor: ink.label, labelBoxBkgColor: "#ffffff", labelTextColor: ink.label,
  });
  else if (dark) Object.assign(variables, {
    primaryColor: "#1f3329", primaryTextColor: ink.ink, primaryBorderColor: "#7cc29b",
    secondaryColor: "#2a2f3a", secondaryTextColor: ink.ink, tertiaryColor: "#332b22", tertiaryTextColor: ink.ink,
    noteBkgColor: "#3a3420", noteTextColor: "#f3eed9", clusterBkg: "#232322", clusterBorder: "#4a4a46",
    actorBkg: "#1f3329", actorBorder: "#7cc29b", actorTextColor: ink.ink, signalColor: ink.ink, signalTextColor: ink.ink,
    labelBoxBkgColor: "#1f3329", labelTextColor: ink.ink, loopTextColor: ink.ink,
  });
  else Object.assign(variables, {
    primaryColor: "#e6f0ea", primaryTextColor: ink.ink, primaryBorderColor: "#2f6b4f",
    secondaryColor: "#e8eefb", secondaryTextColor: ink.ink, tertiaryColor: "#fff4e0", tertiaryTextColor: ink.ink,
    noteBkgColor: "#fff8db", noteTextColor: "#3d3a2a", clusterBkg: "#f7f7f5", clusterBorder: "#d6d6d1",
  });
  return variables;
}

function configure(mermaid: MermaidApi, style: BoardStyle, dark: boolean): void {
  const key = `${style}:${dark}`;
  if (configured === key) return;
  configured = key;
  const ink = palette(style, dark);
  const hand = style !== "clean";
  const variables = themeFor(style, dark);
  const data = [1, 2, 3, 4, 5, 6].map(n => variables[`pie${n}`]!);
  const sections = [0, 1, 2, 3, 4, 5].map(n => variables[`cScale${n}`]!);
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", htmlLabels: false, theme: "base",
    look: hand ? "handDrawn" : "classic", handDrawnSeed: 7,
    fontFamily: ink.font, deterministicIds: true,
    flowchart: { htmlLabels: false, curve: "basis", padding: 14, nodeSpacing: 34, rankSpacing: 50, useMaxWidth: false, wrappingWidth: 200 },
    sequence: { useMaxWidth: false, mirrorActors: false, actorMargin: 60, noteMargin: 12, messageFontFamily: ink.font,
      actorFontFamily: ink.font, noteFontFamily: ink.font, messageFontSize: hand ? 18 : 15, actorFontSize: hand ? 18 : 15, noteFontSize: hand ? 17 : 14 },
    class: { htmlLabels: false, useMaxWidth: false }, state: { useMaxWidth: false }, er: { useMaxWidth: false, fontSize: hand ? 16 : 13 },
    // A fixed, comfortable width: the default sizes from an invisible container (too narrow, bars clipped).
    gantt: { useMaxWidth: false, useWidth: 920, fontSize: hand ? 16 : 13, sectionFontSize: hand ? 17 : 13, barHeight: 30, barGap: 8,
      topPadding: 64, leftPadding: 130, rightPadding: 40, gridLineStartPadding: 44, axisFormat: "%d %b", numberSectionStyles: 2 },
    journey: { useMaxWidth: false, titleFontFamily: ink.font, titleColor: variables.titleColor, titleFontSize: hand ? "30px" : "20px",
      actorColours: data, sectionFills: sections, sectionColours: sections.map(fill => readableOn(fill, "#ffffff", variables.textColor!)) },
    timeline: { useMaxWidth: false, padding: 8 },
    mindmap: { useMaxWidth: false, padding: 16 }, pie: { useMaxWidth: false, textPosition: 0.66 }, quadrantChart: { useMaxWidth: false },
    xyChart: { useMaxWidth: false, width: 760, height: 460 }, sankey: { useMaxWidth: false },
    themeVariables: {
      ...variables,
      xyChart: { backgroundColor: "transparent", titleColor: variables.titleColor, xAxisLabelColor: variables.textColor,
        xAxisTitleColor: variables.textColor, xAxisTickColor: variables.lineColor, xAxisLineColor: variables.lineColor,
        yAxisLabelColor: variables.textColor, yAxisTitleColor: variables.textColor, yAxisTickColor: variables.lineColor,
        yAxisLineColor: variables.lineColor, plotColorPalette: data.join(",") },
    },
  });
}

// Mermaid appends configured colour lists to its own, so journeys always use its neon defaults:
// swap them for the board's colours in the finished drawing.
const JOURNEY_DEFAULTS = ["#8FBC8F", "#7CFC00", "#00FFFF", "#20B2AA", "#B0E0E6", "#FFFFE0"];
export function recolourJourney(svg: string, style: BoardStyle, dark: boolean): string {
  const variables = themeFor(style, dark);
  return JOURNEY_DEFAULTS.reduce((result, colour, index) =>
    result.replace(new RegExp(`(fill|stroke)="${colour}"`, "gi"), `$1="${variables[`pie${index + 1}`]}"`), svg);
}

/** Mermaid's own CSS draws some label boxes see-through, so lines show through their text. */
export function diagramCss(style: BoardStyle, dark: boolean): string {
  const board = palette(style, dark).board;
  return `.edgeLabel rect,.edgeLabel .label rect,.labelBkg{opacity:1!important;fill:${board}!important;background-color:${board}!important;}`
    + `.edgeLabel,.edgeLabel p{background-color:${board}!important;}`
    // The mind map's central topic is placed at its centre but drawn left-aligned, so it spills out.
    + `.mindmap-node.section-root .label text{text-anchor:middle;}`;
}

/** Tidy what models commonly get wrong, without changing a valid diagram. */
export function normaliseSource(source: string): string {
  let text = source.replace(/\r\n?/g, "\n").replace(/^\s*```(?:mermaid)?\s*$/gm, "").trim();
  text = text.replace(/^flowchart\s*$/m, "flowchart TD");
  text = text.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/–|—/g, "-");
  // Mind-map circles ((topic)) are sized too small for their words, which spill out; a rounded
  // box ((…) → (…)) fits its text in every font.
  if (/^\s*mindmap\b/.test(text)) text = text.replace(/\(\(([^()\n]+)\)\)/g, "($1)");
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
      const rendered = (await mermaid.render(id, source)).svg;
      const svg = /^\s*journey\b/.test(source) ? recolourJourney(rendered, style, dark) : rendered;
      const dims = size(svg);
      const sized = svg.replace(/<svg\b[^>]*>/, tag =>
        tag.replace(/\s(?:width|height|style)="[^"]*"/g, "").replace(/^<svg/, `<svg width="${dims.width}" height="${dims.height}"`) + `<style>${diagramCss(style, dark)}</style>`);
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
