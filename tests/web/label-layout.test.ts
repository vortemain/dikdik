import { expect, test } from "bun:test";
import { LabelLayout, segmentHitsBox, textWidth, type Box } from "../../dikdik/web/label-layout";
import { parsePlot, parseSketch, plotSvg, sketchSvg } from "../../dikdik/web/sketch";

const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("segment and box intersection", () => {
  const box = { x: 10, y: 10, w: 20, h: 10 };
  expect(segmentHitsBox([0, 15], [40, 15], box)).toBe(true);     // straight through
  expect(segmentHitsBox([0, 0], [40, 0], box)).toBe(false);      // passes above
  expect(segmentHitsBox([15, 12], [16, 13], box)).toBe(true);    // wholly inside
  expect(segmentHitsBox([0, 30], [30, 25], box)).toBe(false);    // passes below
});

test("text width grows with the text and handwriting is narrower", () => {
  expect(textWidth("Mitochondrion", 20, false)).toBeGreaterThan(textWidth("Nucleus", 20, false));
  expect(textWidth("Nucleus", 20, true)).toBeLessThan(textWidth("Nucleus", 20, false));
});

test("labels never land on each other, and steer clear of lines", () => {
  const layout = new LabelLayout({ x: 0, y: 0, w: 400, h: 300 }, true);
  layout.addPath([[0, 150], [400, 150]]);                          // a line right through the anchor
  const placed = ["Normal force N", "Friction f", "Weight mg = 49 N", "m = 5 kg"].map(text => layout.place(text, 20, [[200, 150]]));
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) expect(overlaps(placed[i]!.box, placed[j]!.box)).toBe(false);
    expect(segmentHitsBox([0, 150], [400, 150], { ...placed[i]!.box, y: placed[i]!.box.y + 2, h: placed[i]!.box.h - 4 })).toBe(false);
  }
});

test("labels stay inside a plot's area", () => {
  const layout = new LabelLayout({ x: 0, y: 0, w: 300, h: 200 }, false);
  const placed = layout.place("A long label near the edge", 14, [[295, 5]]);
  expect(placed.box.x).toBeGreaterThanOrEqual(0);
  expect(placed.box.x + placed.box.w).toBeLessThanOrEqual(300);
  expect(placed.box.y).toBeGreaterThanOrEqual(0);
});

test("a sketch grows so a label at its edge is never cut off", () => {
  const spec = parseSketch('{"items": [{"type": "line", "from": [0,0], "to": [10,0]}, {"type": "point", "at": [10,0], "label": "Range R (maximum distance)"}]}');
  for (const style of ["chalkboard", "whiteboard", "clean"] as const) {
    const { svg, width } = sketchSvg(spec, style, false);
    const [vx, , vw] = /viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"/.exec(svg)!.slice(1).map(Number);
    const label = /<text x="([\d.]+)"[^>]*text-anchor="(\w+)"[^>]*>Range R/.exec(svg)!;
    const size = style === "clean" ? 14 : 20;
    const x = Number(label[1]), right = label[2] === "start" ? x + textWidth("Range R (maximum distance)", size, style !== "clean") : x;
    expect(right).toBeLessThanOrEqual(vx! + vw! + 1);
    expect(width).toBe(vw!);
  }
});

test("every label has a halo in the board colour, so lines never cut through letters", () => {
  const plot = plotSvg(parsePlot("y = x^2\nx: -3..3\npoint: (1, 1) here"), "chalkboard", false).svg;
  const labels = plot.match(/<text [^>]*>/g)!;
  expect(labels.length).toBeGreaterThan(5);
  for (const label of labels) expect(label).toContain('paint-order="stroke"');
});
