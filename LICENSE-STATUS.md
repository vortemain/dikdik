# Licensing and third-party notices

## Dikdik

Original Dikdik code is licensed under the **MIT License**, copyright (c) 2026
vortemain and Dikdik contributors. See [LICENSE](LICENSE) for the complete grant
and copyright notice.

This document describes the code, assets, and dependencies used by Dikdik
releases. Third-party components keep their own licenses and notices.

## Bundled components

| Component | Version | License | Included notice |
| --- | --- | --- | --- |
| Highlight.js and its language grammars | 11.12.0 | BSD-3-Clause | [Highlight.js license](dikdik/web/assets/LICENSE-highlight.js.txt) |
| Material Icon Theme icons and associations | 5.39.0 | MIT | [Material Icon Theme license](dikdik/web/assets/LICENSE-material-icon-theme.txt) |
| Lobe Icons static SVG assets | 1.95.1 | MIT | [Lobe Icons license](dikdik/web/assets/LICENSE-lobe-icons.txt) |
| Inter variable font subsets | 5.3.0 | SIL Open Font License 1.1 | [Inter license](dikdik/web/fonts/LICENSE-Inter.txt) |
| JetBrains Mono variable font subsets | 5.3.0 | SIL Open Font License 1.1 | [JetBrains Mono license](dikdik/web/fonts/LICENSE-JetBrainsMono.txt) |

The Dikdik logo is original project artwork covered by the root MIT license.
The Visual Studio Code and NVIDIA marks identify integrations and remain the
property of their respective owners. Their source and usage references, along
with further asset attribution, appear in [asset notices](dikdik/web/assets/NOTICES.md).
Dikdik's MIT license does not grant rights to third-party trademarks or replace
component license terms.

The wheel includes the bundled notice files, and distribution metadata also
includes the applicable license texts. Keep those notices when redistributing
Dikdik and its bundled components.

## Separately installed dependencies

Dikdik's Python code uses the standard library. On Windows, installation also
supplies `tzdata` for IANA time zones. That Python-maintained package uses
Apache-2.0 for its code and public-domain time-zone data; its own notices remain
with the separately installed package. It is not bundled into Dikdik's wheel.

Python, Git, browsers, Rust, Docker, and account-integration CLIs retain their
own licenses. Install them separately when their corresponding features are needed.

## Development and packaging tools

- **TypeScript 6.0.3** is Apache-2.0 and is a development compiler/type-checker.
- **Bun** is MIT-licensed and builds the frontend. The running application uses
  the packaged browser bundle.
- **Setuptools** is MIT-licensed and is the Python build backend. Release tooling
  is pinned in [scripts/requirements-release.txt](scripts/requirements-release.txt);
  each tool retains its own license.

The private frontend package declares MIT for original Dikdik source. Installing
build tools does not relicense those tools or their dependencies under Dikdik's
license.

## Optional hosting image

Dikdik does not use container images at runtime (the Docker test runner and browser
image were removed on 2026-11-02). The repository keeps one `Dockerfile` for hosting
on a server; release downloads do not bundle an image. Its `python:3.13-slim` base
contains separately licensed software; review its notices before redistributing an
image, and see [Security](SECURITY.md) for deployment considerations.
