---
name: ui-ux-design
description: Produce polished interfaces, not generic AI-looking dashboards. Adapted from nextlevelbuilder/ui-ux-pro-max-skill.
category: design
version: "1.0"
license: MIT
triggers: "website, web app, UI, component, page layout"
---

# UI/UX design

The goal is an interface a person would use by choice, not one that looks
generated. Generic AI output has recognizable tells; this skill is about
avoiding them.

## Avoid the AI-generated look

- **Uniformity.** Every card the same size, every gap the same, every section
  the same heading style. Real designs have hierarchy: one thing is primary,
  the rest supports it.
- **Placeholder copy.** "Welcome to your dashboard" and "Lorem ipsum" signal
  no thought about the user. Write copy for the actual user and the actual
  task, or mark it clearly as placeholder to be replaced.
- **Over-decoration.** Gradients on every button, drop shadows on every card,
  icons on every label. Restraint reads as designed; excess reads as
  generated.
- **No empty states, no error states, no loading states.** A real interface
  handles the 90% of time the happy path is not running. Add them.

## Concrete defaults that read as intentional

- **Spacing:** use a small set of spacing values (4, 8, 16, 24, 48) and apply
  them consistently. Random pixel values read as unconsidered.
- **Type:** one font family, three sizes (body, heading, small), two weights
  (regular, semibold). More variation needs a reason.
- **Color:** a primary, a surface, a text color, and one accent. A seventh
  color needs a reason. Dark and light should both be considered.
- **Corners:** pick one radius (e.g., 8px) and use it. Mixing 4, 8, 12, 16, 24
  randomly is a tell.
- **Layout:** content width capped for readability (60–75 characters for
  prose). Full-width text is hard to read and reads as unfinished.

## Before declaring a UI done

- Look at it at mobile width, not just desktop. If it breaks at 375px, it is
  not done.
- Check the focus states (tab through it). If there is no visible focus, it is
  not accessible and not done.
- Read every string out loud. If it could appear in any app, it is too generic.

## What this skill is not

It is not a license to redesign an existing product's whole style system for
a one-component change. Match the surrounding conventions unless the request
is explicitly a redesign. A consistent interface is better than a "better"
inconsistent one.
