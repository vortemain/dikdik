import { expect, test } from "bun:test";
import { nextReveal, safeCut, stableEnd } from "../../dikdik/web/typewriter";

test("typing keeps a steady pace and catches up with a big burst in under a second", () => {
  expect(nextReveal(0, 10, 16)).toBeGreaterThanOrEqual(1);          // always moves
  expect(nextReveal(0, 10, 16)).toBeLessThanOrEqual(10);
  expect(nextReveal(5, 5, 16)).toBe(5);                             // nothing more to show
  let shown = 0, frames = 0;
  while (shown < 1500 && frames < 1000) { shown = nextReveal(shown, 1500, 16); frames++; }
  expect(frames * 16).toBeLessThan(2200);                          // a 1,500-character burst is typed out in about two seconds
  const slow = nextReveal(0, 20, 16);
  expect(slow).toBeLessThan(5);                                     // a slow model is not rushed: a few characters a frame
});

test("formulas, bold text, code and links are revealed whole, never as raw source", () => {
  const text = String.raw`The area is $\pi r^2$ and **very** big, see ` + "`code`" + " and [the source](https://example.org) now.";
  const inside = (needle: string) => text.indexOf(needle) + 2;
  expect(safeCut(text, inside(String.raw`$\pi`))).toBe(text.indexOf("$ and") + 1);
  expect(safeCut(text, inside("**very"))).toBe(text.indexOf("** big") + 2);
  expect(safeCut(text, inside("`code"))).toBe(text.indexOf("` and") + 1);
  expect(safeCut(text, inside("[the"))).toBe(text.indexOf(") now") + 1);
  // Not closed yet: wait where it starts instead of showing half of it.
  expect(safeCut("Energy is $E = mc", 14)).toBe("Energy is ".length);
  // Words appear whole.
  expect(safeCut("Photosynthesis happens", 4)).toBe("Photosynthesis".length);
});

test("drawings and display formulas appear whole once finished, and wait while they are still coming", () => {
  const done = "Look:\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\nNext";
  expect(safeCut(done, done.indexOf("A -->"))).toBe(done.indexOf("```\n\nNext") + 3);
  const coming = "Look:\n\n```mermaid\nflowchart TD\n  A --> B";
  expect(safeCut(coming, coming.length - 2)).toBe("Look:\n\n".length);
  const formula = "So\n\n$$\nx = " + String.raw`\frac{1}{2}` + "\n$$\n\nThen";
  expect(safeCut(formula, formula.indexOf("frac"))).toBe(formula.indexOf("$$\n\nThen") + 2);
});

test("only finished paragraphs are treated as stable", () => {
  expect(stableEnd("First paragraph.\n\nSecond is still being wri")).toBe("First paragraph.\n\n".length);
  expect(stableEnd("```python\nx = 1\n\ny = 2")).toBe(0);                 // inside an open fence
  expect(stableEnd("$$\na\n\nb")).toBe(0);                                 // inside a display formula
  expect(stableEnd("1. one\n\n2. two")).toBe(0);                           // a list is kept together
  expect(stableEnd("<details><summary>Answer</summary>\n\nStep 1\n\nStep 2")).toBe(0);
  expect(stableEnd("Done.\n\n<details><summary>A</summary>\n\nx\n\n</details>\n\nAfter")).toBe("Done.\n\n<details><summary>A</summary>\n\nx\n\n</details>\n\n".length);
});
