# Bundled editor assets

These assets are served by Dikdik itself. No CDN or favicon service is used.

- **Material Icon Theme 5.39.0**, copyright (c) 2025 Material Extensions, MIT.
  `files/` and the generated filename/folder associations come from the pinned
  `material-icon-theme` development package.
  Source: https://github.com/material-extensions/vscode-material-icon-theme
  Full notice: `LICENSE-material-icon-theme.txt`.
- **Lobe Icons static SVG 1.95.1**, copyright (c) 2023 LobeHub, MIT.
  `providers/` contains original marks selected from the pinned package.
  Source: https://github.com/lobehub/lobe-icons/tree/49a2130
  Full notice: `LICENSE-lobe-icons.txt`.
  The xAI model-provider entry uses the package's Grok product symbol (`grok.svg`),
  rather than the separate xAI company mark. Brand reference:
  https://x.ai/legal/brand-guidelines
- **Highlight.js 11.12.0**, copyright (c) 2006 Ivan Sagalaev, BSD 3-Clause.
  Its engine and 193 language grammars are compiled into the browser bundle.
  Source: https://github.com/highlightjs/highlight.js
  Full notice: `LICENSE-highlight.js.txt`.
- **Visual Studio Code stable blue icon**, Microsoft. `brands/vscode.svg` is
  unmodified from Microsoft's official icon pack, downloaded 2026-10-28:
  https://code.visualstudio.com/assets/branding/visual-studio-code-icons.zip
  Usage guidelines: https://code.visualstudio.com/brand
  Used to identify the external editor and its Open in VS Code actions.
- **NVIDIA vertical logo**, NVIDIA Corporation. `brands/nvidia.svg` is unchanged
  from NVIDIA's official brand page, downloaded 2026-10-28:
  https://www.nvidia.com/content/nvidiaGDC/us/en_US/about-nvidia/legal-info/logo-brand-usage/_jcr_content/root/responsivegrid/nv_container_392921705/nv_container/nv_image.coreimg.svg/1776076920288/nvidia-logo-vert.svg
  Brand reference: https://www.nvidia.com/en-us/about-nvidia/legal-info/logo-brand-usage/
  Used to identify NVIDIA among model providers; displayed on a white tile to
  keep its original green eye and black wordmark legible in either theme.

Product marks remain the property of their respective owners. These notices do
not license Dikdik's original source or assert provider/Microsoft endorsement.

Run `bun run assets:web` after changing the pinned icon packages. The Microsoft
and NVIDIA SVGs are retained separately. Keep the license files when packaging the application.
