import { expect, test } from "bun:test";
import { diagramCss, normaliseSource, recolourJourney, themeFor } from "../../dikdik/web/mermaid";

function luminance(hex: string): number {
  const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(1 + i, 3 + i), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
const contrast = (a: string, b: string) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x! + 0.05) / (y! + 0.05); };
const themes = [["chalkboard", false], ["whiteboard", false], ["clean", false], ["clean", true]] as const;

test("every diagram text colour reads clearly on the fill behind it, in every style and theme", () => {
  for (const [style, dark] of themes) {
    const v = themeFor(style, dark);
    const pairs: [string, string, string][] = [
      ...Array.from({ length: 12 }, (_, i) => [`section ${i}`, v[`cScaleLabel${i}`]!, v[`cScale${i}`]!] as [string, string, string]),
      ["mind-map centre", v.gitBranchLabel0!, v.git0!],
      ["Gantt task", v.taskTextColor!, v.taskBkgColor!],
      ...[1, 2, 3, 4].map(n => [`quadrant ${n}`, v[`quadrant${n}TextFill`]!, v[`quadrant${n}Fill`]!] as [string, string, string]),
      ["text on board", v.textColor!, v.background!],
    ];
    for (const [what, text, fill] of pairs) {
      if (!/^#[0-9a-f]{6}$/i.test(text) || !/^#[0-9a-f]{6}$/i.test(fill)) continue;
      expect({ style, dark, what, ratio: Math.round(contrast(text, fill) * 10) / 10 >= 4.5 }).toEqual({ style, dark, what, ratio: true });
    }
  }
});

test("label boxes on lines are solid board colour, never see-through", () => {
  for (const [style, dark] of themes) {
    const css = diagramCss(style, dark);
    expect(css).toContain("opacity:1!important");
    expect(css).toContain(themeFor(style, dark).background!);
  }
});

test("journeys use the board's colours instead of Mermaid's neon defaults", () => {
  const svg = '<circle fill="#8FBC8F"/><circle fill="#7CFC00"/><circle fill="#00FFFF"/>';
  const recoloured = recolourJourney(svg, "chalkboard", false);
  expect(recoloured).not.toMatch(/#7CFC00|#00FFFF/i);
  expect(recoloured).toContain(themeFor("chalkboard", false).pie2!);
});

test("mind-map circles become rounded boxes that fit their words; other diagrams are untouched", () => {
  expect(normaliseSource("mindmap\n  root((Photosynthesis))\n    Inputs")).toBe("mindmap\n  root(Photosynthesis)\n    Inputs");
  expect(normaliseSource("flowchart TD\n  A((Start)) --> B")).toBe("flowchart TD\n  A((Start)) --> B");
});
