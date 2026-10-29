import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

test("sidebar task layout has spiral loading for active tasks and removes dot for inactive tasks", () => {
  const css = readFileSync("dikdik/web/style.css", "utf8");
  const html = readFileSync("dikdik/web/index.html", "utf8");
  const appTs = readFileSync("dikdik/web/app.ts", "utf8");

  // SVG sprite includes the spiral icon
  expect(html).toContain('id="i-spiral"');

  // CSS hides the old static dot for inactive tasks and defines spiral animation
  expect(css).toContain(".conv-state { display: none; }");
  expect(css).toContain(".conv-spiral-loader");
  expect(css).toContain("@keyframes spiral-spin");

  // Old dot colors on conv-state are removed
  expect(css).not.toMatch(/\.conv-state\.state-completed\s*\{\s*background:\s*var\(--ok\);?\s*\}/);

  // app.ts checks isTaskActive and appends spiral loader for active tasks only
  expect(appTs).toContain("function isTaskActive(task: TaskSummary): boolean");
  expect(appTs).toContain("conv-spiral-loader");
  expect(appTs).toContain('icon("spiral")');
});

test("structured UI replaces raw JSON code blocks for tool, staff, and checklist updates", () => {
  const appTs = readFileSync("dikdik/web/app.ts", "utf8");
  const css = readFileSync("dikdik/web/style.css", "utf8");

  // Functions for structured UI exist
  expect(appTs).toContain("function renderTaskChecklistWidget(");
  expect(appTs).toContain("function renderTestChecksWidget(");
  expect(appTs).toContain("function renderPropertiesUI(");
  expect(appTs).toContain("function renderStructuredUI(");
  expect(appTs).toContain("function renderOutputUI(");
  expect(appTs).toContain("function renderEvidenceCard(");

  // Tool results are paired through renderOutputUI instead of dumping raw JSON
  expect(appTs).toContain("call.body.append(renderOutputUI(msg.body, name, label));");

  // Result card evidence uses renderEvidenceCard instead of JSON.stringify
  expect(appTs).toContain("evidence.append(summary, renderEvidenceCard(msg.details));");

  // Confirmation card uses renderStructuredUI instead of JSON.stringify
  expect(appTs).toContain("renderStructuredUI(args, { title: \"Arguments\" })");

  // CSS includes structured component classes
  expect(css).toContain(".tracker-widget");
  expect(css).toContain(".tracker-header");
  expect(css).toContain(".tracker-status-done");
  expect(css).toContain(".tracker-status-progress");
  expect(css).toContain(".prop-table");
  expect(css).toContain(".prop-row");
  expect(css).toContain(".evidence-ui-card");
  expect(css).toContain(".test-checks-card");
});
