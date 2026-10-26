# Original DIKDIK agent milestone

## Scope and provenance

`dikdik/agent.py`, `agent_provider.py` and their integration are original DIKDIK
implementations. They do not import, link, vendor or copy the OpenHands SDK or
Canvas. The upstream checkouts under `upstream/` remain research references with
their own notices. Public tool-call HTTP message formats are protocol contracts,
not transplanted upstream agent code. No formal clean-room claim is made because
upstream source has been inspected; legal ownership/exclusivity needs the actual
owner's applicable agreements and licensing decisions.

The original prototype's authenticated HTTP, SQLite events, conservative inventory,
candidate snapshots and Docker runner are retained—not thrown away merely because
the product direction was clarified. Explicit-edit and single-proposal modes still
work, but neither is described as a tool-using autonomous agent.

## New workflow

The `agent` task mode accepts an objective **without user-authored edits**. An
original model/tool loop can list paths, read candidate lines, search exact text,
perform validated replacements, run the fixed Docker verification, inspect real
failure diagnostics, repair and request completion. It works entirely on an
in-memory candidate and private snapshots. There is no arbitrary host command,
path escape, test-file modification, new-file creation or source apply operation.

Tools: `list_files`, `read_file`, `search_text`, `edit_file`, `run_tests`, `finish`.
No separate OpenHands process or imported backend runs underneath this workflow.
The compatible-provider adapter implements the public function-tool-call format;
the `AgentModel` protocol also permits independent provider implementations.

## Completion gate and bounded work

- Agent mode requires literal Boolean external-disclosure **and** Docker execution
  approvals, and cannot be selected with syntax-only verification.
- A `finish` request alone is not sufficient. There must be net changes and a
  successful unittest result for the exact current candidate.
- Every edit invalidates prior test evidence. Historical failures remain visible
  even when a subsequent current-candidate check succeeds.
- Python syntax errors are diagnosed without executing code and fed back for repair.
- Infrastructure-blocked/inconclusive checks stop the workflow; they never become
  successful repairs. Syntax parsing alone cannot authorize completion.
- Test execution requires a matching per-run receipt and nonzero executed counts,
  not only startup and exit zero. This catches early process exit, but cannot
  establish adversarial-proof test integrity within an untrusted interpreter.
- Default ceilings: 12 model calls, 40 tool calls, 3 repair edits after failures,
  32000 **reported** tokens, 180-second between-step deadline. No hidden retries.
- Reported-token enforcement depends on accurate provider usage metadata. When
  absent, token counters may be zero; call/output/context caps still apply, but
  no exact token-cost cap or monetary budget is claimed.
- A model response may request at most 8 tools; mixed finish/edit batches are
  rejected. IDs are unique across the run. Arguments and observations are capped;
  context and provider output are bounded. Repeated identical tool failures stop.
  A failed unchanged candidate cannot reach the test runner again before a repair.
- Cancellation and deadlines are cooperative around model/verifier calls. Socket
  idle timeout is not a hard provider wall-clock deadline. Docker retains its
  separate process cleanup and in-container timeout mechanisms.
- Persistent event failures stop further agent work rather than silently dropping
  the audit trail. SQLite and candidate files are not one atomic transaction.

## Verification evidence and limits

The new Python tests use scripted models and instrumented verifiers to exercise
failure → edit → pass → finish, stale-verification denial, bad arguments, protected
tests, repair/budget/cancellation limits and real Engine/HTTP/filesystem persistence.
**Those are orchestration/contract tests, not a live model solving a task or a
live Docker acceptance pass.** No actual provider credentials have been used.

To run the genuine demonstration after configuring the HTTPS tool-capable provider
and a working Linux Docker daemon with the reviewed image preloaded:

```powershell
python scripts/demo_agent.py --approve-external --approve-execution
```

This command really calls the configured model and restricted Docker runner; it
does not silently substitute a fake. It sends only the demo fixture snapshot through
the approved tools, may incur charges, prints actual evidence/diff, and returns
nonzero unless the terminal result is completed with test verification. It creates
a temporary copy and deletes its transient state when finished; use the application
UI with a persistent data directory when retained artifacts are needed.

Live model/sandbox success remains unverified in this environment. Product limits
still include Python unittest-only execution, bounded text snapshots, protected
test-named files, existing-file replacements and no automatic source application.
General multi-file feature generation, broader runners, Rust indexing, advanced
delegation and wider release assurance remain subsequent milestones.
