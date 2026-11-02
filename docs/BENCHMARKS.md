# Benchmarks

`python -m dikdik.bench` measures the agent so a change can be shown to help, not assumed to.
It reports pass rate, wall-clock time split into model / tool / Dikdik's own time, model and
tool calls, failed tool calls, compactions, tokens and provider-reported cost.

## Real tasks

```powershell
python -m dikdik.bench run benchmarks/fixtures.json --out before.json
python -m dikdik.bench run benchmarks/my-projects.json --profile "Nebius Token Factory · zai-org/GLM-5.3" --out after.json
python -m dikdik.bench compare before.json after.json
```

Each task runs as an ordinary local-mode agent task on a throwaway copy of its project:

- **Projects** are exported from git at `ref` (default `HEAD`), so uncommitted work is never
  used or changed. `"ref": null` copies the folder instead (the bundled fixture).
  `"link": ["node_modules"]` shares a heavy folder through a link rather than copying it;
  commands the agent runs can still write into it.
- **The model** comes from `DIKDIK_MODEL_URL`, `DIKDIK_MODEL` and `DIKDIK_API_KEY`, or from a
  Dikdik model profile (`--profile` name or id, else your default profile). Profiles are read
  read-only from `~/.dikdik` and the key stays in memory. Results never enter your task list.
- **Scoring**: a task passes when it ends in `expect_state` (default `completed`), every check
  passes, and no changed file breaks an invariant (valid UTF-8, no LF lines mixed into a CRLF
  file). `targets` (for example `{"model_calls": 5, "seconds": 120}`) are reported as
  `on_target` without failing the task.

Checks: `{"file": p, "contains" | "not_contains" | "matches" | "exists": …}`,
`{"command": "bun run check", "timeout": 900}` (passes on exit code 0) and
`{"changed": [paths]}` (exactly these files changed; `[]` for questions). In commands,
`{python}` is the benchmark's interpreter and `{suite}` the suite's folder, so a checker can
live outside the project the agent edits: `navbar-flow-design` is judged in a real browser by
`benchmarks/check_landing.py` (computed borders, shadows, gaps and backgrounds at desktop and
mobile width). Tasks with `"needs": "playwright"` are skipped where Playwright is missing. Budgets:
`max_model_calls`, `max_tool_calls`, `deadline_seconds`. `--repeat N` runs each task N times,
since model runs vary; `--keep` keeps each project copy for inspection.

`benchmarks/fixtures.json` runs anywhere. `benchmarks/my-projects.json` replays real requests from
past runs on projects at their `C:\Projects` paths; edit the paths and checks for another machine.

## Dikdik's own overhead

```powershell
python -m dikdik.bench overhead --files 350 --steps 30
```

A scripted model that answers instantly runs read, search and edit steps on a generated
project. What remains is Dikdik's own per-step cost: inventory, checkpoints, events, hooks and
file writes. No model or key is needed. Freshly generated files are scanned by antivirus on
first open, so compare runs on the same machine.

## Past runs

```powershell
python -m dikdik.bench history --limit 20
```

Reads your recent agent tasks from `~/.dikdik` read-only and prints the same metrics, counted
from events so paused and resumed runs report their whole cost.
