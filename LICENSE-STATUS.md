# License status

## Newly cloned upstream sources

The active direction is an original DIKDIK platform with OpenHands as a separate
research reference, not a production dependency or renamed fork.
The following separate checkouts retain their original licenses unchanged:

- `upstream/OpenHands/LICENSE`: MIT, copyright © 2025 OpenHands contributors.
- `upstream/software-agent-sdk/LICENSE`: MIT, copyright (c) 2026 OpenHands contributors.

Redistributing their code requires preservation of the applicable copyright and
permission notices. Component/dependency licenses still require review; the MIT
root licenses do not imply that all dependencies have identical terms. See
`docs/UPSTREAM.md` for exact source revisions. No license is asserted for original
DIKDIK additions beyond what the actual owner authorizes.

## Original DIKDIK implementation

This workspace began empty. The implementation added here is original DIKDIK
work; no OpenHands or other upstream source has been copied or adapted.

The project owner has not selected a distribution license. **No open-source
license grant is asserted.** Choose an appropriate license and actual copyright
holder before publication or distribution. This file is a status record, not
legal advice or a replacement license.

Runtime Python dependencies: none beyond Python's standard library.
The private Bun frontend package uses `UNLICENSED` to avoid asserting an
owner-unapproved license grant. Its only third-party frontend development
dependency is TypeScript 6.0.3 (Apache-2.0), published 2026-04-16; registry
timestamp and locked integrity were verified before installation. It is a build/
type-check tool, not copied DIKDIK runtime code. Bun has its own MIT license.
Packaging build requirement: setuptools >=77 (MIT; verify the exact resolved
version if producing a distribution). Development tests require no packages.
System prerequisites and optional tooling (Python, Docker, Git, browser) retain
their own terms. The optional `python:3.13-slim` container includes separately
licensed software; image notices, immutable digest selection, vulnerability
scanning and redistribution review remain release gates. No images are bundled.
The web UI bundles two fonts under the SIL Open Font License 1.1: Inter
(© The Inter Project Authors) and JetBrains Mono (© The JetBrains Mono Project
Authors), latin and latin-ext variable WOFF2 subsets taken from the Fontsource
packages `@fontsource-variable/inter` and `@fontsource-variable/jetbrains-mono`
5.3.0 (dev dependencies). Their license texts ship beside them in
`dikdik/web/fonts/`. The SVG icon is original.
