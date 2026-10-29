---
name: debugging
description: Systematic debugging instead of random changes. Adapted from obra/superpowers.
category: methodology
version: "1.0"
license: MIT
triggers: "bug, error, wrong output, failure to reproduce, crash, broken"
---

# Debugging

Most "debugging" by an agent is actually guessing and editing until something
passes. That produces a change, not a fix — the bug often moves or hides.

## The disciplined loop

1. **Reproduce.** Before anything else, get a concrete input that triggers the
   bug. If you cannot reproduce it, you cannot know a fix works. Say so and ask
   for a reproducing case rather than editing blind.
2. **Read the failing path.** Follow the input through the code to where the
   output goes wrong. Use `read_file` and `search_text` to trace it; do not
   skim filenames and guess.
3. **Form a hypothesis.** One sentence: "I think X is wrong because Y." The
   hypothesis must predict what you will see if it is true.
4. **Test the hypothesis.** Run the reproducer with a check that targets the
   hypothesis — a print, a small test, or a targeted change. If the prediction
   does not hold, the hypothesis is wrong; do not edit the code yet.
5. **Make the smallest fix that makes the reproducer pass.** A fix that touches
   five files is usually five fixes; do one.
6. **Run the broader suite.** The fix may have broken something the reproducer
   did not cover.

## Anti-patterns

- **Editing the symptom.** Suppressing an error message is not a fix; the
  underlying condition still happened.
- **Changing what you can see instead of what is wrong.** If the bug is in the
  data flow and you only edit the display, the display will look right and the
  data will still be wrong.
- **"Cleaning up" while debugging.** Unrelated refactors in a bug fix make the
  fix hard to review and hard to revert. Save cleanup for its own change.

## Output

The reply should state the reproducer, the root cause (one sentence), the fix,
and the verification. If the user cannot tell what was actually wrong from
your reply, the debugging was not done; the editing was.
