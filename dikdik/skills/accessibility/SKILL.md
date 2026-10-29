---
name: accessibility
description: Build and review accessible interfaces using semantic, keyboard and assistive-technology behavior.
category: engineering
version: "1.0"
triggers: "accessibility, screen reader, keyboard navigation, aria, wcag"
---

# Accessibility

1. Use native semantic elements and accessible names before adding ARIA. Check labels, error associations, heading order and meaningful control states.

2. Verify keyboard operation, visible focus, logical traversal, escape behavior and focus restoration for dialogs. Ensure updates do not trap or silently move focus.

3. Check contrast, zoom/reflow, reduced motion and non-color state cues. Preserve readable text and usable hit targets at narrow widths.

4. Use available automated checks plus focused browser or assistive-technology inspection. State which behavior was exercised; a static code review alone cannot establish full conformance.
