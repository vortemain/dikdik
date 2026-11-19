import { expect, test } from "bun:test";
import { boardLines, liftMath } from "../../dikdik/web/rich-text";
import { chemistryTex, collectDisplay } from "../../dikdik/web/math";
import katex from "katex";
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

test("a bare number in dollars is maths, but prices stay prices", () => {
  expect(formulas("each carbon uses $1$ bond, leaving $3$ bonds ($3+3=6$)")).toEqual(["1", "3", "3+3=6"]);
  expect(formulas("It costs $5 and $10 for two.")).toEqual([]);
  expect(formulas("Pay $5$10 later")).toEqual([]);
});

test("chemistry written as maths is typeset as chemistry", () => {
  expect(chemistryTex("CH_3-CH_3")).toBe(String.raw`\ce{CH3-CH3}`);
  expect(chemistryTex("CH_3-CH_2-OH")).toBe(String.raw`\ce{CH3-CH2-OH}`);
  expect(chemistryTex("C_3H_8")).toBe(String.raw`\ce{C3H8}`);
  expect(chemistryTex("H_2O")).toBe(String.raw`\ce{H2O}`);
  expect(chemistryTex("NaCl")).toBe(String.raw`\ce{NaCl}`);
  expect(chemistryTex("-COOH")).toBe(String.raw`\ce{-COOH}`);     // functional groups
  expect(chemistryTex("Cl")).toBe(String.raw`\ce{Cl}`);
  expect(chemistryTex("2H_2+O_2")).toBe(String.raw`\ce{2H2+O2}`);
  expect(chemistryTex("C_nH_{2n+2}")).toBe(String.raw`\mathrm{C}_n\mathrm{H}_{2n+2}`);  // general formula
  // Physics and algebra are left alone.
  for (const tex of ["E = mc^2", "F=ma", "V = IR", "PV = nRT", "x^2", "AB", "A+B", "v_x", "R", "H", "H_0", "H_1", "x_1", "C_2", "V_2", "P_1V_1=P_2V_2", "H=2(3)+2=8", "O", "N"]) {
    expect(chemistryTex(tex)).toBeNull();
  }
  // mhchem is loaded, so \ce{} typesets instead of failing.
  expect(() => katex.renderToString(String.raw`\ce{CH3-CH2-OH}`, { throwOnError: true })).not.toThrow();
  expect(() => katex.renderToString(String.raw`\ce{2H2 + O2 -> 2H2O}`, { throwOnError: true })).not.toThrow();
});

test("each board line stays its own step, while formulas, fences and tables stay whole", () => {
  expect(boardLines("Problem: propane\nStep 1: n = 3\nStep 2: $C_3H_8$")).toBe("Problem: propane\n\nStep 1: n = 3\n\nStep 2: $C_3H_8$");
  const aligned = String.raw`$$\begin{aligned}` + "\n" + String.raw`x &= 2\\` + "\n" + String.raw`y &= 3` + "\n" + String.raw`\end{aligned}$$`;
  expect(boardLines(`Solve:\n${aligned}\nDone`)).toBe(`Solve:\n\n${aligned}\n\nDone`);
  expect(boardLines("| a | b |\n| - | - |\n| 1 | 2 |")).toBe("| a | b |\n| - | - |\n| 1 | 2 |");
  expect(boardLines("First\n\nSecond")).toBe("First\n\nSecond");
});
