# Planning and reported task progress

Choose **Plan**, or send `/plan <objective>`. An existing conversation gets a
separate planner child; a new plan gets a draft code conversation and its planner.
Bare `/plan` and `/code` switch between the same pair. Payloads after those
commands are sent without the prefix. Busy turns get an explicit wait message.

The planner can list, read and search the bounded repository inventory, write a
private `PLAN.md`, and report a checklist. It cannot edit candidate files, run
commands/tests, browse or call MCP servers. Provider disclosure still needs your
explicit approval. Profiles and skills can supply guidance but cannot expand the
planner's tool set or permissions.

The plan preview's **Read more** and **View** open the Planner tab. Markdown is
rendered with DOM text nodes; raw HTML stays text, and allowed HTTP/HTTPS links
open with `noopener noreferrer`. Plans are bounded to 32 KiB of UTF-8, stored as
numbered SQLite revisions and mirrored at `<data>/plans/<code-task>/PLAN.md`.
This is a private artifact, not a workspace edit. Importing the reference app's
workspace `.agents_tmp/PLAN.md` remains an explicit parity gap.

**Build plan** or Ctrl/Cmd+Enter in plan mode with an empty composer hands the
reviewed revision to the code conversation. With text in the composer, Enter or
Ctrl/Cmd+Enter sends that text instead. Build requires a current revision,
explicit review confirmation and the code turn's own disclosure/execution
approvals. Stale or duplicate revisions are refused. The code agent receives an
immutable copy marked as untrusted data and can use `read_plan`; follow-ups retain
that approved revision even if the planner later changes its draft. A plan cannot
grant itself shell, network, secret, or workspace-write permissions.

Build produces the ordinary isolated candidate and checks. **Apply to workspace**
remains the only way to write it into the approved project. A syntax-only Build
never claims tests passed. Permission Booleans are persisted atomically with each
turn's `running` event, so task APIs use the latest approved turn without borrowing
permissions from the planner or its parent.

The `task_tracker` tool replaces a task-scoped checklist of up to 50 steps. Each
step has a short unique ID, title and `todo`, `in_progress` or `done` status; at
most one step may be in progress. The Task List tab appears only after the tool
has been used, shows icons plus labels and highlights the current step. This is
progress reported by the agent; completing it is not test or acceptance evidence.
The checklist and plan survive service restart. Deleting the code conversation
also removes its planner, progress rows and private plan artifacts; active work
must be stopped first.

All routes use the existing session/bearer and Host/Origin checks:

| Route | Behavior |
|---|---|
| `POST /api/tasks` with `mode: "plan"` | start the separately approved read-only planner |
| `GET /api/tasks/{id}/planner` | code/planner IDs, content, revision and last built revision |
| `POST /api/tasks/{id}/planner` with `{}` | initialize/reuse the planner without making a model call |
| `POST /api/tasks/{id}/planner/build` | `{confirm: true, planner_id, revision, verification, approve_external, approve_execution}` and optional agent profile/browser approval |
| `GET /api/tasks/{id}/progress` | persisted reported checklist |

Browser verification used a real local HTTP model stand-in with a checked fixture
key and streamed tool calls, plus real SQLite/API/SSE and Chrome. The build used
syntax verification and left source files unchanged. No real provider or live
Docker acceptance run is claimed.
