# Agent skills and lifecycle hooks

This change supplies 38 distinct bundled workflows and a central policy-preserving
hook engine. There is no finite universal registry of “all agentic skills”: the
catalog below defines this release's coverage. Skills teach workflows; they do not
install missing browsers, document renderers, vector databases or external services.

## Research and design decisions

Primary sources reviewed on 2026-10-28:

| Source | Decision applied here |
| --- | --- |
| [Agent Skills specification](https://agentskills.io/specification) | Keep skills in named folders with `SKILL.md`; discover metadata cheaply and load bodies/resources on demand. |
| [Building effective agents](https://www.anthropic.com/research/building-effective-agents) | Compose routing, tools, retrieval, bounded delegation and verification around the existing runtime. |
| [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) | Give lifecycle events explicit decisions, structured outputs, failure semantics and timeouts. Dikdik's schema is its own contract, not Claude's schema. |
| [MCP security best practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices) | Preserve consent and privilege boundaries independently of plugin/tool-provided text. |
| [OpenTelemetry tracing API](https://opentelemetry.io/docs/specs/otel/trace/api/) | Correlate executions by task/run/call and distinguish errors, decisions and timing. No OpenTelemetry dependency/exporter is added. |
| [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) | Exercise complete scripted trajectories, adversarial inputs and outcome evidence, not just successful callback invocation. |

These sources informed the design. New skill bodies are concise original guidance;
the existing ten skills and their attribution are retained.

## Skill coverage

Each row has a different responsibility; overlapping words do not create duplicate
skill ids or copied bodies.

| Skill | Responsibility |
| --- | --- |
| brainstorming | Explore product/problem alternatives before committing to a design. |
| planning | Break the chosen task into concrete ordered work. |
| research-first | Inspect the repository and callers before proposing a change. |
| spec-to-ship | Coordinate the existing specification-to-delivery workflow. |
| new-app | Build a new app on a modern stack (Next.js, Prisma with PostgreSQL) with its full feature set; always loaded for an empty project. |
| test-driven-development | Develop behavior with focused regression tests. |
| debugging | Diagnose the cause of an observed defect. |
| code-review | Inspect a change for actionable correctness/maintenance issues. |
| security-review | Review implementation security boundaries. |
| ui-ux-design | Design the visual and interaction experience. |
| document-creation | Create useful project documents. |
| context-engineering | Select and compact active task context. |
| memory-management | Maintain durable, sourced project knowledge. |
| tool-orchestration | Select and sequence tool calls and interpret outcomes. |
| agent-delegation | Assign bounded investigations to Dikdik's read-only helpers. |
| verification | Connect current evidence to acceptance/completion claims. |
| error-recovery | Adapt after failure without repeating denied/stale actions. |
| prompt-injection-defense | Separate hostile data from instruction authority. |
| agent-evaluation | Build reproducible behavioral evaluations and regressions. |
| retrieval-rag | Retrieve and synthesize relevant evidence with provenance. |
| structured-output | Define and validate machine-readable contracts. |
| skill-authoring | Extend reusable skills without duplicating workflows. |
| mcp-integration | Manage MCP transport, session and tool boundaries. |
| observability | Instrument correlated execution and audit evidence. |
| automation-design | Design idempotent jobs, triggers and notifications. |
| api-design | Evolve request/response contracts and compatibility. |
| data-engineering | Maintain schemas, transactions and migrations. |
| data-analysis | Analyze datasets with reproducible transformations. |
| performance | Measure and improve workload bottlenecks. |
| accessibility | Verify keyboard, semantic and assistive interaction. |
| refactoring | Improve structure while preserving behavior. |
| dependency-management | Maintain reproducible and compatible dependencies. |
| release-management | Prepare artifacts, rollout and recovery evidence. |
| git-workflows | Preserve user changes and maintain focused repository work. |
| browser-verification | Match web claims to available rendered/interactive evidence. |
| scholarly-apis | Recipe: query OpenAlex, PubMed E-utilities and Crossref with `web_fetch` or `run_code`. |
| doi-verification | Recipe: check that each DOI exists and matches the cited paper, singly or for a whole list. |
| statistics-in-python | Recipe: analyses the `statistics` tool cannot express, in `run_code` (Python, or R when installed), with checks. |

### Recipes and narrow tools

General tools come first: shell, files, web, `read_document` and `run_code`. A
recipe is a skill with `type: recipe` in its front matter that teaches how to
compose them for one job. Recipes appear in the catalog by name and description
only; their bodies are never routed into the initial context, whatever the request
says, and load through `read_skill` when the model decides it needs one.

Narrow tools were reviewed against that rule (November 2026):

| Tool | Decision | Why |
| --- | --- | --- |
| `literature_search` | Keep | One call normalises OpenAlex, PubMed and arXiv records and verifies a DOI through Crossref, with no key and bounded output. The `scholarly-apis` recipe covers what it cannot express (paging, filters, counts). |
| `statistics` | Keep | Deterministic, tested analyses that resolve columns by name and report assumptions. `statistics-in-python` covers custom models through `run_code`. |
| `read_document`, `create_document`, `read_image` | Keep | File formats (DOCX, PDF, XLSX, OCR) that would otherwise need packages the user may not have. |
| `analyze_data` | Keep | A bounded first look at a data file; heavier work moves to `run_code`. |

### Research subagent

`delegate` gains a built-in read-only `researcher` in every chat and agent run (a
project or personal agent named `researcher` takes precedence). It starts with a fresh context
and only reading tools (`read_document`, `statistics`, `read_image`, `web_fetch`,
`web_search`, `literature_search`, `analyze_data`, `read_skill`), never commands,
`run_code` or edits, and returns a short report with sources (at most 6,000
characters). Long documents and web pages are read in the child, so they never
fill the main conversation. It has 12 model calls, 40 tool calls and five minutes,
uses the side model when one is configured, and its tool calls pass the parent's
approvals.

Bundled skills seed into the existing installed-skill directory without overwriting
user edits. Existing uninstall tombstones and enable/disable settings are respected.
Project and personal overrides retain their existing precedence; plugin skills are
namespaced. No third-party skill repositories are fetched or executed automatically.

Context contains a catalog of all enabled skills and routed bodies within the
existing 24 KiB content budget (plus the fixed trust preamble). Core methodology and
user/plugin guidance have priority over other installed bodies. A body that does
not fit is skipped without hiding subsequent smaller bodies. A catalog beyond half
the budget falls back to ids, and exceptionally large catalogs are explicitly
labelled truncated rather than silently presented as complete.

Use `$skill-id` or the id in the objective for explicit activation. Native agents,
planners and read-only helpers receive `read_skill` when profile skills are enabled:

```json
{"id":"agent-evaluation"}
```

`read_skill` also accepts `resource`, `offset` and `max_chars` (1–16000). It lists
up to 100 resource paths, confines resource reads to the enumerated skill folder,
rejects traversal/drive paths and escaping symlinks, and returns marked untrusted
UTF-8 text. A disabled skill cannot be loaded. Resources/scripts are never executed
by the loader. The `skills` task event lists bodies actually injected into initial
context; on-demand reads appear as ordinary audited tool calls.

Linked source directories (including Windows junctions) and escaping skill/helper
files are excluded from discovery. Plugin source folders must resolve inside
their configured plugin root; a manifest cannot select an absolute skill/agent
directory or escape through a linked subfolder.

## Hook contract

`dikdik.hooks.HookEvent` defines exactly six initial events. Payloads have concrete
TypedDict definitions and runtime boundary validation:

| Event | Payload | Gate |
| --- | --- | --- |
| `task.started` | `objective`, `mode` | Before workspace inspection and task work. |
| `context.building` | `objective`, `mode` | Before attachment/history/skill context assembly. |
| `tool.before` | `tool`, `call_id`, `arguments` | Before executing a validated native tool or requesting confirmation. |
| `tool.after` | Above plus bounded `result` | After successful execution; a block stops further progress. |
| `tool.failed` | Above plus bounded `result` | After validation failure, policy rejection or tool failure. |
| `task.completed` | `state`, `error` | Before terminal task commit; can downgrade completion to blocked. |

Task lifecycle events cover native agent, planning, model/offline proposal and ACP
task modes. Native tool events also cover MCP calls, shell commands, secret
requests, planning tools, skill reads and delegated helper tools. `task.completed`
describes a terminal execution attempt, including failure/cancellation; a pause
does not fire completion. Each resumed/follow-up attempt gets a new run id and a
fresh configuration snapshot. Delegated executions share the run id and carry a
child task id, so their lifecycle events can be distinguished from the parent.

External ACP program launches are guarded as a single `acp.launch` tool action,
before credentials are resolved or the process starts. They execute outside
Dikdik's native tool loop. Their internal
actions are not security-interceptable via notification messages. Do not treat
these hooks as an OS sandbox or a way to constrain arbitrary external code.

An invalid call can emit `tool.failed` without `tool.before`: tool schema/batch and
retry validation happen first. Cancellation at a step boundary is not reported as
a successful tool result. A post-event block does not reverse a completed write or
external side effect; it prevents subsequent agent progress. Completion blocks
never manufacture successful tests, and do not erase candidate artifacts.

### Declarative project and plugin rules

Project configuration is `.dikdik/hooks.json`:

```json
{
  "hooks": [
    {
      "id": "freeze-release-commands",
      "event": "tool.before",
      "priority": -100,
      "timeout_seconds": 2,
      "match": {"tool": "run_command", "contains": "git push"},
      "decision": "block",
      "reason": "Publishing is frozen during this maintenance window",
      "data": {"policy": "release-freeze"}
    },
    {
      "id": "completion-audit",
      "event": "task.completed",
      "decision": "allow",
      "data": {"label": "completion-observed"}
    }
  ]
}
```

A plugin uses the same document inside `plugin.json`'s `hooks` field, for example
`{"name":"review-kit","hooks":{"hooks":[...]}}`. The installed plugin remains
inactive until the existing trust/enable review is completed. The review includes
its lifecycle rules. Enablement permits **declarative hooks**, not executable code.

`match` accepts only `tool` (exact name or `*`), `contains` (literal substring of
canonical JSON arguments, or lifecycle payload), and `pattern` (regex against the
same text). Conditions are ANDed. Missing match means every occurrence of that
event. Use a stable substring rather than depending on JSON whitespace. No match
produces allow with `matched=false` and no annotations. An unmatched rule cannot
block. Bad regex syntax fails closed when evaluated.

The legacy `{"block":[{"tool":"edit_file","pattern":"app\\.py","message":"Frozen"}]}`
format is translated into `tool.before` rules. Legacy rules and lifecycle rules
can coexist. Invalid JSON, malformed rules, duplicate ids or registry overflow
close every event gate rather than dropping a policy. Enabled plugins whose
manifests become malformed also close the gates until repaired/disabled.

### Trusted host hooks

Only application-owned Python code can register callbacks:

```python
from dikdik.hooks import Decision, HookEvent, HookResult

def maintenance(context):
    return HookResult(Decision.BLOCK, "Maintenance is active")

engine.hooks.register_host(
    "maintenance", HookEvent.TASK_STARTED, maintenance,
    priority=-100, timeout=1,
)
```

Callbacks receive a frozen envelope and a private JSON copy of the payload. Each
callback sees the original payload; mutating its copy cannot alter tool arguments,
the next hook or the runtime. Registries are copied at task start so later
registration changes affect future runs.

Callbacks/rules return only `decision`, `reason`, `data`. Decisions are explicitly
`allow` or `block`; blocks require a nonblank reason. Unknown output fields, bad
types, non-finite numbers, exceptions and oversized data produce a recorded block.
Data is JSON object annotation (at most 16 KiB), not executable instructions or a
patch. Reasons are at most 2000 characters. Inputs are capped at 256 KiB.

Priorities are integers from -10000 to 10000, lower first; ties preserve
registration order. Every matching-event registration is evaluated, preserving
audit visibility. **Any block wins**, independent of source or priority. An allow
never authorizes a call: ordinary schema validation, tool availability, read-only
constraints, approvals, creation policy, protected-test rules, sandbox/browser/MCP
boundaries and verification still run. Plugins cannot register callbacks or
change those decisions through `data`.

Timeouts default to five seconds and must be greater than zero and at most 30.
Regex evaluation runs in a killable isolated Python subprocess with fixed host
code, no shell and no plugin imports. Trusted callbacks use bounded daemon threads;
timeout discards late output, but Python cannot forcibly stop callback side
effects. Consequently host callbacks must be cooperative, side-effect-aware trusted
code. At most eight callback threads can be outstanding; exhaustion blocks. The
registry accepts at most 256 hooks; each document at most 100 rules.

### Durable traces

Task events contain `hook_execution` per registration and `hook_event` per
dispatch, including empty dispatches. Executions store event, hook/source ids,
run/task/dispatch/call ids, priority, sequence, timestamp, duration, timeout,
match status, decision, reason and structured annotation. The trace contains a
SHA-256 digest of canonical input instead of raw arguments, prompts or outputs.
Host callbacks must keep their own returned annotations free of secrets. Trace
write failures propagate and prevent the guarded execution from proceeding.

Hook executions appear in the conversation with correlation details; aggregates
remain in the existing task event API/search. The agent-info dialog displays event,
source, priority and deadline. These records provide an audit trail, not a
cryptographically tamper-proof log or an OTLP exporter.

## Memory, session search and background review

Phase 5 of `docs/AGENT_ROADMAP.md`. All state is in `state.sqlite3`, created by core migration 3
(`memory_entries`, `skill_proposals`, the `session_index` FTS5 table, backfilled from existing
conversations). Standard library only; a Python without FTS5 falls back to a plain table searched
with `LIKE`.

**Durable memory** (`dikdik/memory.py`). Two stores: `user` (who the user is, lasting preferences;
2,000 characters) and `project` (one per project folder: conventions, commands, decisions; 3,000
characters). Chats have no project memory. The `memory` tool takes `add`, `replace` (the one entry
containing `old_text`) and `remove`. A write that would pass the cap is refused with an error that
lists the current entries and asks the agent to consolidate; nothing is dropped. Content that looks
like a credential is refused: the log redaction rules (provider key shapes, bearer tokens, values
registered at runtime such as revealed provider keys), `password: ...`-style assignments with a
non-word value, and long mixed-case alphanumeric tokens. Refusal messages never echo the content.

Each conversation takes a snapshot of memory on its first turn and records it as its first
`memory_snapshot` event; later turns, resumes and follow-ups reuse that exact text, so the prompt
prefix stays byte-stable for prompt caching. Writes made during a conversation show from the next
one. The snapshot is a user message placed before the history, inside
`[UNTRUSTED AGENT MEMORY ...]` markers that say it is agent-reported data with no permissions.

**Session search** (`dikdik/session_search.py`). Objectives, follow-ups (`user_message`) and
replies (`agent_message`) are indexed as `Store.create` and `Store.event` write them. The
`session_search` tool returns raw FTS5 snippets (all words first, then any word) with no model
call, scoped like the task list: the bound project's conversations plus chats, never other
projects, and never the current conversation. Snippets pass through log redaction. Deleting a
conversation removes its index rows; event retention purges indexed messages with the events.

**Background review** (`dikdik/review.py`). After a completed turn with model calls, a single
dedicated thread (not the task pool) checks for triggers: a failed tool or recovery event in the
turn (`recovered_error`), a follow-up that corrects the agent (`user_correction`), or at least five
successful tool calls across three tools (`workflow`). With a trigger, one call to the active
router's fast model (skipped and recorded as `learning_review {status: skipped}` without one, or
when the conversation did not approve sharing with a model) gets a redacted record of the turn and
the current memory, and replies with JSON. Up to five memory updates apply through the same caps
and credential checks (source `review`); a proposed skill is stored in `skill_proposals` as
`pending` and becomes a personal skill only when the user approves it. Each review records a
`learning_review` event on the conversation.

**Skill lifecycle.** Using a skill (`read_skill`, routing into context, or re-enabling it) records
its last use. A deterministic sweep (at startup and after reviews, at most hourly, no model call)
marks personal and project skills unused for 14 days `stale` and archives (disables) them after 30.
Enabling an archived skill starts its clock again. Skill listings carry `lifecycle`.

**Settings and API.** `memory` (default on) turns memory injection and both tools off;
`background_review` (default on) turns reviews off. Review also needs `memory` on. Routes, behind
the usual session and origin checks:

| Route | Effect |
| --- | --- |
| `GET /api/memory` | Both stores with entries, used and limit (`project` is null without a project). |
| `POST /api/memory` | `{action, target, content, old_text}` like the tool, as the user. |
| `PATCH /api/memory/{user,project}` | `{entries: [...]}` replaces the store, within its cap. |
| `DELETE /api/memory[/{target}]` | Clears one store or both. |
| `GET /api/skill-proposals` | Proposals, newest first (`?status=pending`). |
| `POST /api/skill-proposals/{id}/{approve,reject}` | The user's decision. |

There is no UI for these yet; the web client is unchanged.

**Limits.** Credential detection is pattern-based: a secret in an unusual shape (a short password
in prose, a lowercase passphrase) can still be saved, and the user can see and clear it. Memory is
stored unencrypted in `state.sqlite3`, like conversations. Review sends a redacted turn record to the
configured fast model, the same provider class the conversation already approved.

## Validation

`tests/test_hooks.py` exercises event trajectories, rule compatibility, malformed
configuration/output, stable priority ordering, denial precedence, payload and
registry isolation, approvals/creation policy, post/completion gates, plugin
enablement/corruption, tracing, regex termination and callback deadlines.
`tests/test_skill_disclosure.py` covers catalog completeness, routing, deduplication,
disabled skills, bounded loading and resource confinement. Existing agent,
planning, plugin, control and engine tests verify integration behavior.

Run the broader suite with `python -m unittest discover -s tests -p 'test_*.py'`.
Use the operating system's normal temporary directory outside this checkout:
fixtures inside Dikdik's own Git/program directory change repository discovery
and intentionally fail project-opening guards. Frontend validation uses
`bun run typecheck:web`, `bun test tests/web` and `bun run build:web`.

`tests/test_learning.py` covers the memory caps and overflow errors, credential refusal, the frozen
snapshot, FTS search and its scoping, migration 3's backfill, review triggers and a scripted review
model, skill proposals and the stale/archive sweep, the settings and the memory routes.
