# Diagnosing repeated model-request failures

The 2026-10-28 investigation of task `3449b42d4d4e4039add228b8eec105af`
found four defects in Dikdik's client. The saved events retained only
`Model request failed`, so the exact provider error for those historical calls
cannot be recovered. Their prompts were approximately 82–95 KiB, below the
128 KiB client context bound. The displayed 132,005/137,601 token totals were
cumulative usage across requests, not a single prompt overflowing a context window.

1. The native agent adapter always requested 2048 output tokens. A profile could
   lower that limit but could not raise it. Reasoning and tool-call generation can
   exhaust this budget before producing a complete call.
2. Streaming measured all SSE framing/envelope bytes against the 256,000-byte
   response limit. A short answer split into many chunks could exceed that bound.
3. Provider categories, truncation and malformed responses all became the same
   generic error in the agent loop. HTTP-200/SSE error envelopes were not checked.
4. Assistant reasoning continuation fields were dropped between tool calls.

The fixes keep normal tool permissions, hook decisions, confirmations, context
limits and candidate verification intact:

- Default agent output budget is 8192 tokens. On truncation, an unconfigured
  budget can grow to 16384 and then 32768, with at most three requests for one
  response. Explicit profile limits are honored exactly, including smaller limits.
- SSE wire traffic has a separate 16 MiB bound; actual assembled output retains
  its 256,000-byte bound. An individual line is also bounded. Streams that end
  without a finish marker cannot execute their partial tool calls.
- Known provider errors have safe categories and messages. Rate limits,
  temporary upstream/network failures, timeouts and interrupted/empty responses
  get at most three attempts. Authentication, credit, context and content-filter
  failures do not get blind retries. Long Retry-After cooldowns
  are surfaced instead of retrying too early. Backoff is cancellable, and every
  attempt consumes the existing model-call budget.
- Tool response validation now names the exact failed check. Malformed tool JSON,
  oversized calls and invalid call lists get bounded regeneration with a format
  correction, before executing any tool. Null tool lists and object-form JSON
  arguments from compatible servers are normalized. Tool arguments are bounded at
  128 KiB and model text at 64 KiB in both adapter and native loop; a full-page edit
  is no longer rejected solely for exceeding the earlier 32 KiB argument cap.
- Protocol errors raised before tool validation also receive bounded regeneration.
  The adapter retries with streaming disabled after a transport-format failure;
  malformed reasoning continuation fields receive the same correction as malformed
  tool arguments. All share the existing maximum of three requests per response,
  cancellation and model-call budgets. Discarded calls never execute, and safe
  validation codes identify the failed check without storing raw provider bodies.
- Inference retries reuse the same prompt before accepting any response/tool
  effects. Reported usage from rejected attempts is included in task totals and UI.
  Billing remains unknown where the provider does not report it.
- Bounded reasoning text/details are preserved only on assistant messages and in
  resumable checkpoints. Indexed streaming fragments retain signatures/opaque
  data; they never become system instructions or change runtime permissions.
  Existing checkpoints cannot recover reasoning that older code already discarded.
- Failure and retry rows show category, attempt, HTTP status and a safe explanation,
  rather than provider bodies, keys, raw exceptions or arbitrary metadata.
- Context compaction retains earlier user requirements, the latest reported
  checklist, bounded excerpts of findings and authoritative current change/test
  state. Large completed observations are excerpted; a large group no longer
  prevents smaller older groups from fitting. Signed assistant continuation data
  is kept intact whenever its group is retained. Older resumable checkpoints can
  recover the checklist and user requirements from host-persisted task records.
- Repeated identical observations across an investigation prompt continuation
  guidance after the full tool batch finishes. This does not forbid necessary
  reads or bypass validation. Compaction is shown as a plain status notice; byte
  counters remain in the durable event log instead of appearing as code in chat.

Primary references: [OpenRouter error handling](https://openrouter.ai/docs/api-reference/errors-and-debugging)
documents HTTP-200 error envelopes and truncation; [reasoning-token guidance](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
describes shared output/reasoning budgets and continuation fields during tool use.

Validation includes deterministic truncation/error/stream fixtures, unchanged
prompts and candidates during retries, cancellation, usage accounting, spending
caps, signed reasoning round-trips and the existing provider/agent integration tests.
A live two-request GLM-5.2 probe completed one read-only tool call against a literal
in-memory synthetic file, without sending saved task content or repository source.

Restart the running Dikdik service to load the updated adapter before resuming an
older failed task. No failed task is automatically resumed or modified by this fix.

## Local verification and verification-only resumes (2026-10-29)

Task `05c24a7c21b84084b1b9335f3b8db843` had successfully written its URL change.
It was blocked because only automated test suites were detected; the pnpm
workspace declared typecheck scripts instead. Its resumed attempt made no new
edits, so the latest result also lost the earlier diff/checkpoint metadata.

Local checks now discover workspace/nested manifests, respect the package manager
and working directory, prefer declared tests, and use typecheck/lint/build only
when no tests are configured. Root orchestration is not duplicated per child.
Passed static checks permit completion with `tests_verified=false`. Missing known
project check tools are detected before executing a script. In local mode only,
when the baseline project has no declared test suite, unavailable validation may
complete as **applied but unverified**, with explicit missing-tool/check evidence.
It never produces a test or check pass. Real test suites remain required; actual
compiler/lint failures remain unsuccessful, and removing a baseline check cannot
grant the unavailable-validation exception. Native callers must explicitly opt
into either static-check or unavailable-check completion; Docker/default callers
retain strict test verification. Any fresh edit or verification attempt invalidates
older completion evidence, including an unavailable-check allowance.

Resumable state records the logical turn's original file contents and the check
kind, so passing typecheck can never become a claimed test pass after resume.
Verification-only resumes retain the existing diff and Undo checkpoint. Further
edits begin a fresh Undo checkpoint at that resume boundary. Older checkpoints
can recover these facts from the matching user turn's durable events. Files changed
externally after a pause are neither overwritten nor accepted as agent-verified.

The UI distinguishes execution mode, passed project checks, unavailable validation
and actual test evidence. Missing validation is not presented as a failing test.

## Native Anthropic transport and cache-stable prompts (2026-11-21)

A chat run on `claude-sonnet-5-5` cost about $4.90 because Anthropic profiles went through
Anthropic's OpenAI-compatible endpoint. That endpoint has no prompt caching, ignores
`reasoning_effort`, never returns thinking and hoists every system message to the top.

- Profiles whose provider is `anthropic` (or whose endpoint is `api.anthropic.com`) now use
  `anthropic_messages.py`: `POST /v1/messages` with `x-api-key` and `anthropic-version`, over the
  same `urllib` transport, cancellation and size limits as before. Replies are converted to the
  chat-completions shape, so the agent loop, retries and usage accounting are unchanged.
- Cache breakpoints sit on the tool list, the system prompt and the two newest turns (at most
  four). `usage.cache_read_input_tokens` and `cache_creation_input_tokens` become
  `cache_read_tokens` and `cache_write_tokens` in the `agent_model` end events; input tokens
  count the whole prompt, cached or not.
- Models from 4.6 (and every Fable and Mythos model) get `thinking: {type: "adaptive",
  display: "summarized"}`; the profile's reasoning effort becomes `output_config.effort`
  (`minimal` maps to `low`). Models from 4.7 get no `temperature` or `top_p`. No request forces
  a tool choice. Older models get neither thinking nor effort.
- Thinking summaries become the reply's reasoning. Thinking blocks are kept, with their
  signatures, in the assistant message's `reasoning_details` and replayed unchanged on the next
  request. Compaction rewrites older turns, so models that bind thinking to its conversation
  (Opus and Sonnet 5.5, Fable and Mythos 5.1 and later) send
  `block_binding.prefix_mismatch_behavior: "drop_block"` with the
  `thinking-binding-controls-2026-08-01` beta. A 400 about a thinking signature is retried
  once with the thinking blocks removed.
- `stop_reason` `max_tokens` is an output-limit failure, `refusal` a content-filter failure and
  `pause_turn` is resumed (at most three times). HTTP 413 and "prompt is too long" are context
  failures, 529 and overloaded stream events are transient provider failures.
- The chat system prompt no longer contains the date. The date and this message's skill
  guidance go into a user message after the earlier turns, so the system prompt, the tools
  and the history stay a byte-identical prefix. Requests to `api.openai.com` carry a
  `prompt_cache_key` (a digest of model, system prompt and tool names); other
  OpenAI-compatible servers are left alone because some reject unknown fields. OpenRouter
  keeps its `cache_control` marks for Claude models.
- Tool results over 30 KB keep their first 16 KB and last 8 KB inline, with a header giving
  the original size. In chats the full text is saved as `tool-output/<hash>.txt` in the chat's
  folder (hidden from its download list) and the header names that path, which
  `read_document` can page or search. `read_document` pages are now 24,000 characters so an
  ordinary page stays inline. Agent-mode tasks get the head and tail without a saved file.

Verified with unit tests against a loopback fake server and mocked openers only; no live
Anthropic request was made. Remaining risks: the exact event and error shapes of the live
API, thinking blocks whose text and tool calls arrive interleaved (they are replayed thinking
first, then text, then tool calls), and models whose names do not follow `claude-<family>-N-M`
(they are treated as older models and get no thinking).
