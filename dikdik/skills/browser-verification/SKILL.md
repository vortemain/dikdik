---
name: browser-verification
description: Verify a design or UI change in the rendered page — before and after, at desktop and mobile width — instead of trusting the code edit.
category: engineering
version: "1.1"
triggers: "browser verification, visual check, end-to-end, rendered page, preview, screenshot"
---

# Browser Verification

A UI change is done when the rendered page shows it, not when the CSS looks right.
Utility classes, inherited styles, sibling elements and parent backgrounds all decide
what a person sees, so check the result where it renders.

## The loop

1. **Name the complaint as something measurable.** "The navbar looks separated from the
   page" becomes: no visible border or shadow on the header's bottom edge, the same
   background on the header and the section below, no gap between them.
2. **Look before you change.** Run `preview_app` with `focus` set to the element in
   question (`header`, `footer`, `.hero`) and `inspect` listing it and its neighbours
   (`["header", "main > :first-child"]`). Read the inspected `border-*`, `box-shadow`,
   `background-*`, `margin`/`padding` and `gap_to_next_sibling`. The real cause is often
   not the class you expected: a shadow, a different background, a margin on the next
   section, or a wrapper's border.
3. **Change what causes it.** Edit the property the inspection showed, matching the
   project's conventions (Tailwind classes, CSS modules, design tokens).
4. **Look again.** Preview with the same `focus` and `inspect`. Compare the before and
   after numbers, and look at both screenshots when they are shown to you. The specific
   complaint must be gone at desktop *and* mobile width, with no new runtime errors,
   failed resources or horizontal overflow.
5. **Exercise interactions** the request mentions with `actions` and `expect_text`.

## When preview cannot run

If the dev server cannot start or no browser is available, verify from the code and the
CSS cascade, then say plainly that the visual result was not checked in a browser.
Never describe an unchecked design as verified.

## Reporting

Say what you inspected and what changed in the numbers (for example "header
border-bottom 1px → none; header and main both rgb(255, 255, 255); gap 0px"). Report
whether evidence came from code reading, a page fetch or a rendered preview.
