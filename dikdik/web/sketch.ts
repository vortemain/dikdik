// Function plots (```plot) and science sketches (```sketch), drawn like a teacher on the board.
//
// ```plot
// y = x^2 - 4
// y = 2x + 1 | label: tangent
// x: -4..4
// point: (2, 0) root
// title: A parabola and a line
// ```
// Curves are computed here from a small, safe maths parser (no eval): the model describes the
// function, Dikdik draws the real curve. Sketches are JSON shapes in board units (y up):
// lines, arrows/vectors, circles, rectangles, polygons, smooth curves, angles, points, text.
// Both render as SVG through ./board, so they share its styles, handwriting and animation.

import { type BoardStyle, drawingCard, finishSvg, handwritingFont, isDark, palette, type Palette } from "./board";
import { LabelLayout, haloText, textWidth, type Point } from "./label-layout";

// --- a safe expression parser -------------------------------------------------------------

type Node = { kind: "num"; value: number } | { kind: "var" } | { kind: "neg"; arg: Node }
  | { kind: "bin"; op: string; left: Node; right: Node } | { kind: "call"; name: string; args: Node[] };

const FUNCS: Record<string, (...args: number[]) => number> = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan, arcsin: Math.asin,
  arccos: Math.acos, arctan: Math.atan, sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh, sqrt: Math.sqrt, abs: Math.abs,
  exp: Math.exp, ln: Math.log, log: Math.log10, log10: Math.log10, log2: Math.log2, floor: Math.floor, ceil: Math.ceil,
  round: Math.round, sign: Math.sign, min: Math.min, max: Math.max, pow: Math.pow, sec: (v: number) => 1 / Math.cos(v),
  csc: (v: number) => 1 / Math.sin(v), cot: (v: number) => 1 / Math.tan(v), cbrt: Math.cbrt,
};
const CONSTS: Record<string, number> = { pi: Math.PI, "π": Math.PI, e: Math.E, tau: Math.PI * 2 };

export function parseExpression(source: string): Node {
  const text = source.replace(/\*\*/g, "^").replace(/[−–]/g, "-").replace(/×|·/g, "*").replace(/÷/g, "/").replace(/\\cdot/g, "*")
    .replace(/\\(sin|cos|tan|ln|log|sqrt|exp|pi)\b/g, "$1").replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, "(($1)/($2))")
    .replace(/[{}]/g, m => (m === "{" ? "(" : ")"));
  const tokens = text.match(/\d+\.?\d*(?:e[-+]?\d+)?|\.\d+|[A-Za-zπ]+|[-+*/^(),|]/g) ?? [];
  if (tokens.join("").replace(/\s/g, "") !== text.replace(/\s/g, "").replace(/\\/g, "")) {
    const bad = text.replace(/\d+\.?\d*(?:e[-+]?\d+)?|\.\d+|[A-Za-zπ]+|[-+*/^(),|]|\s/g, "");
    if (bad) throw new Error(`Unexpected "${bad.slice(0, 10)}" in ${source}`);
  }
  // Implicit multiplication: 2x, 2(x+1), (x+1)(x-1), x sin(x), 3pi.
  const expanded: string[] = [];
  tokens.forEach((token, index) => {
    const previous = tokens[index - 1];
    const ends = previous !== undefined && (/^[\d.]/.test(previous) || previous === ")" || (/^[A-Za-zπ]+$/.test(previous) && !(previous in FUNCS)));
    const starts = /^[\d.]/.test(token) || token === "(" || /^[A-Za-zπ]+$/.test(token);
    if (ends && starts) expanded.push("*");
    expanded.push(token);
  });
  let position = 0;
  const peek = () => expanded[position];
  const take = () => expanded[position++];
  const expect = (value: string) => { if (take() !== value) throw new Error(`Expected "${value}" in ${source}`); };
  const primary = (): Node => {
    const token = take();
    if (token === undefined) throw new Error(`Incomplete expression: ${source}`);
    if (token === "(") { const inner = sum(); expect(")"); return inner; }
    if (token === "|") { const inner = sum(); expect("|"); return { kind: "call", name: "abs", args: [inner] }; }
    if (token === "-") return { kind: "neg", arg: power() };
    if (token === "+") return power();
    if (/^[\d.]/.test(token)) return { kind: "num", value: Number(token) };
    const name = token.toLowerCase();
    if (name === "x") return { kind: "var" };
    if (name in CONSTS || token in CONSTS) return { kind: "num", value: CONSTS[name] ?? CONSTS[token]! };
    if (name in FUNCS) {
      if (peek() === "(") {
        take();
        const args = [sum()];
        while (peek() === ",") { take(); args.push(sum()); }
        expect(")");
        return { kind: "call", name, args };
      }
      return { kind: "call", name, args: [power()] };  // sin x
    }
    throw new Error(`Unknown name "${token}" in ${source} (use x as the variable)`);
  };
  const power = (): Node => {
    const base = primary();
    if (peek() === "^") { take(); return { kind: "bin", op: "^", left: base, right: unary() }; }
    return base;
  };
  const unary = (): Node => (peek() === "-" ? (take(), { kind: "neg", arg: unary() }) : peek() === "+" ? (take(), unary()) : power());
  const product = (): Node => {
    let node = unary();
    while (peek() === "*" || peek() === "/") { const op = take()!; node = { kind: "bin", op, left: node, right: unary() }; }
    return node;
  };
  const sum = (): Node => {
    let node = product();
    while (peek() === "+" || peek() === "-") { const op = take()!; node = { kind: "bin", op, left: node, right: product() }; }
    return node;
  };
  const tree = sum();
  if (position < expanded.length) throw new Error(`Could not read "${expanded.slice(position).join("")}" in ${source}`);
  return tree;
}

export function evaluate(node: Node, x: number): number {
  switch (node.kind) {
    case "num": return node.value;
    case "var": return x;
    case "neg": return -evaluate(node.arg, x);
    case "call": return FUNCS[node.name]!(...node.args.map(arg => evaluate(arg, x)));
    case "bin": {
      const a = evaluate(node.left, x), b = evaluate(node.right, x);
      return node.op === "+" ? a + b : node.op === "-" ? a - b : node.op === "*" ? a * b : node.op === "/" ? a / b : Math.pow(a, b);
    }
  }
}

// --- plot specifications --------------------------------------------------------------

export interface PlotSpec {
  title: string; functions: { expression: string; label: string; tree: Node }[]; vertical: { x: number; label: string }[];
  x: [number, number]; y: [number, number] | null; points: { x: number; y: number; label: string }[];
  xLabel: string; yLabel: string; grid: boolean; piAxis: boolean;
}

function range(text: string): [number, number] | null {
  // A bound is a number, a multiple of π, or a fraction of it: 3, -2.5, 2pi, -π, pi/2.
  const bound = String.raw`-?(?:(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?\s*(?:pi|π)?|pi|π)(?:\s*/\s*\d+)?`;
  const match = new RegExp(`(${bound})\\s*(?:\\.\\.|to|,|—|–)\\s*(${bound})`, "i").exec(text);
  if (!match) return null;
  const value = (raw: string) => evaluate(parseExpression(raw), 0);
  const low = value(match[1]!), high = value(match[2]!);
  return Number.isFinite(low) && Number.isFinite(high) && high > low ? [low, high] : null;
}

export function parsePlot(source: string): PlotSpec {
  const spec: PlotSpec = { title: "", functions: [], vertical: [], x: [-5, 5], y: null, points: [], xLabel: "x", yLabel: "y", grid: true, piAxis: false };
  const trimmed = source.trim();
  if (trimmed.startsWith("{")) {
    const data = JSON.parse(trimmed) as Record<string, unknown>;
    const list = (Array.isArray(data.functions) ? data.functions : data.function ? [data.function] : []) as unknown[];
    const lines = list.map(item => typeof item === "string" ? item : `y = ${(item as { expression?: string; f?: string }).expression ?? (item as { f?: string }).f ?? ""}${(item as { label?: string }).label ? ` | label: ${(item as { label?: string }).label}` : ""}`);
    const xs = data.x as number[] | undefined, ys = data.y as number[] | undefined;
    const points = (Array.isArray(data.points) ? data.points : []) as { x: number; y: number; label?: string }[];
    return parsePlot([
      ...lines, ...(xs ? [`x: ${xs[0]}..${xs[1]}`] : []), ...(ys ? [`y: ${ys[0]}..${ys[1]}`] : []),
      ...points.map(p => `point: (${p.x}, ${p.y}) ${p.label ?? ""}`), ...(data.title ? [`title: ${data.title}`] : []),
      ...(data.xlabel ? [`xlabel: ${data.xlabel}`] : []), ...(data.ylabel ? [`ylabel: ${data.ylabel}`] : []),
    ].join("\n"));
  }
  for (const raw of trimmed.split("\n")) {
    const line = raw.trim().replace(/^[-*]\s+/, "");
    if (!line || line.startsWith("#") || line.startsWith("//")) continue;
    const [main, ...options] = line.split("|");
    const label = (options.join("|").match(/label:\s*(.+)/i)?.[1] ?? "").trim();
    let match: RegExpExecArray | null;
    if ((match = /^title\s*[:=]\s*(.+)$/i.exec(line))) spec.title = match[1]!.trim();
    else if ((match = /^x[-_ ]?label\s*[:=]\s*(.+)$/i.exec(line))) spec.xLabel = match[1]!.trim();
    else if ((match = /^y[-_ ]?label\s*[:=]\s*(.+)$/i.exec(line))) spec.yLabel = match[1]!.trim();
    else if ((match = /^grid\s*[:=]\s*(\w+)/i.exec(line))) spec.grid = !/^(off|no|false|0)$/i.test(match[1]!);
    else if ((match = /^(?:x|domain)\s*(?::|\bfrom\b|∈|in)\s*(.+)$/i.exec(line)) && range(match[1]!)) { spec.x = range(match[1]!)!; spec.piAxis = /pi|π/i.test(match[1]!); }
    else if ((match = /^(?:y|range)\s*(?::|\bfrom\b|∈|in)\s*(.+)$/i.exec(line)) && range(match[1]!)) spec.y = range(match[1]!);
    else if ((match = /^points?\s*[:=]\s*(.+)$/i.exec(line))) {
      for (const point of match[1]!.matchAll(/\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)\s*([^(;]*)/g)) {
        const x = evaluate(parseExpression(point[1]!), 0), y = evaluate(parseExpression(point[2]!), 0);
        if (Number.isFinite(x) && Number.isFinite(y)) spec.points.push({ x, y, label: point[3]!.replace(/^["'\s]+|["'\s,]+$/g, "") });
      }
    } else if ((match = /^x\s*=\s*(-?[\d.]+)\s*$/i.exec(main!.trim()))) spec.vertical.push({ x: Number(match[1]), label });
    else {
      const body = main!.trim().replace(/^(?:y|f\s*\(\s*x\s*\)|g\s*\(\s*x\s*\)|h\s*\(\s*x\s*\)|[a-z]\s*\(\s*x\s*\))\s*=\s*/i, "");
      if (/^[a-z_ ]+:/i.test(body)) continue;  // an option we do not know; ignore rather than fail
      spec.functions.push({ expression: body, label: label || main!.trim(), tree: parseExpression(body) });
    }
  }
  if (!spec.functions.length && !spec.points.length && !spec.vertical.length) throw new Error("A plot needs at least one function such as y = x^2");
  return spec;
}

// --- hand-drawn strokes -------------------------------------------------------------------

function random(seed: number): () => number {
  let state = seed >>> 0 || 1;
  return () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 4294967296; };
}


/** A path through points; on boards each segment wobbles a little and is drawn twice, like chalk. */
function strokePath(points: Point[], hand: boolean, rand: () => number, closed = false, smooth = false): string {
  if (points.length < 2) return "";
  const pts = closed ? [...points, points[0]!] : points;
  const draw = (offset: number) => {
    const jitter = (value: number) => (hand ? value + (rand() - 0.5) * offset : value);
    if (smooth && pts.length > 2) {
      let d = `M${jitter(pts[0]![0]).toFixed(1)},${jitter(pts[0]![1]).toFixed(1)}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)]!, p1 = pts[i]!, p2 = pts[i + 1]!, p3 = pts[Math.min(pts.length - 1, i + 2)]!;
        const c1: Point = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
        const c2: Point = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
        d += ` C${jitter(c1[0]).toFixed(1)},${jitter(c1[1]).toFixed(1)} ${jitter(c2[0]).toFixed(1)},${jitter(c2[1]).toFixed(1)} ${jitter(p2[0]).toFixed(1)},${jitter(p2[1]).toFixed(1)}`;
      }
      return d;
    }
    let d = `M${jitter(pts[0]![0]).toFixed(1)},${jitter(pts[0]![1]).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1]!, [x1, y1] = pts[i]!;
      if (hand) {
        const bend = Math.hypot(x1 - x0, y1 - y0) * 0.012 * (rand() - 0.5) * offset;
        const mx = (x0 + x1) / 2 + (y1 - y0 === 0 ? 0 : bend), my = (y0 + y1) / 2 + bend;
        d += ` Q${mx.toFixed(1)},${my.toFixed(1)} ${jitter(x1).toFixed(1)},${jitter(y1).toFixed(1)}`;
      } else d += ` L${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return d;
  };
  return hand ? `${draw(1.6)} ${draw(1.1)}` : draw(0);
}

function escapeXml(text: string): string {
  return text.replace(/[<>&"]/g, char => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[char]!);
}

function colourOf(name: string | undefined, ink: Palette, fallback: string): string {
  if (!name) return fallback;
  const map: Record<string, number> = { yellow: 0, blue: 1, red: 2, green: 3, pink: 4, purple: 4, orange: 5 };
  if (name in map) return ink.accents[map[name]!]!;
  if (/^#[0-9a-f]{3,8}$/i.test(name)) return name;
  return name === "white" || name === "black" || name === "ink" ? ink.ink : fallback;
}

function niceStep(span: number): number {
  const raw = span / 8;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  return ([1, 2, 2.5, 5, 10].find(step => step * power >= raw) ?? 10) * power;
}

const SUPERSCRIPT: Record<string, string> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻", "n": "ⁿ", "x": "ˣ" };
/** How a formula is written on a board: x^2 → x², * → ·, sqrt → √, pi → π. */
export function prettyFormula(text: string): string {
  return text
    .replace(/\^\(?(-?[0-9nx]+)\)?/g, (_all, power: string) => [...power].map(char => SUPERSCRIPT[char] ?? char).join(""))
    .replace(/\s*\*\s*/g, "·").replace(/\bsqrt\b/g, "√").replace(/\bpi\b/g, "π").replace(/-/g, "−");
}

function fmt(value: number, pi = false): string {
  if (Math.abs(value) < 1e-9) return "0";
  if (pi) {
    const halves = Math.round(value / (Math.PI / 2));
    if (Math.abs(value - halves * Math.PI / 2) < 1e-9) {
      const sign = halves < 0 ? "−" : "", n = Math.abs(halves);
      return n % 2 === 0 ? `${sign}${n / 2 === 1 ? "" : n / 2}π` : `${sign}${n === 1 ? "" : n}π/2`;
    }
  }
  if (Math.abs(Math.abs(value) - Math.PI) < 1e-9) return value < 0 ? "−π" : "π";
  const rounded = Math.round(value * 1000) / 1000;
  return String(rounded).replace("-", "−");
}

// --- plot rendering -------------------------------------------------------------------------

export function plotSvg(spec: PlotSpec, style: BoardStyle, dark: boolean): { svg: string; width: number; height: number } {
  const ink = palette(style, dark);
  const hand = style !== "clean";
  const rand = random(7);
  // The title has its own band above the graph, so it never meets the y-axis or its name.
  const width = 700, height = 460, left = 58, right = 40, top = spec.title ? 70 : 36, bottom = 48;
  const plotW = width - left - right, plotH = height - top - bottom;
  const [x0, x1] = spec.x;
  const samples = 480;
  const curves = spec.functions.map(fn => {
    const values: [number, number][] = [];
    for (let i = 0; i <= samples; i++) {
      const x = x0 + (x1 - x0) * i / samples;
      let y: number;
      try { y = evaluate(fn.tree, x); } catch { y = NaN; }
      values.push([x, y]);
    }
    return values;
  });
  let [y0, y1] = spec.y ?? [Infinity, -Infinity];
  if (!spec.y) {
    const finite = curves.flat().map(([, y]) => y).filter(Number.isFinite).sort((a, b) => a - b);
    finite.push(...spec.points.map(p => p.y));
    if (finite.length) {
      const lo = finite[Math.floor(finite.length * 0.03)]!, hi = finite[Math.ceil(finite.length * 0.97) - 1]!;
      y0 = Math.min(lo, ...spec.points.map(p => p.y)); y1 = Math.max(hi, ...spec.points.map(p => p.y));
    } else { y0 = -5; y1 = 5; }
    if (y0 > 0 && y0 < (y1 - y0) * 0.6) y0 = 0;
    if (y1 < 0 && -y1 < (y1 - y0) * 0.6) y1 = 0;
    if (y1 - y0 < 1e-6) { y0 -= 1; y1 += 1; }
    const pad = (y1 - y0) * 0.08;
    y0 -= pad; y1 += pad;
  }
  const sx = (x: number) => left + (x - x0) / (x1 - x0) * plotW;
  const sy = (y: number) => top + plotH - (y - y0) / (y1 - y0) * plotH;
  const parts: string[] = [];
  const font = `font-family:${ink.font.replace(/"/g, "'")}`;
  const textSize = hand ? 19 : 13;
  const layout = new LabelLayout({ x: 4, y: 4, w: width - 8, h: height - 8 }, hand);
  const write = (placed: { x: number; y: number; anchor: string }, text: string, colour: string, size: number, weight = "") =>
    haloText(placed, escapeXml(text), colour, ink.board, `${font};font-size:${size}px${weight}`);
  const axisY = Math.min(Math.max(sy(0), top), top + plotH), axisX = Math.min(Math.max(sx(0), left), left + plotW);
  if (spec.title) {
    const title = layout.fixed(spec.title, textSize + 5, width / 2, 32, "middle");
    parts.push(write(title, spec.title, ink.ink, textSize + 5, ";font-weight:600"));
  }
  // graph paper and tick labels (reserved first: curve names must not cover them)
  if (spec.grid) {
    const stepX = spec.piAxis ? (x1 - x0 > 8 * Math.PI ? Math.PI * 2 : x1 - x0 > 3 * Math.PI ? Math.PI : Math.PI / 2) : niceStep(x1 - x0);
    const stepY = niceStep(y1 - y0);
    for (let x = Math.ceil(x0 / stepX) * stepX; x <= x1 + 1e-9; x += stepX) parts.push(`<line data-static="1" x1="${sx(x).toFixed(1)}" y1="${top}" x2="${sx(x).toFixed(1)}" y2="${top + plotH}" stroke="${ink.grid}" stroke-width="1"/>`);
    for (let y = Math.ceil(y0 / stepY) * stepY; y <= y1 + 1e-9; y += stepY) parts.push(`<line data-static="1" x1="${left}" y1="${sy(y).toFixed(1)}" x2="${left + plotW}" y2="${sy(y).toFixed(1)}" stroke="${ink.grid}" stroke-width="1"/>`);
    const tick = textSize - 2;
    for (let x = Math.ceil(x0 / stepX) * stepX; x <= x1 + 1e-9; x += stepX) {
      if (Math.abs(x) < 1e-9 && y0 <= 0 && y1 >= 0) continue;
      const text = fmt(x, spec.piAxis), half = textWidth(text, tick, hand) / 2 + 2;
      const at = layout.fixed(text, tick, Math.min(Math.max(sx(x), half + 2), width - half - 2), axisY + tick + 4, "middle");
      parts.push(write(at, text, ink.muted, tick));
    }
    for (let y = Math.ceil(y0 / stepY) * stepY; y <= y1 + 1e-9; y += stepY) {
      if (Math.abs(y) < 1e-9) continue;
      const text = fmt(y);
      const at = layout.fixed(text, tick, axisX - 7, sy(y) + tick * 0.35, "end");
      parts.push(write(at, text, ink.muted, tick));
    }
  }
  // axes with arrowheads
  const arrow = (x: number, y: number, dx: number, dy: number) => {
    const size = 9, nx = -dy, ny = dx;
    return `<path d="${strokePath([[x - dx * size + nx * size * 0.5, y - dy * size + ny * size * 0.5], [x, y], [x - dx * size - nx * size * 0.5, y - dy * size - ny * size * 0.5]], hand, rand)}" fill="none" stroke="${ink.ink}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
  };
  parts.push(`<path d="${strokePath([[left - 4, axisY], [left + plotW + 10, axisY]], hand, rand)}" fill="none" stroke="${ink.ink}" stroke-width="2" stroke-linecap="round"/>`,
    arrow(left + plotW + 10, axisY, 1, 0),
    `<path d="${strokePath([[axisX, top + plotH + 4], [axisX, top - 12]], hand, rand)}" fill="none" stroke="${ink.ink}" stroke-width="2" stroke-linecap="round"/>`,
    arrow(axisX, top - 12, 0, -1));
  layout.addPath([[left - 4, axisY], [left + plotW + 10, axisY]]);
  layout.addPath([[axisX, top + plotH + 4], [axisX, top - 12]]);
  // curves, broken at asymptotes and gaps
  const drawn: Point[][][] = curves.map((values, index) => {
    const colour = ink.accents[index % ink.accents.length]!;
    const segments: Point[][] = [];
    let current: Point[] = [];
    const limit = (y1 - y0) * 3;
    values.forEach(([x, y], i) => {
      const previous = values[i - 1];
      const jump = previous && Number.isFinite(previous[1]) && Number.isFinite(y) && Math.abs(y - previous[1]) > limit;
      if (!Number.isFinite(y) || jump || y < y0 - (y1 - y0) || y > y1 + (y1 - y0)) {
        if (current.length > 1) segments.push(current);
        current = [];
        if (!Number.isFinite(y) || jump) return;
      }
      current.push([sx(x), sy(Math.min(Math.max(y, y0 - (y1 - y0) * 0.2), y1 + (y1 - y0) * 0.2))]);
    });
    if (current.length > 1) segments.push(current);
    for (const segment of segments) {
      const thinned = hand ? segment.filter((_, i) => i % 4 === 0 || i === segment.length - 1) : segment;
      parts.push(`<path d="${strokePath(thinned, hand, rand, false, true)}" fill="none" stroke="${colour}" stroke-width="${hand ? 2.6 : 2.2}" stroke-linecap="round" stroke-linejoin="round" clip-path="url(#plot-area)"/>`);
      layout.addPath(segment.filter((_, i) => i % 2 === 0 || i === segment.length - 1).filter(([, y]) => y >= top - 4 && y <= top + plotH + 4));
    }
    return segments;
  });
  const lines = spec.vertical.map(line => {
    const x = sx(line.x);
    parts.push(`<path d="${strokePath([[x, top], [x, top + plotH]], hand, rand)}" fill="none" stroke="${ink.accents[2]}" stroke-width="2" stroke-dasharray="6 6"/>`);
    layout.addPath([[x, top], [x, top + plotH]]);
    return { line, x };
  });
  for (const point of spec.points) layout.reserve({ x: sx(point.x) - 6, y: sy(point.y) - 6, w: 12, h: 12 });
  // Names, placed where they cover nothing: axis names first, then each curve, line and point.
  const xName = layout.place(spec.xLabel, textSize, [[left + plotW + 10, axisY]], { prefer: [-0.4, -1] });
  const yName = layout.place(spec.yLabel, textSize, [[axisX, top - 12]], { prefer: [1, 0] });
  parts.push(write(xName, spec.xLabel, ink.ink, textSize), write(yName, spec.yLabel, ink.ink, textSize));
  drawn.forEach((segments, index) => {
    const inside = segments.flat().filter(([x, y]) => y >= top + 8 && y <= top + plotH - 8 && x >= left + 8 && x <= left + plotW - 8);
    if (!inside.length) return;
    const anchors = [0.88, 0.72, 0.95, 0.56, 0.4, 0.25, 0.1].map(f => inside[Math.min(inside.length - 1, Math.floor(inside.length * f))]!);
    const text = prettyFormula(spec.functions[index]!.label.replace(/^y\s*=\s*/i, "y = "));
    parts.push(write(layout.place(text, textSize, anchors, { gap: 9 }), text, ink.accents[index % ink.accents.length]!, textSize));
  });
  for (const { line, x } of lines) {
    if (!line.label) continue;
    const placed = layout.place(line.label, textSize, [[x, top + 14], [x, top + plotH - 14], [x, top + plotH / 2]], { prefer: [1, 0] });
    parts.push(write(placed, line.label, ink.accents[2]!, textSize));
  }
  for (const point of spec.points) {
    const text = `${point.label ? point.label + " " : ""}(${fmt(point.x)}, ${fmt(point.y)})`;
    parts.push(`<circle cx="${sx(point.x).toFixed(1)}" cy="${sy(point.y).toFixed(1)}" r="4.5" fill="${ink.ink}" stroke="${ink.ink}"/>`,
      write(layout.place(text, textSize - 1, [[sx(point.x), sy(point.y)]], { gap: 8 }), text, ink.ink, textSize - 1));
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">` +
    `<defs><clipPath id="plot-area"><rect x="${left}" y="${top - 4}" width="${plotW + 4}" height="${plotH + 8}"/></clipPath></defs>${parts.join("")}</svg>`;
  return { svg, width, height };
}

// --- sketches -------------------------------------------------------------------------------

interface SketchItem {
  type?: string; from?: number[]; to?: number[]; at?: number[]; center?: number[]; points?: number[][]; r?: number; rx?: number; ry?: number;
  label?: string; text?: string; color?: string; dashed?: boolean; fill?: boolean; size?: number; arrow?: boolean | string;
  start?: number; end?: number; width?: number;
}
export interface SketchSpec { title?: string; width?: number; height?: number; grid?: boolean; axes?: boolean; items: SketchItem[] }

/** Repairs the slips models commonly make in sketch JSON: a missing "to" key after "from", trailing commas, and smart quotes. */
export function repairSketchJson(source: string): string {
  return source.trim()
    .replace(/[“”]/g, '"')
    .replace(/("from"\s*:\s*\[[^\[\]]*\])\s*,\s*(\[[^\[\]]*\])/g, '$1, "to": $2')
    .replace(/,\s*([}\]])/g, "$1");
}

export function parseSketch(source: string): SketchSpec {
  let data: SketchSpec | SketchItem[];
  try {
    data = JSON.parse(source.trim()) as SketchSpec | SketchItem[];
  } catch (error) {
    try {
      data = JSON.parse(repairSketchJson(source)) as SketchSpec | SketchItem[];
    } catch {
      throw error;
    }
  }
  const spec = Array.isArray(data) ? { items: data } : data;
  if (!Array.isArray(spec.items) || !spec.items.length) throw new Error('A sketch needs "items": [...]');
  return spec;
}

export function sketchSvg(spec: SketchSpec, style: BoardStyle, dark: boolean): { svg: string; width: number; height: number } {
  const ink = palette(style, dark);
  const hand = style !== "clean";
  const rand = random(11);
  const allPoints: number[][] = [];
  for (const item of spec.items) {
    for (const p of [item.from, item.to, item.at, item.center, ...(item.points ?? [])]) if (Array.isArray(p) && p.length >= 2) allPoints.push(p);
    if (item.center && (item.r || item.rx)) {
      const r = item.r ?? item.rx ?? 0, ry = item.r ?? item.ry ?? 0;
      allPoints.push([item.center[0]! - r, item.center[1]! - ry], [item.center[0]! + r, item.center[1]! + ry]);
    }
  }
  const textSize = hand ? 20 : 14;
  const fit = (extra: number[][]) => {
    const xs = [...allPoints, ...extra].map(p => p[0]!), ys = [...allPoints, ...extra].map(p => p[1]!);
    const origin = spec.axes || spec.grid ? [0] : [];  // only axes and grids need the origin on the board
    const minX = Math.min(...origin, ...xs) - 0.6, maxX = Math.max(spec.width ?? -Infinity, ...origin, ...xs) + 0.6;
    const minY = Math.min(...origin, ...ys) - 0.6, maxY = Math.max(spec.height ?? -Infinity, ...origin, ...ys) + 0.6;
    return { minX, maxX, minY, maxY, unit: Math.min(70, 600 / Math.max(maxX - minX, 1), 430 / Math.max(maxY - minY, 1)) };
  };
  // Free text is centred on its point; widen the board so long notes are never cut off.
  const textExtent = (unit: number) => spec.items.filter(item => (item.type ?? "").toLowerCase() === "text" && Array.isArray(item.at)).flatMap(item => {
    const half = textWidth(String(item.text ?? item.label ?? ""), textSize * (item.size ?? 1), hand) / 2 / unit;
    return [[item.at![0]! - half, item.at![1]!], [item.at![0]! + half, item.at![1]!]];
  });
  let bounds = fit([]);
  bounds = fit(textExtent(bounds.unit));
  bounds = fit(textExtent(bounds.unit));
  const { minX, maxX, minY, maxY, unit } = bounds;
  const top = spec.title ? 44 : 10;
  const width = Math.round((maxX - minX) * unit + 20), height = Math.round((maxY - minY) * unit + top + 10);
  const px = (p: number[]): Point => [10 + (p[0]! - minX) * unit, top + (maxY - p[1]!) * unit];
  const font = `font-family:${ink.font.replace(/"/g, "'")}`;
  const parts: string[] = [];
  const layout = new LabelLayout(null, hand);
  const write = (placed: { x: number; y: number; anchor: string }, text: string, colour: string, size = textSize, weight = "") =>
    haloText(placed, escapeXml(text), colour, ink.board, `${font};font-size:${size}px${weight}`);
  if (spec.grid) {
    for (let x = Math.ceil(minX); x <= maxX; x++) parts.push(`<line data-static="1" x1="${px([x, 0])[0]}" y1="${top}" x2="${px([x, 0])[0]}" y2="${height - 10}" stroke="${ink.grid}"/>`);
    for (let y = Math.ceil(minY); y <= maxY; y++) parts.push(`<line data-static="1" x1="10" y1="${px([0, y])[1]}" x2="${width - 10}" y2="${px([0, y])[1]}" stroke="${ink.grid}"/>`);
  }
  const head = (from: Point, to: Point, colour: string) => {
    const angle = Math.atan2(to[1] - from[1], to[0] - from[0]), size = 12;
    const a: Point = [to[0] - size * Math.cos(angle - 0.45), to[1] - size * Math.sin(angle - 0.45)];
    const b: Point = [to[0] - size * Math.cos(angle + 0.45), to[1] - size * Math.sin(angle + 0.45)];
    return `<path d="${strokePath([a, to, b], hand, rand)}" fill="none" stroke="${colour}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`;
  };
  if (spec.axes) {
    const ax: Point[] = [px([minX + 0.2, 0]), px([maxX - 0.2, 0])], ay: Point[] = [px([0, minY + 0.2]), px([0, maxY - 0.2])];
    parts.push(`<path d="${strokePath(ax, hand, rand)}" fill="none" stroke="${ink.muted}" stroke-width="1.8"/>`, head(ax[0]!, ax[1]!, ink.muted),
      `<path d="${strokePath(ay, hand, rand)}" fill="none" stroke="${ink.muted}" stroke-width="1.8"/>`, head(ay[0]!, ay[1]!, ink.muted));
    layout.addPath(ax); layout.addPath(ay);
  }
  // 1. Draw every shape and record its lines, so labels can avoid them.
  type Pending = { text: string; colour: string; anchors: Point[]; prefer?: Point; gap?: number };
  const pending: Pending[] = [];
  const along = (a: Point, b: Point, t: number): Point => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  spec.items.forEach(item => {
    const type = (item.type ?? (item.text && item.at ? "text" : item.points ? "polygon" : item.center ? "circle" : item.from ? "line" : "point")).toLowerCase();
    const colour = colourOf(item.color, ink, ink.ink);
    const dash = item.dashed ? ' stroke-dasharray="7 6"' : "";
    const stroke = `fill="${item.fill ? ink.fill : "none"}" stroke="${colour}" stroke-width="${item.width ?? 2.4}" stroke-linecap="round" stroke-linejoin="round"${dash}`;
    if ((type === "line" || type === "arrow" || type === "vector" || type === "force") && item.from && item.to) {
      const a = px(item.from), b = px(item.to);
      parts.push(`<path d="${strokePath([a, b], hand, rand)}" ${stroke}/>`);
      if (type !== "line" || item.arrow) parts.push(head(a, b, colour));
      if (item.arrow === "both") parts.push(head(b, a, colour));
      layout.addPath([a, b]);
      const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      const normal: Point = [-(b[1] - a[1]) / length, (b[0] - a[0]) / length];
      const tipFirst = type !== "line";
      if (item.label) pending.push({ text: item.label, colour, prefer: normal[1] > 0 ? [-normal[0], -normal[1]] : normal,
        anchors: tipFirst ? [b, along(a, b, 0.7), along(a, b, 0.5)] : [along(a, b, 0.5), along(a, b, 0.3), along(a, b, 0.7)] });
    } else if ((type === "circle" || type === "ellipse") && item.center) {
      const c = px(item.center), rx = (item.r ?? item.rx ?? 1) * unit, ry = (item.r ?? item.ry ?? 1) * unit;
      const points: Point[] = Array.from({ length: 33 }, (_, i) => [c[0] + rx * Math.cos(i / 32 * Math.PI * 2), c[1] + ry * Math.sin(i / 32 * Math.PI * 2)]);
      parts.push(`<path d="${strokePath(points, hand, rand, false, true)}" ${stroke}/>`);
      layout.addPath(points);
      if (item.label) {
        const fitsInside = textWidth(item.label, textSize, hand) < rx * 1.6 && ry > textSize * 0.8;
        const rim = [-45, -135, 45, 135, 0, 180, -90, 90].map(deg => [c[0] + rx * Math.cos(deg * Math.PI / 180), c[1] + ry * Math.sin(deg * Math.PI / 180)] as Point);
        pending.push({ text: item.label, colour, anchors: item.fill && fitsInside ? [c, ...rim] : rim, gap: 8 });
      }
    } else if (type === "rect" && item.from && item.to) {
      const a = px(item.from), b = px(item.to);
      const corners: Point[] = [a, [b[0], a[1]], b, [a[0], b[1]]];
      parts.push(`<path d="${strokePath(corners, hand, rand, true)}" ${stroke}/>`);
      layout.addPath(corners, true);
      const cx = (a[0] + b[0]) / 2, topY = Math.min(a[1], b[1]), bottomY = Math.max(a[1], b[1]);
      if (item.label) pending.push({ text: item.label, colour, anchors: [[cx, topY], [cx, bottomY], [cx, (topY + bottomY) / 2]], prefer: [0, -1] });
    } else if ((type === "polygon" || type === "triangle") && item.points && item.points.length > 1) {
      const points = item.points.map(px);
      parts.push(`<path d="${strokePath(points, hand, rand, true)}" ${stroke}/>`);
      layout.addPath(points, true);
      if (item.label) {
        const centre: Point = [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length];
        pending.push({ text: item.label, colour, anchors: [centre, ...points], gap: 4 });
      }
    } else if ((type === "curve" || type === "path" || type === "polyline") && item.points && item.points.length > 1) {
      const points = item.points.map(px);
      parts.push(`<path d="${strokePath(points, hand, rand, false, type === "curve")}" ${stroke}/>`);
      if (item.arrow) parts.push(head(points[points.length - 2]!, points[points.length - 1]!, colour));
      layout.addPath(points);
      if (item.label) pending.push({ text: item.label, colour, anchors: [points[points.length - 1]!, points[Math.floor(points.length / 2)]!, points[0]!] });
    } else if ((type === "angle" || type === "arc") && item.at) {
      const c = px(item.at), r = (item.r ?? 0.6) * unit;
      const start = (item.start ?? 0) * Math.PI / 180, end = (item.end ?? 90) * Math.PI / 180;
      const steps = 16;
      const points: Point[] = Array.from({ length: steps + 1 }, (_, i) => {
        const t = start + (end - start) * i / steps;
        return [c[0] + r * Math.cos(t), c[1] - r * Math.sin(t)];
      });
      parts.push(`<path d="${strokePath(points, hand, rand, false, true)}" ${stroke}/>`);
      layout.addPath(points);
      const middle = (start + end) / 2, outward: Point = [Math.cos(middle), -Math.sin(middle)];
      if (item.label) pending.push({ text: item.label, colour, anchors: [[c[0] + r * outward[0], c[1] + r * outward[1]]], prefer: outward, gap: 6 });
    } else if (type === "point" && item.at) {
      const c = px(item.at);
      parts.push(`<circle cx="${c[0].toFixed(1)}" cy="${c[1].toFixed(1)}" r="4.5" fill="${colour}" stroke="${colour}"/>`);
      layout.reserve({ x: c[0] - 6, y: c[1] - 6, w: 12, h: 12 });
      if (item.label) pending.push({ text: item.label, colour, anchors: [c], gap: 8 });
    } else if (type === "text" && item.at) {
      const c = px(item.at), size = Math.round(textSize * (item.size ?? 1)), text = item.text ?? item.label ?? "";
      parts.push(write(layout.fixed(text, size, c[0], c[1], "middle"), text, colour, size));
    }
  });
  if (spec.title) parts.push(write(layout.fixed(spec.title, textSize + 3, width / 2, 28, "middle"), spec.title, ink.ink, textSize + 3, ";font-weight:600"));
  // 2. Place every label where it covers no other label and crosses as few lines as possible.
  for (const label of pending) parts.push(write(layout.place(label.text, textSize, label.anchors, { gap: label.gap ?? 7, prefer: label.prefer }), label.text, label.colour));
  // 3. Grow the board so no label is cut off at an edge.
  const extent = layout.extent();
  const vx = Math.min(0, Math.floor((extent?.x ?? 0) - 8)), vy = Math.min(0, Math.floor((extent?.y ?? 0) - 8));
  const vw = Math.max(width, Math.ceil((extent ? extent.x + extent.w : 0) + 8)) - vx, vh = Math.max(height, Math.ceil((extent ? extent.y + extent.h : 0) + 8)) - vy;
  return { svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${vw}" height="${vh}" viewBox="${vx} ${vy} ${vw} ${vh}">${parts.join("")}</svg>`, width: vw, height: vh };
}

// --- cards ------------------------------------------------------------------------------------

const seen = new Set<string>();

function card(kind: "plot" | "sketch", source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement {
  const figure = document.createElement("figure");
  figure.className = "diagram";
  let title = "";
  let build: (style: BoardStyle, dark: boolean) => { svg: string; width: number; height: number };
  try {
    if (kind === "plot") {
      const spec = parsePlot(source);
      title = spec.title;
      build = (style, dark) => plotSvg(spec, style, dark);
    } else {
      const spec = parseSketch(source);
      title = spec.title ?? "";
      build = (style, dark) => sketchSvg(spec, style, dark);
    }
  } catch (error) {
    const note = document.createElement("p");
    note.className = "diagram-error";
    note.textContent = `This ${kind} could not be drawn (${error instanceof Error ? error.message : "invalid"}). Its source is shown instead.`;
    figure.append(note, fallback(source, kind));
    return figure;
  }
  const first = !seen.has(source);
  seen.add(source);
  drawingCard(figure, {
    title, source, sourceLabel: "Copy source", name: kind, animateFirst: first,
    draw: async (style, animate) => {
      const result = build(style, isDark());
      const font = style === "clean" ? "" : await handwritingFont();
      return { ...result, svg: finishSvg(result.svg, style, font, animate) };
    },
    onError: error => {
      const note = document.createElement("p");
      note.className = "diagram-error";
      note.textContent = `This ${kind} could not be drawn (${error instanceof Error ? error.message : "invalid"}).`;
      figure.replaceChildren(note, fallback(source, kind));
    },
  });
  return figure;
}

export function renderPlot(source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement { return card("plot", source, fallback); }
export function renderSketch(source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement { return card("sketch", source, fallback); }
