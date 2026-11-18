import { expect, test } from "bun:test";
import { evaluate, parseExpression, parsePlot, parseSketch, plotSvg, prettyFormula, sketchSvg } from "../../dikdik/web/sketch";
import { finishSvg, palette } from "../../dikdik/web/board";

const at = (expression: string, x: number) => evaluate(parseExpression(expression), x);

test("the plot parser evaluates real maths without eval", () => {
  expect(at("x^2 - 4", 3)).toBe(5);
  expect(at("2x + 1", 2)).toBe(5);
  expect(at("2(x+1)", 1)).toBe(4);
  expect(at("(x+1)(x-1)", 3)).toBe(8);
  expect(at("-x^2", 3)).toBe(-9);           // unary minus binds looser than ^
  expect(at("2^3^2", 0)).toBe(512);         // ^ is right-associative
  expect(at("sin(pi/2)", 0)).toBeCloseTo(1);
  expect(at("3pi", 0)).toBeCloseTo(3 * Math.PI);
  expect(at("sqrt(x) + abs(-2)", 9)).toBe(5);
  expect(at("ln(e^2)", 0)).toBeCloseTo(2);
  expect(at("|x - 5|", 2)).toBe(3);
  expect(at("x sin(x)", 0)).toBe(0);
  expect(at("\\frac{1}{x}", 4)).toBe(0.25);
  expect(() => parseExpression("alert(1)")).toThrow();
  expect(() => parseExpression("x; fetch")).toThrow();
  expect(() => parseExpression("y + 1")).toThrow(/Unknown name/);
});

test("plot specifications read functions, ranges, points and options", () => {
  const spec = parsePlot("title: Parabola\ny = x^2 - 4\nf(x) = 2x + 1 | label: tangent\nx: -4..4\ny: -6 to 10\npoint: (2, 0) root, (-2, 0) root\nx = 1 | label: axis\ngrid: off");
  expect(spec.title).toBe("Parabola");
  expect(spec.functions.map(f => f.expression)).toEqual(["x^2 - 4", "2x + 1"]);
  expect(spec.functions[1]!.label).toBe("tangent");
  expect(spec.x).toEqual([-4, 4]);
  expect(spec.y).toEqual([-6, 10]);
  expect(spec.points).toEqual([{ x: 2, y: 0, label: "root" }, { x: -2, y: 0, label: "root" }]);
  expect(spec.vertical).toEqual([{ x: 1, label: "axis" }]);
  expect(spec.grid).toBe(false);
  const trig = parsePlot("y = sin(x)\nx: -2pi..2pi");
  expect(trig.piAxis).toBe(true);
  expect(trig.x[1]).toBeCloseTo(2 * Math.PI);
  const json = parsePlot('{"functions": ["y = x^3"], "x": [-2, 2], "title": "Cubic"}');
  expect(json.functions[0]!.expression).toBe("x^3");
  expect(() => parsePlot("title: nothing")).toThrow();
});

test("plots and sketches render well-formed SVG with every style", () => {
  const spec = parsePlot("y = 1/x\nx: -3..3");  // an asymptote must break the curve, not draw a wall
  for (const style of ["chalkboard", "whiteboard", "clean"] as const) {
    const { svg, width, height } = plotSvg(spec, style, false);
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
    expect(svg.startsWith("<svg")).toBe(true);
    expect(svg).not.toContain("NaN");
    expect(svg).not.toContain("Infinity");
    const sketch = sketchSvg(parseSketch('{"items": [{"type": "vector", "from": [0,0], "to": [2,1], "label": "F"}, {"type": "circle", "center": [3,3], "r": 1}, {"type": "angle", "at": [0,0], "end": 30, "label": "θ"}, {"type": "text", "at": [1,4], "text": "a < b & c"}]}'), style, false);
    expect(sketch.svg).toContain("a &lt; b &amp; c");  // text is escaped, never markup
    expect(sketch.svg).not.toContain("NaN");
  }
  expect(() => parseSketch('{"items": []}')).toThrow();
});

test("sketch JSON with common model slips is repaired, not rejected", () => {
  // Seen in a live lesson: "from":[a,b],[c,d] with the "to" key missing, plus a trailing comma.
  const spec = parseSketch('{"items": [{"type":"line","from":[0,2],[10,2],"label":"ground"}, {"type":"point","at":[1,2]},]}');
  expect(spec.items[0]!.to).toEqual([10, 2]);
  expect(spec.items).toHaveLength(2);
  expect(() => parseSketch("{not json")).toThrow();
});

test("board formulas read like handwriting", () => {
  expect(prettyFormula("y = x^2 - 4")).toBe("y = x² − 4");
  expect(prettyFormula("y = 2*x + sqrt(x)")).toBe("y = 2·x + √(x)");
  expect(prettyFormula("y = x^(-1) + pi")).toBe("y = x⁻¹ + π");
});

test("finished drawings embed the font and animate fail-safe", () => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0" style="stroke:red"/><rect data-static="1"/><text>a</text></svg>';
  const out = finishSvg(svg, "chalkboard", "data:font/woff2;base64,AAAA", true);
  expect(out).toContain("@font-face");
  expect(out).toContain('style="--i:0;stroke:red"');   // no duplicate style attribute
  expect(out).toContain('<rect data-static="1"/>');      // backgrounds are not animated
  expect(out).toContain("backwards");                    // hidden only while the animation runs
  expect(out).not.toMatch(/opacity:0;animation/);
  expect(finishSvg(svg, "clean", "", false)).toBe(svg);
  expect(palette("chalkboard", false).board).toBe("#2c4a3c");
});
