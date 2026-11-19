// Label placement for plots and sketches: every label is put where it overlaps no other label
// and crosses as few lines as possible, near what it names. Labels then get a thin halo in the
// board colour, so a line that must pass behind one never cuts through its letters.

export type Point = [number, number];
export interface Box { x: number; y: number; w: number; h: number }
export interface Placed { x: number; y: number; anchor: "start" | "middle" | "end"; box: Box }

/** Approximate width of `text` at `size` px: handwriting is narrower than the clean sans-serif. */
export function textWidth(text: string, size: number, hand: boolean): number {
  let units = 0;
  for (const char of text) {
    units += /[A-Z0-9%]/.test(char) ? 0.6 : /[mwMW]/.test(char) ? 0.78 : /[il.,:;'|!]/.test(char) ? 0.28 : char === " " ? 0.3 : 0.52;
  }
  return units * size * (hand ? 0.86 : 1);
}

/** Does the segment a–b pass through the box (Liang–Barsky clipping)? */
export function segmentHitsBox(a: Point, b: Point, box: Box): boolean {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  let t0 = 0, t1 = 1;
  const edges: [number, number][] = [[-dx, a[0] - box.x], [dx, box.x + box.w - a[0]], [-dy, a[1] - box.y], [dy, box.y + box.h - a[1]]];
  for (const [p, q] of edges) {
    if (p === 0) { if (q < 0) return false; continue; }
    const t = q / p;
    if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t; }
    else { if (t < t0) return false; if (t < t1) t1 = t; }
  }
  return true;
}

function overlap(a: Box, b: Box): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Directions to try around an anchor, as unit vectors: right, above-right, above, … */
const DIRECTIONS: Point[] = [[1, -1], [1, 0], [1, 1], [0, -1], [0, 1], [-1, -1], [-1, 0], [-1, 1]].map(([x, y]) => {
  const length = Math.hypot(x!, y!);
  return [x! / length, y! / length] as Point;
});

export class LabelLayout {
  private readonly boxes: Box[] = [];
  private readonly segments: [Point, Point][] = [];

  /** `bounds`: where labels must stay (plots), or null to let the drawing grow to fit them (sketches). */
  constructor(private readonly bounds: Box | null, private readonly hand: boolean) {}

  /** Lines labels should avoid crossing. */
  addPath(points: Point[], closed = false): void {
    for (let i = 1; i < points.length; i++) this.segments.push([points[i - 1]!, points[i]!]);
    if (closed && points.length > 2) this.segments.push([points[points.length - 1]!, points[0]!]);
  }

  /** Space already taken (a title, a tick label, a point marker). */
  reserve(box: Box): void { this.boxes.push(box); }

  /** The box a label would have with its baseline at (x, y). */
  boxFor(text: string, size: number, x: number, y: number, anchor: Placed["anchor"]): Box {
    const w = textWidth(text, size, this.hand) + 4, h = size * 1.05;
    const left = anchor === "start" ? x - 2 : anchor === "end" ? x - w + 2 : x - w / 2;
    return { x: left, y: y - size * 0.82, w, h };
  }

  /** Reserve a label that must stay exactly where it is (free text the author placed). */
  fixed(text: string, size: number, x: number, y: number, anchor: Placed["anchor"]): Placed {
    const box = this.boxFor(text, size, x, y, anchor);
    this.boxes.push(box);
    return { x, y, anchor, box };
  }

  /** Cost of putting a label in `box`: overlaps first, then crossed lines, then leaving the area. */
  private cost(box: Box): number {
    let cost = 0;
    for (const other of this.boxes) cost += overlap(box, other) / Math.max(1, Math.min(box.w * box.h, other.w * other.h)) * 1000;
    const inner = { x: box.x + 1, y: box.y + 2, w: box.w - 2, h: box.h - 4 };
    for (const [a, b] of this.segments) {
      if (Math.max(a[0], b[0]) < inner.x || Math.min(a[0], b[0]) > inner.x + inner.w || Math.max(a[1], b[1]) < inner.y || Math.min(a[1], b[1]) > inner.y + inner.h) continue;
      if (segmentHitsBox(a, b, inner)) cost += 40;
    }
    if (this.bounds) {
      const outside = Math.max(0, this.bounds.x - box.x) + Math.max(0, box.x + box.w - (this.bounds.x + this.bounds.w))
        + Math.max(0, this.bounds.y - box.y) + Math.max(0, box.y + box.h - (this.bounds.y + this.bounds.h));
      cost += outside * 60;
    }
    return cost;
  }

  /**
   * Place a label near one of `anchors` (in order of preference), trying every side at a few
   * distances. `gap` is how far from the anchor the nearest candidate starts.
   */
  place(text: string, size: number, anchors: Point[], options: { gap?: number | undefined; prefer?: Point | undefined } = {}): Placed {
    const gap = options.gap ?? 7;
    const directions = options.prefer ? [options.prefer, ...DIRECTIONS] : DIRECTIONS;
    let best: Placed | null = null, bestCost = Infinity;
    anchors.forEach((anchor, rank) => {
      for (const distance of [gap, gap + 10, gap + 22]) {
        directions.forEach((direction, order) => {
          const x = anchor[0] + direction[0] * distance, y = anchor[1] + direction[1] * distance;
          const placement: Placed["anchor"] = direction[0] > 0.3 ? "start" : direction[0] < -0.3 ? "end" : "middle";
          // Baseline so the text sits beside the point in the chosen direction.
          const baseline = direction[1] < -0.3 ? y : direction[1] > 0.3 ? y + size * 0.8 : y + size * 0.32;
          const box = this.boxFor(text, size, x, baseline, placement);
          const cost = this.cost(box) + distance * 0.35 + rank * 6 + order * 0.8;
          if (cost < bestCost) { bestCost = cost; best = { x, y: baseline, anchor: placement, box }; }
        });
      }
    });
    this.boxes.push(best!.box);
    return best!;
  }

  /** The area covered by every label placed so far (to grow a drawing so none is cut off). */
  extent(): Box | null {
    if (!this.boxes.length) return null;
    const x = Math.min(...this.boxes.map(b => b.x)), y = Math.min(...this.boxes.map(b => b.y));
    return { x, y, w: Math.max(...this.boxes.map(b => b.x + b.w)) - x, h: Math.max(...this.boxes.map(b => b.y + b.h)) - y };
  }
}

/** SVG text with a halo in the board colour, so lines passing behind never cross the letters. */
export function haloText(placed: { x: number; y: number; anchor: string }, text: string, colour: string, board: string,
                         style: string, extra = ""): string {
  return `<text x="${placed.x.toFixed(1)}" y="${placed.y.toFixed(1)}" text-anchor="${placed.anchor}" fill="${colour}" stroke="${board}" ` +
    `stroke-width="4" stroke-linejoin="round" paint-order="stroke" style="${style}"${extra}>${text}</text>`;
}
