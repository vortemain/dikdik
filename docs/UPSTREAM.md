# OpenHands reference source provenance

The latest user clarification requires an **original DIKDIK implementation with
OpenHands as a reference**, not a relabeled fork. The intermediate plan to ship
adapted upstream runtime code is superseded. Preserve these checkouts separately
for research and comparative testing; no source has been copied into DIKDIK.
Because the source has been inspected, no formal clean-room claim is made.

## Checked-out sources

| Local path | Origin | Branch / exact revision |
|---|---|---|
| `upstream/OpenHands` | https://github.com/OpenHands/OpenHands.git | `main` / `ec0f70fe31b637e699ab3713ba477abe3c3d6b6f` |
| `upstream/software-agent-sdk` | https://github.com/OpenHands/software-agent-sdk.git | `main` / `ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50` |

Both were cloned with `--depth 1`; relevant history must be fetched explicitly
if needed. `git status --short --branch` showed clean checkouts at acquisition.
No upstream source was edited, dependency installed or upstream test/build run
during this correction. These independently cloned main revisions are **not yet
established as a compatible deployment pair**.

## Evidence from the actual checkout

- Canvas `AGENTS.md` identifies this repository as the Agent Canvas React/TS
  frontend and explicitly assigns backend contracts to software-agent-sdk.
- Canvas `package.json` identifies version 1.33.0, Node >=24, npm-based scripts,
  and `@openhands/typescript-client` pinned to 1.58.1.
- Canvas `README.md:151–162` describes the Python Agent Server and optional
  separate Automation Server. Source quickstart warns that unsandboxed backend
  execution grants host filesystem access; do not run it blindly.
- SDK root `pyproject.toml` defines a uv workspace containing `openhands-sdk`,
  `openhands-tools`, `openhands-workspace`, `openhands-agent-server`; its root
  instructions identify the canonical API and separate `clients/typescript`.
- Both root license files are MIT and remain intact. Full dependency/component
  license analysis and notices review have not been completed.

## Working intent

1. Study capabilities and outcomes, while keeping third-party source, notices and
   reference dependencies outside DIKDIK's production code/dependency graph.
2. Implement original Python orchestration with bounded tools, provider adapters,
   durable evidence and candidate-specific completion checks.
3. Use Bun/TypeScript for DIKDIK's own product interface and tooling.
4. Add original Rust repository-intelligence components where measurements justify
   native execution, using explicit interfaces rather than copying upstream code.
5. Evaluate investigate → edit → sandboxed test → repair → verified-report against
   independent acceptance tests. Do not claim superiority or exclusivity by renaming.

Tool executables found: Bun, rustc, cargo, Python, Node, Git, Docker. `uv` was not
found on PATH. The previously checked Docker Linux daemon was unavailable.
Exact tool-version compatibility, dependency setup, upstream baselines, SDK/UI
version alignment and supported Windows/WSL execution paths remain the immediate
investigation work. Do not attribute the prototype's 49 passing tests or synthetic
benchmark results to OpenHands or this new implementation direction.
