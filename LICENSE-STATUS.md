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
The editor bundles Highlight.js 11.12.0 (BSD-3-Clause) and its 193 language
grammars at build time, plus SVG assets/associations from Material Icon Theme
5.39.0 (MIT) and Lobe Icons static SVG 1.95.1 (MIT). These are pinned development
packages; a running installation needs neither npm nor an external CDN. Full
notices ship under `dikdik/web/assets/`, alongside the source record for the
unmodified Microsoft Visual Studio Code stable icon. See
[`assets/NOTICES.md`](dikdik/web/assets/NOTICES.md).
The private Bun frontend package uses `UNLICENSED` to avoid asserting an
owner-unapproved license grant. Its frontend compiler development
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

The optional browser image installs **playwright-core 1.64.1 (Apache-2.0)** and
uses the matching Microsoft Playwright **v1.64.1-noble** base image. Chromium,
Ubuntu, Node.js and other image components retain their own notices and licenses;
the Playwright license does not cover the entire image. The Dockerfile is original
Dikdik code, and no container image or Playwright source is bundled in this repo.
The operator builds/downloads it explicitly. Immutable digest selection,
vulnerability review and image redistribution/license review remain release gates.
Primary references: [Playwright license](https://github.com/microsoft/playwright/blob/main/LICENSE)
and [Docker image documentation](https://playwright.dev/docs/docker).
