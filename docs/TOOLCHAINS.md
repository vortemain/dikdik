# Setting up the computer for a project

Projects need toolchains the computer may not have: a compiler, an SDK, a runtime or a
package. The agent sets them up instead of stopping at "command not found"
(`dikdik/toolchain.py`).

## What the agent gets

- **`environment_check`** (read-only, low risk). It reports this computer's OS,
  architecture, package managers and the toolchains present with their versions. It
  lists the toolchains the project's files call for, each marked installed or missing,
  with any pinned version (`.nvmrc`, `engines`, `.python-version`, `go.mod`,
  `rust-toolchain`, `global.json`, `pom.xml`). Each missing one comes with its exact
  non-interactive install command, alternatives and a verify command. It also gives the
  commands that install the project's own dependencies.
- **A diagnosis on every failed `run_command`.** When the output says a program,
  module or toolchain is missing, the result carries a `diagnosis` and a `next_step`
  with the fix for this computer. For example, `zig: command not found` comes back with
  `winget install --id zig.zig ...` and `zig --version`. `No module named 'cv2'` comes
  back with `pip install opencv-python`. `Cannot find module 'zod'` comes back with
  `pnpm add zod` (following the lockfile). `'vite' is not recognized` comes back with
  the project's own dependency install. The MSVC `link.exe` error comes back with the
  C++ Build Tools, or with how to activate them when they are installed.
- **Instructions.** Install prerequisites first, verify each, then continue. Don't
  hand an install back to the user unless it needs an administrator password or a
  dialog. Research the official method for tools outside the catalog.

## Coverage

Python, Node.js (npm, pnpm, Yarn, Bun, Deno), uv and Poetry, Go, Rust, Java (Maven,
Gradle, Kotlin), .NET, C/C++ (GCC/Clang, the Microsoft C++ Build Tools, CMake, Make,
Ninja), PHP and Composer, Ruby and Bundler, Dart and Flutter, Swift, Zig, R, Docker,
PostgreSQL, Android platform tools, Arduino CLI, PlatformIO, Protocol Buffers, FFmpeg,
Terraform, kubectl and Git.

Package managers by platform:

- **Windows:** winget, Scoop, Chocolatey.
- **macOS:** Homebrew, and `xcode-select` for the Command Line Tools.
- **Linux:** apt, dnf, pacman, zypper and snap, plus the official installers for
  rustup, Bun, Deno and uv.

Language-level tools prefer the language's own manager (npm, pip, gem) when that
language is installed. The winget and Chocolatey package names were checked against
the live catalogs.

## Behaviour

- **New tools are found immediately.** Every command's PATH includes the folders saved
  by installers (the Windows registry PATH for the machine and the user) and the usual
  per-user tool folders (`~/.cargo/bin`, `~/.bun/bin`, `~/go/bin`, `~/.local/bin`, npm
  globals, Scoop shims, WinGet links, Homebrew). These are appended, so the user's own
  PATH order still wins. A tool installed by one command is found by the next, with no
  restart.
- **Installers get time.** A command recognised as an installer may run up to 30
  minutes instead of the usual 10. Afterwards Dikdik forgets what it had cached about
  installed tools.
- **Approvals still apply.** Installing software is a high-risk action (`winget`,
  `choco`, `scoop`, `brew`, `dnf`, `pacman`, `zypper`, `snap`, `rustup`,
  `xcode-select`, `sudo`, and package installs), so Ask mode prompts for it.
- **Administrator rights.** On Linux, system package managers run with `sudo -n` only
  when sudo needs no password (or as root). Otherwise the agent tells the user exactly
  which command to run. On Windows, machine-wide installers raise their own UAC prompt.
- **Python isolation.** Project dependencies install into a project `.venv`, which
  avoids PEP 668 "externally managed" errors.
