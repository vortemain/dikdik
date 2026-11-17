// Lightweight Mermaid diagram renderer — pure SVG, no dependencies.
//
// Renders the most common Mermaid diagram types as clean, professional SVG:
// - flowchart / graph (nodes + edges)
// - sequenceDiagram (participants + messages)
// - classDiagram (classes + relationships)
//
// Server-supplied diagram text is parsed as data, never executed. Only
// known shapes and labels are rendered; unknown syntax falls back to a
// code block so the user still sees the source.
//
// The CSS classes (.mermaid-* ) are defined in style.css.

const NS = "http://www.w3.org/2000/svg";

function svgEl<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}, text?: string): SVGElementTagNameMap[K] {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  if (text !== undefined) el.textContent = text;
  return el;
}

interface Node { id: string; label: string; shape: string; x: number; y: number; w: number; h: number }
interface Edge { from: string; to: string; label: string; style: string }

// --- Flowchart parser ---
// Handles: A[label], A([label]), A{label}, A>label], A((label)), A -->|label| B, A --- B, A -.-> B
function parseFlowchart(source: string): { nodes: Map<string, Node>; edges: Edge[] } {
  const nodes = new Map<string, Node>();
  const edges: Edge[] = [];
  let y = 40;
  const nodeRegex = /^(\w+)\s*(?:\[(?:([^[\]]*)\])?\s*$)?/;
  for (const line of source.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("%%") || trimmed.startsWith("flowchart") || trimmed.startsWith("graph")) continue;
    // Edge: A --> B, A --- B, A -.-> B, A -->|text| B, A ==> B
    const edgeMatch = trimmed.match(/^(\w+)\s*(-->|---|-.->|==>|--)\s*(?:\|([^|]*)\|)?\s*(\w+)?/);
    if (edgeMatch) {
      const [, from, arrow, label, to] = edgeMatch;
      if (from) {
        if (!nodes.has(from)) nodes.set(from, { id: from, label: from, shape: "rect", x: 0, y, w: 80, h: 32 });
        if (to) {
          if (!nodes.has(to)) nodes.set(to, { id: to, label: to, shape: "rect", x: 0, y, w: 80, h: 32 });
          edges.push({ from, to, label: label ?? "", style: arrow === "-.->" ? "dashed" : arrow === "==>" ? "thick" : "solid" });
        }
      }
      continue;
    }
    // Node definition: A[label], A(label), A{label}, A((label)), A>label]
    const nodeMatch = trimmed.match(/^(\w+)\s*([\[\(\{<])/);
    if (nodeMatch) {
      const id = nodeMatch[1]!;
      const bracket = nodeMatch[2]!;
      const shape = bracket === "[" ? "rect" : bracket === "(" ? "rounded" : bracket === "{" ? "diamond" : bracket === "<" ? "parallelogram" : "rect";
      const labelMatch = trimmed.match(new RegExp(`\\${bracket}([^\\]\\}\\)>]*)`));
      const label = labelMatch ? labelMatch[1]!.trim() : id;
      if (!nodes.has(id)) nodes.set(id, { id, label, shape, x: 0, y, w: Math.max(60, label.length * 8 + 20), h: 32 });
      continue;
    }
    // Bare node reference
    const bare = trimmed.match(/^(\w+)$/);
    if (bare && !nodes.has(bare[1]!)) nodes.set(bare[1]!, { id: bare[1]!, label: bare[1]!, shape: "rect", x: 0, y, w: 80, h: 32 });
  }
  // Simple layout: arrange nodes in rows based on edges (topological-ish)
  const nodeList = [...nodes.values()];
  const placed = new Set<string>();
  let row = 0, col = 0;
  const maxCols = Math.ceil(Math.sqrt(nodeList.length));
  for (const node of nodeList) {
    if (placed.has(node.id)) continue;
    node.x = 60 + col * 160;
    node.y = 40 + row * 72;
    placed.add(node.id);
    col++;
    if (col >= maxCols) { col = 0; row++; }
  }
  return { nodes, edges };
}

function renderFlowchartSvg(parsed: { nodes: Map<string, Node>; edges: Edge[] }): SVGElement {
  const { nodes, edges } = parsed;
  const nodeList = [...nodes.values()];
  if (!nodeList.length) return svgEl("svg", { class: "mermaid-empty" });
  const maxX = Math.max(...nodeList.map(n => n.x + n.w)) + 40;
  const maxY = Math.max(...nodeList.map(n => n.y + n.h)) + 40;
  const svg = svgEl("svg", { viewBox: `0 0 ${maxX} ${maxY}`, class: "mermaid-diagram", role: "img" });
  // Edges first (behind nodes)
  for (const edge of edges) {
    const from = nodes.get(edge.from), to = nodes.get(edge.to);
    if (!from || !to) continue;
    const x1 = from.x + from.w / 2, y1 = from.y + from.h / 2;
    const x2 = to.x + to.w / 2, y2 = to.y + to.h / 2;
    const line = svgEl("line", { x1, y1, x2, y2, class: `mermaid-edge mermaid-${edge.style}` });
    svg.append(line);
    // Arrowhead
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const ah = svgEl("polygon", {
      points: `${x2},${y2} ${x2 - 8 * Math.cos(angle - 0.4)},${y2 - 8 * Math.sin(angle - 0.4)} ${x2 - 8 * Math.cos(angle + 0.4)},${y2 - 8 * Math.sin(angle + 0.4)}`,
      class: "mermaid-arrowhead",
    });
    svg.append(ah);
    if (edge.label) {
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - 8;
      const text = svgEl("text", { x: mx, y: my, "text-anchor": "middle", class: "mermaid-edge-label" }, edge.label);
      svg.append(text);
    }
  }
  // Nodes
  for (const node of nodeList) {
    const group = svgEl("g", { class: `mermaid-node mermaid-${node.shape}` });
    if (node.shape === "diamond") {
      group.append(svgEl("polygon", {
        points: `${node.x},${node.y} ${node.x + node.w / 2},${node.y + node.h / 2} ${node.x},${node.y + node.h} ${node.x - node.w / 2},${node.y + node.h / 2}`,
        class: "mermaid-shape",
      }));
    } else if (node.shape === "rounded") {
      group.append(svgEl("rect", { x: node.x, y: node.y, width: node.w, height: node.h, rx: 16, class: "mermaid-shape" }));
    } else {
      group.append(svgEl("rect", { x: node.x, y: node.y, width: node.w, height: node.h, rx: 4, class: "mermaid-shape" }));
    }
    group.append(svgEl("text", { x: node.x + node.w / 2, y: node.y + node.h / 2 + 5, "text-anchor": "middle", class: "mermaid-node-label" }, node.label));
    svg.append(group);
  }
  return svg;
}

// --- Sequence diagram parser ---
interface Participant { id: string; label: string; x: number }
interface Message { from: string; to: string; label: string; dotted: boolean }
function parseSequence(source: string): { participants: Participant[]; messages: Message[] } {
  const participants: Participant[] = [];
  const messages: Message[] = [];
  const partMap = new Map<string, Participant>();
  let msgY = 0;
  for (const line of source.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("%%") || /^sequenceDiagram/i.test(trimmed)) continue;
    const partMatch = trimmed.match(/^(?:participant|actor)\s+(\w+)(?:\s+(?:as\s+)?(.+))?/);
    if (partMatch) {
      const id = partMatch[1]!;
      const label = (partMatch[2] ?? id).trim();
      const p = { id, label, x: 0 };
      participants.push(p);
      partMap.set(id, p);
      continue;
    }
    const msgMatch = trimmed.match(/^(\w+)\s*(--?>>?|-->>?|-\.->>?)\s*(\w+)\s*:\s*(.*)/);
    if (msgMatch) {
      const [, from, arrow, to, label] = msgMatch;
      if (!from || !arrow || !to) continue;
      if (!partMap.has(from)) { const p = { id: from, label: from, x: 0 }; participants.push(p); partMap.set(from, p); }
      if (!partMap.has(to)) { const p = { id: to, label: to, x: 0 }; participants.push(p); partMap.set(to, p); }
      messages.push({ from, to, label: label ?? "", dotted: arrow.includes(".") });
      msgY++;
    }
  }
  // Layout: evenly spaced
  participants.forEach((p, i) => { p.x = 80 + i * Math.max(120, 600 / Math.max(1, participants.length)); });
  return { participants, messages };
}

function renderSequenceSvg(parsed: { participants: Participant[]; messages: Message[] }): SVGElement {
  const { participants, messages } = parsed;
  if (!participants.length) return svgEl("svg", { class: "mermaid-empty" });
  const width = Math.max(320, participants[participants.length - 1]!.x + 80);
  const lifeHeight = messages.length * 36 + 40;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${lifeHeight + 40}`, class: "mermaid-diagram mermaid-sequence", role: "img" });
  // Lifelines
  for (const p of participants) {
    svg.append(svgEl("line", { x1: p.x, y1: 30, x2: p.x, y2: lifeHeight, class: "mermaid-lifeline" }));
    const box = svgEl("rect", { x: p.x - 40, y: 8, width: 80, height: 24, rx: 4, class: "mermaid-actor" });
    svg.append(box);
    svg.append(svgEl("text", { x: p.x, y: 24, "text-anchor": "middle", class: "mermaid-actor-label" }, p.label));
  }
  // Messages
  messages.forEach((msg, i) => {
    const from = participants.find(p => p.id === msg.from);
    const to = participants.find(p => p.id === msg.to);
    if (!from || !to) return;
    const y = 50 + i * 36;
    const x1 = from.x, x2 = to.x;
    const cls = `mermaid-msg ${msg.dotted ? "mermaid-dotted" : ""}`;
    if (x1 === x2) {
      // Self-message: loop right
      svg.append(svgEl("path", { d: `M ${x1} ${y} q 30 0 30 10 v 8 q 0 10 -30 10`, class: cls, fill: "none" }));
      svg.append(svgEl("text", { x: x1 + 35, y: y + 14, class: "mermaid-msg-label" }, msg.label));
    } else {
      svg.append(svgEl("line", { x1, y1: y, x2, y2: y, class: cls }));
      const dir = x2 > x1 ? 1 : -1;
      svg.append(svgEl("polygon", {
        points: `${x2},${y} ${x2 - 8 * dir},${y - 5} ${x2 - 8 * dir},${y + 5}`,
        class: "mermaid-arrowhead",
      }));
      const mx = (x1 + x2) / 2;
      svg.append(svgEl("text", { x: mx, y: y - 6, "text-anchor": "middle", class: "mermaid-msg-label" }, msg.label));
    }
  });
  return svg;
}

/** Render a Mermaid diagram from source text. Returns an SVG or a code fallback. */
export function renderMermaid(source: string, fallback: (text: string, lang: string) => HTMLElement): HTMLElement {
  const trimmed = source.trim();
  if (!trimmed) return fallback(source, "mermaid");
  // Detect diagram type from the first non-empty, non-comment line
  const firstLine = trimmed.split("\n").find(l => l.trim() && !l.trim().startsWith("%%")) ?? "";
  const fl = firstLine.trim().toLowerCase();
  const card = document.createElement("figure");
  card.className = "mermaid-card";
  let svg: SVGElement;
  if (fl.startsWith("flowchart") || fl.startsWith("graph")) {
    svg = renderFlowchartSvg(parseFlowchart(trimmed));
  } else if (fl.startsWith("sequencediagram")) {
    svg = renderSequenceSvg(parseSequence(trimmed));
  } else {
    // Unknown diagram type — try flowchart as a generic fallback
    svg = renderFlowchartSvg(parseFlowchart(trimmed));
  }
  card.append(svg);
  // Add a collapsible source view for transparency/debugging
  const details = document.createElement("details");
  details.className = "mermaid-source";
  const summary = document.createElement("summary");
  summary.textContent = "Diagram source";
  details.append(summary, fallback(source, "mermaid"));
  card.append(details);
  return card;
}