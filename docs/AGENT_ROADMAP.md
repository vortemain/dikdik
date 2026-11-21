# Agent quality roadmap

Goal: make Dikdik the most accurate agent per token. Cheap on simple questions, deep on hard
ones, never re-reading what it already knows, and always checking its own work before it answers.

This plan comes from two sources. The first is a diagnosis of a real failed chat run: a thesis
analysis that used 2.49M tokens over 73 model calls and 33 compactions and produced no
deliverable. The second is a source-level study (November 2026) of OpenHands, OpenCode, OpenClaw,
Hermes Agent, Codex CLI, Cline, Roo Code, Aider, Goose and smolagents. Each item names the project
whose mechanism it borrows.

## What the failed run showed

| Cause | Effect in the run |
| --- | --- |
| Chat and planning ran on a 128 KiB working context instead of 320 KiB | Each 40k-character read pushed the context past the cap and triggered compaction |
| Compaction kept findings only from coding read tools | Document, statistics and web results vanished; 61 of 87 `read_document` calls re-read text already seen |
| Claude went through Anthropic's OpenAI-compatible endpoint | No prompt caching (every call billed at full input price, about $4.90), `reasoning_effort` ignored, thinking never shown |
| Chat skills were routed as coding work in an empty project | `new-app`, `ui-ux-design` and `browser-verification` loaded into a thesis chat; with no match, every skill loaded |
| The prompt said "Read documents in full" | Forced paging through a 935 KB proposal |
| Loop detection matched only identical calls | Re-reads with different `offset` and `max_chars` went undetected |
| No check before the answer | Chat answers are accepted as soon as there is no tool call |

## Done on this branch (phase 0)

- Chat and planning get the hosted 320 KiB budget (`engine._context_limit`).
- Compaction remembers `read_document`, `statistics`, `web_fetch`, `web_search`,
  `literature_search` and `read_image` results, with larger excerpts and a budget that scales with
  the context limit.
- A `save_notes` tool (chat and agent mode) records key facts as working notes that are carried
  through every compaction, in the style of OpenClaw's memory flush and Hermes' bounded memory.
- Re-read detection for documents covers overlapping ranges and repeated `find` lookups, whatever
  page size is asked for. It prompts the model to write notes and move on.
- Chat skills route as `chat`: only skills whose triggers match load, never new-app or coding
  defaults, and the catalog still lists the rest for on-demand loading.
- The chat prompt now asks for targeted reading, effort matched to the question, and checking
  numbers against tool output before delivering.

## Phase 1: stop paying for the same tokens twice

1. **Native Anthropic Messages adapter** (OpenCode `anthropic-messages.ts`, Hermes "system_and_3").
   - `cache_control` breakpoints on the tools, the system prompt and a rolling last message.
   - `thinking: adaptive` with `output_config.effort`; summarized thinking streamed to the UI.
   - Thinking blocks replayed unchanged.
   - Keep the OpenAI-compatible path for other providers.
2. **Cache-stable prompt layout** (OpenHands static/dynamic split, OpenClaw cache boundary).
   - Byte-stable system prompt and tool list in a fixed order.
   - The date and per-turn facts move into the latest user message.
   - `prompt_cache_key` for OpenAI-compatible providers.
   - Effort held constant within a task.
3. **Spill large tool output to files** (OpenHands `truncate.py`, OpenCode `tool-output.ts`,
   Hermes `archive_compaction_drops`).
   - Cap inline output at about 10–30 KB, keeping head and tail.
   - Save the full text under the task's artifacts by content hash.
   - When compaction drops a result, leave a stub with the saved path, so going back to it costs
     one local read instead of a re-fetch.
4. **Show cost and cache in the UI**: context used, cached versus fresh tokens, cost per turn, and
   compaction shown as a visible step (Cline).

## Phase 2: compaction that never forgets

5. **Typed rolling checkpoint** (OpenCode template, OpenHands summarizer, Goose JSON).
   - Sections: objective, requirements, decisions, work state, next move, files/sources, key
     numbers.
   - Updated in place across compactions rather than rewritten.
   - Keep about 15–20k tokens of recent turns verbatim, compact down to about 50%, and refuse
     compactions that free less than 10%.
6. **Identifier audit** (OpenClaw safeguard mode): check that URLs, DOIs, file paths and numbers
   survive the summary; retry once if they don't.
7. **Pre-compaction memory flush** (OpenClaw): a silent turn about 4k tokens before the threshold
   asks the model to update its notes.
8. **Files and sources ledger** (Cline `## Files`): a deterministic list of what was read, fetched
   and changed, merged across compactions.

## Phase 3: think in proportion, verify before answering

9. **Adaptive effort** (OpenClaw `/think`, OpenCode variants).
   - A cheap heuristic picks low effort with no planning and few tools for short factual
     questions, and high effort with plan and verification for multi-step work.
   - The user can override it per message.
10. **Final-answer checks** (smolagents `final_answer_checks`, OpenHands critic).
    - Deterministic first: every cited source was fetched this session, numbers match tool
      output, deliverable files exist and open, tests ran for code.
    - A model critic only at high effort or when a check fails, with at most 2 refinement rounds.
11. **Richer stuck detection** (OpenHands `StuckDetector`, Cline loop detection).
    - Same action with the same result 4 times; same action with an error 3 times, nudged before
      stopping.
    - A monologue of 3 turns, A/B alternation 6 times, signatures with sorted keys.
    - A soft warning at 3 and a stop-and-ask at 5.
12. **Lint/test reflection for code** (Aider, OpenCode v1 LSP diagnostics): run the project's
    linter or type checker on edited files and feed errors back, with at most 3 reflections.

## Phase 4: an open-minded toolset (done)

Done on `claude/phase4-tools-8afbd0`; details in `docs/TOOLCHAINS.md` ("Running commands and
Python") and `docs/AGENT_SKILLS_AND_HOOKS.md` ("Recipes and narrow tools", "Research subagent").

- `run_python` runs scripts through the approval gate and local execution, calling allow-listed
  read-only tools (`read_document`, `statistics`, `web_fetch`, `web_search`, `literature_search`)
  over a per-run loopback bridge; printed output returns, capped head and tail. It is offered
  wherever commands are approved; per-model enablement is not implemented.
- `run_command` describes the real shell family, takes `workdir` and `background`, reports exit
  code and working directory, and moves a timed-out command to the background with a completion
  notice. `command_process` reads output, sends input and stops background commands.
- Allow rules and remembered commands work by command prefix and never cover chained commands.
- `literature_search` and `statistics` stay tools; three recipes (`scholarly-apis`,
  `doi-verification`, `statistics-in-python`) load on demand.
- `delegate` has a built-in read-only `researcher`, also in chat.
- Not done: a persistent shell session (each command is still a fresh process).

13. **General tools first, narrow tools as skills** (OpenClaw, Hermes).
    - The core is shell, files, web, `read_document` and Python.
    - Recipes such as literature search, DOI checks and specific statistics become SKILL.md
      guidance the model reads on demand. Only names and descriptions stay in the prompt.
14. **`run_python` that can call Dikdik tools** (Hermes `execute_code`, smolagents CodeAgent).
    - Loops, bulk filtering and statistics are done in real code; only printed output returns.
    - Uses the existing approval gate and local execution, with no Docker.
    - On by default only for models that handle it well.
15. **Adaptive shell** (OpenHands terminal, OpenCode shell, OpenClaw `exec`).
    - A persistent session with exit-code and working-directory markers.
    - A tool description per shell family (PowerShell or POSIX), and a `workdir` argument.
    - A timeout moves the command to the background instead of killing it, with a completion
      notice.
    - Input can be sent to a running process; allow-lists by command prefix.
16. **Fresh-context research subagents** (OpenCode `explore`, Hermes `delegate_task`): long reading
    happens in a child context, and only a short report returns.

## Phase 5: learn from experience

17. **Durable memory** (OpenClaw `MEMORY.md`/`USER.md`, Hermes bounded files): per-user and
    per-project facts, size-capped, injected once per session so the cache holds, and searchable
    with SQLite FTS5 (no embeddings needed).
18. **Background review** (Hermes): after a recovered error, a user correction or a successful
    multi-step workflow, a cheap review proposes memory or skill patches.
    - Skill writes need approval.
    - Unused skills go stale after 14 days and are archived after 30.

## Phase 6: the chat space

19. **Thinking** as a collapsible block that streams while running, then collapses to "Thought for
    Ns" with a summary.
20. **Tool activity** as compact one-line cards (verb, target, status, duration), with consecutive
    reads grouped ("Read 6 sections").
21. **Plan checklist** rendered from `task_tracker`; the model doesn't repeat it in text (Codex).
22. **Citations** as inline numbered chips; deliverables as file cards with a preview.
23. **Steering**: a message sent mid-run is queued and applied at the next tool boundary instead of
    stopping the run.
24. **Checkpoints**: restore files, the conversation or both per step (Cline shadow git; check
    overlap with existing checkpoints first).

## How we will know it worked

Re-run the failed thesis task and a fixed set of chat and coding tasks with the stub model, then
with a live model.

- Tokens per completed task
- Cache-hit ratio
- Compactions per task
- Re-read ratio
- Model calls for a one-line question (target: 1)
- Verified-claim rate

Record each run in `docs/VERIFICATION.md`.
