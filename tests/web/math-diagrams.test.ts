import { expect, test } from "bun:test";
import { liftMath } from "../../dikdik/web/rich-text";
import { collectDisplay } from "../../dikdik/web/math";
import { normaliseSource, quoteLabels } from "../../dikdik/web/mermaid";

const formulas = (text: string) => liftMath(text).formulas;

test("inline formulas are found in every common notation", () => {
  expect(formulas("Energy $E = mc^2$ and \\(a^2 + b^2 = c^2\\) and $$x_1$$.")).toEqual(["E = mc^2", "a^2 + b^2 = c^2", "x_1"]);
  expect(formulas("Roots of $ax^2+bx+c=0$ use $\\Delta$.")).toEqual(["ax^2+bx+c=0", "\\Delta"]);
  expect(formulas("Digits first: $2x + 1 = 5$ works.")).toEqual(["2x + 1 = 5"]);
});

test("prices, shell variables and code are never typeset", () => {
  expect(formulas("It costs $5 and $10 today.")).toEqual([]);
  expect(formulas("Between $5 to $20.")).toEqual([]);
  expect(formulas("Set `$HOME` and `$x$` in code.")).toEqual([]);
  expect(formulas("Earned $3,000 last year.")).toEqual([]);
  expect(formulas("Escaped \\$5 stays.")).toEqual([]);
});

test("formulas are lifted before Markdown so underscores and stars are not emphasis", () => {
  const { text, formulas: found } = liftMath("Compare $a_i * b_j$ with $c_k$.");
  expect(found).toEqual(["a_i * b_j", "c_k"]);
  expect(text).not.toContain("_");
  expect(text).not.toContain("*");
});

test("display formulas: $$ blocks, \\[ \\] blocks and LaTeX environments", () => {
  expect(collectDisplay(["$$", "x = \\frac{-b}{2a}", "$$"], 0)).toEqual({ tex: "\nx = \\frac{-b}{2a}\n", end: 2 });
  expect(collectDisplay(["$$E = mc^2$$"], 0)).toEqual({ tex: "E = mc^2", end: 0 });
  expect(collectDisplay(["\\[", "\\int_0^1 x\\,dx", "\\]"], 0)?.end).toBe(2);
  const aligned = collectDisplay(["\\begin{align}", "a &= b \\\\", "c &= d", "\\end{align}"], 0);
  expect(aligned?.end).toBe(3);
  expect(aligned?.tex.startsWith("\\begin{align}")).toBe(true);
  expect(collectDisplay(["$$", "still streaming"], 0)).toBeNull();
  expect(collectDisplay(["plain text"], 0)).toBeNull();
});

test("diagram sources are tidied and fragile labels quoted on retry", () => {
  expect(normaliseSource("flowchart\n  A[“Start”] –> B")).toBe('flowchart TD\n  A["Start"] -> B');
  const repaired = quoteLabels("flowchart TD\n  A[Sunlight (energy)] --> B{Is x > 0?}\n  B -->|yes (true)| C[Done]\n  D[(Database)] --> E([Stadium])");
  expect(repaired).toContain('A["Sunlight (energy)"]');
  expect(repaired).toContain('B{"Is x > 0?"}');
  expect(repaired).toContain('|"yes (true)"|');
  expect(repaired).toContain("C[Done]");
  expect(repaired).toContain("D[(Database)]");  // cylinder shape untouched
  expect(repaired).toContain("E([Stadium])");   // stadium shape untouched
  expect(quoteLabels("sequenceDiagram\n  A->>B: hi (x)")).toBe("sequenceDiagram\n  A->>B: hi (x)");
});

test("text after a closing $$ stays as a paragraph and does not swallow later lines", () => {
  const lines = ["$$2(3)^2 - 12 - 6 = 0$$ ✓", "Both work.", "$$x^2 - 3x = 10$$"];
  expect(collectDisplay(lines, 0)).toEqual({ tex: "2(3)^2 - 12 - 6 = 0", end: 0, rest: "✓" });
  expect(collectDisplay(lines, 2)).toEqual({ tex: "x^2 - 3x = 10", end: 2 });
});
