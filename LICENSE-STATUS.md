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

## Optional container images

The repository includes Dockerfiles and browser-runtime configuration; release
downloads do not bundle container images. Operators build or download images
separately and retain the notices applicable to their contents.

The optional browser image uses `playwright-core` 1.64.1 (Apache-2.0) and the
Microsoft Playwright `v1.64.1-noble` base image. Chromium, Ubuntu, Node.js, and
other image components retain their own terms. The Python runtime image likewise
contains separately licensed software. Review those components and image notices
before redistributing an image.

See the [Playwright license](https://github.com/microsoft/playwright/blob/main/LICENSE)
and [image documentation](https://playwright.dev/docs/docker) for those components,
and [Security](SECURITY.md) for deployment and image-review considerations.
