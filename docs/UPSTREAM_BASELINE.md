# OpenHands reference baseline — work in progress

The user's later IP clarification supersedes the fork-based implementation plan
below. This record is research/comparative-testing evidence only. DIKDIK is to have
an original core; do not transplant the referenced source into its runtime.

## Scope

Inspect the actual upstream lifecycle, tool executors, event persistence,
provider layer and workspace contracts as a reference, without transplanting them
into DIKDIK. Source acquisition is recorded in `UPSTREAM.md`.

Working branches created without changing upstream source:

- Canvas: `dikdik/bun-baseline` from `ec0f70fe31b637e699ab3713ba477abe3c3d6b6f`.
- SDK: `dikdik/verified-workflow` from `ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50`.

## Source-backed retention map

Paths below are relative to `upstream/software-agent-sdk`:

| Responsibility | Existing owner |
|---|---|
| Agent selection/local vs remote | `openhands-sdk/openhands/sdk/conversation/conversation.py` |
| Main agent/conversation loop | `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/agent/agent.py` |
| Providers, streaming, timeouts/retries | `openhands-sdk/openhands/sdk/llm/llm.py` |
| Tool registry | `openhands-sdk/openhands/sdk/tool/registry.py` |
| Typed persistent state/events | `openhands-sdk/openhands/sdk/conversation/state.py`, `event_store.py` |
| Approval policies | `openhands-sdk/openhands/sdk/security/confirmation_policy.py` |
| Completion hooks and repair feedback | `openhands-sdk/openhands/sdk/hooks/conversation_hooks.py`, `executor.py` |
| Goal audit/retry controller | `openhands-sdk/openhands/sdk/conversation/goal/` |
| Docker-hosted Agent Server | `openhands-workspace/openhands/workspace/docker/workspace.py` |
| Server scheduling and goal recovery | `openhands-agent-server/openhands/agent_server/event_service.py` |

These findings were independently explored in a read-only agent pass. They are
not runtime verification results. Existing tests should be retained and run.

## Consequential risks and first improvement

1. Plain agent completion is not independent verification. Goal judging is
   optional and transcript/model-based. A DIKDIK task must expose required checks
   distinctly from agent completion.
2. Command hooks intentionally fail open for ordinary errors: `executor.py:34–43`
   says only exit **2** blocks; exit **1** does not. Tests in
   `tests/sdk/hooks/test_integration.py:802–819` preserve that contract. Reuse the
   Stop-hook repair mechanism, but explicitly account for failure, timeout and
   wrapper errors before claiming a fail-closed verification gate.
3. DockerWorkspace currently exposes image, mounts, network and cleanup, but
   lacks explicit memory/CPU/PID/capability/no-new-privileges fields. Its default
   image tag is mutable. Do not call the default a hardened hostile-code sandbox.
4. SDK conversation state defaults to `NeverConfirm`; durable storage requires
   explicit configuration. DIKDIK must configure approval/persistence deliberately.
5. Canvas pins Agent Server/typed client 1.58.1; cloned SDK packages are 1.58.2.
   Compatibility needs executed checks, not an assumption based on similar versions.

The researched reference workflow is Canvas → Python agent → isolated workspace →
test failure → repair → checks/diff. DIKDIK's corresponding milestone uses its own
implementation (`ORIGINAL_AGENT.md`), not those upstream classes or source files.
No live model or Docker success is claimed by the reference audit.

## Environment and setup evidence

Observed host tools:

- Node v22.18.0 / npm 10.9.3: do **not** match Canvas's Node >=24 / npm 11.12.1.
- Bun 1.4.0, rustc 1.95.0, cargo 1.95.0, Python 3.13.6.
- `uv` initially absent from PATH. Docker Linux daemon previously unavailable.

Prepared isolated tools under the approved temp `opencode/dikdik-toolchain` path,
without changing global PATH/installations:

- Official Node 24.21.0 Windows x64 ZIP; SHA256 verified against official
  `https://nodejs.org/dist/v24.21.0/SHASUMS256.txt`:
  `158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541`.
  Archive expansion exceeded the initial 120-second command window, but the
  extracted Node executable subsequently ran and reported v24.21.0.
- Bundled npm was 11.19.0, so separately installed the exact upstream npm 11.12.1
  into a private prefix using `--ignore-scripts --min-release-age=7`; verified
  its CLI reports 11.12.1. No project dependency versions were updated.
- Started `npm ci --ignore-scripts --no-audit --no-fund` against Canvas's committed
  lockfile using those pinned tools. Completed: **1379 packages in 16 minutes**.
  Electron and dependency lifecycle scripts are intentionally not executed.
  The targeted Vitest baseline produced 66 passing tests/1 skipped, but also an
  unhandled worker startup timeout; the command exceeded 120 seconds. This is
  **not a passing baseline**, despite the partial test count.
  A subsequent `--maxWorkers=1 --pool=threads` retry passed **74 tests / 1 skipped**
  (two test files, 39.69s). No source/config assertions were weakened.
- Selected uv 0.13.1 from PyPI metadata: upload 2026-10-14, older than seven days.
  Wheel SHA256 `4f693cd4feb52f08f30474c4612f24be2050472609be65b9960b635f286da284`.
  Initial in-memory download timed out; bounded on-disk download succeeded and
  the wheel hash was verified. Isolated executable reports uv 0.13.1.
- Started `uv sync --frozen --package openhands-sdk --group dev --python C:\Python313\python.exe`.
  Completed: 174 packages installed from the frozen lock; one selected upstream
  Stop-hook feedback/continuation regression passed (27 deselected). These reference
  packages are not DIKDIK dependencies. Both reference checkouts remain clean.

## Baseline commands to execute after provisioning

Executed before dependency installation completed: direct imports of the actual
Canvas `scripts/bind-host.mjs` under **both Bun 1.4.0 and Node 24.21.0** passed
smoke assertions for default loopback binding, stripping session-key injection
on `0.0.0.0`, and retaining it on `127.0.0.1`. No backend was started. These three
assertions per runtime are a narrow compatibility smoke check, not the upstream
Vitest suite or proof of full Bun compatibility.

Canvas (pinned Node/npm first, then compare Bun against the same code/dependencies):

```text
npm run make-i18n
npm test -- __tests__/scripts/bind-host.test.ts __tests__/scripts/dev-safe.test.ts
npm run typecheck
npm run build
```

SDK (no live model/Docker cases; setup must first succeed with the frozen lock):

```text
uv sync --frozen --group dev
uv run --no-sync pytest tests/sdk/hooks/test_integration.py -k stop_hook_denial_injects_feedback_and_continues
uv run --no-sync pytest tests/sdk/conversation/goal tests/sdk/hooks tests/sdk/conversation/test_event_store.py tests/sdk/conversation/local/test_confirmation_mode.py tests/sdk/security/test_confirmation_policy.py
uv run --no-sync pytest tests/workspace/test_docker_workspace.py
uv run --no-sync pytest tests/agent_server/test_goal_loop.py
```

Native Windows terminal support exists, but full upstream CI uses Ubuntu/Bash,
forked tool tests and POSIX-only stress tests. Assess WSL2/Linux for that baseline.
Do not launch the unsandboxed `npm run dev` backend merely to get a screenshot.

## Rust and Bun boundaries

Bun adoption starts with measured build/test/runtime compatibility, keeping the
existing npm lock authoritative until migration is demonstrated safe. Rust starts
with a measured repository-intelligence bottleneck and an explicit Python contract;
tool availability alone is not evidence that a rewrite improves performance.
