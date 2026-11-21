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

## Running commands and Python

The agent works with the shell it really has, and runs Python for real computation
(`dikdik/shell_tools.py`, `dikdik/python_tool.py`, `dikdik/local_exec.py`). Both tools
are offered in agent mode with local verification and in a chat whose commands the
user approved; both go through the same approval gate as every command.

### `run_command`

- **One description per shell family.** The tool description matches the shell that
  will run the command: bash (POSIX, including Git Bash on Windows), PowerShell 7, or
  Windows PowerShell 5.1 (no `&&`/`||`; `;` and `if ($?) { ... }`). Syntax hints are
  never offered for a shell the computer does not run.
- **`workdir` instead of `cd`.** A folder relative to the working folder (the project,
  or a chat's own folder). Absolute paths, `..` and missing folders are refused.
- **Exit code and working directory** come back with every result.
- **Timeouts move to the background.** A command still running at `timeout_seconds`
  keeps running instead of being killed. The result has status `running` and a
  `background_id`; a notice reaches the agent with its next tool result when it ends
  (exit code and the end of its output). `background: true` starts servers and
  watchers this way on purpose, after a three-second look at their first output, and
  keeps their standard input open.
- **`command_process`** reads a background command's new output (`wait_seconds` waits
  up to 30 seconds for it to end first), sends it input (`background: true` commands
  only), stops it or lists the conversation's commands. A task sees only its own
  background commands; Stop on the task stops them all.
- **Output** keeps its start and its end (12,000 characters for the model) instead of
  only the end.

Files a background command writes after it moved to the background are not tracked
as the agent's edits; the result tells the agent to read them again before editing.

### `run_python`

A Python 3 script runs in the working folder (or its `workdir`) as an approved
command: the same time limit, resource limits, cancellation and process-tree stop.
It uses the project's `.venv` when there is one, else the user's Python on PATH,
else Dikdik's own. Only what the script prints comes back, capped head and tail.

Before anything runs the code must parse, and string literals naming an existing
absolute path outside the working folder are refused. The script can call Dikdik's
read-only tools from `dikdik_tools` (`read_document`, `statistics`, `web_fetch`, and
`web_search` and `literature_search` when the conversation has them). Calls travel
over a loopback socket open only for that run, with a random token, an allow-list
and a cap of 200 calls; each passes the same permission rules, approvals and hooks
as the model's own call, and reads the same folder.

Risk: a script is medium risk, and high when it starts programs (`subprocess`,
`os.system`), deletes files or opens network connections itself; `web_fetch` from
`dikdik_tools` is the bounded alternative.

### Permission rules and remembered commands

- An **allow** rule for commands matches one command only: `npm test*` approves
  `npm test -- --watch=false` but never `npm test && curl ... | sh`, a pipe, a
  redirect or a `$(...)` substitution. **Deny** and **ask** rules match any command
  in a chain, so `rm *` catches `ls && rm -rf build`.
- A remembered command ending in `*` is a prefix: `git status*` covers
  `git status --short`, with the same single-command rule.
- Ask mode now also asks for any high-risk action, such as sending input to a
  running command.

### Rigidities removed

| Before | Now |
| --- | --- |
| A command that hit its time limit was killed and its work lost | It moves to the background with a completion notice |
| Servers and watchers could only run through `preview_app` | `background: true`, with output and input through `command_process` |
| Subfolders needed `cd`, which the scope check could not always follow | `workdir` |
| The description said "Git Bash when installed, else PowerShell" whatever the computer had | It describes the shell that will actually run |
| Output kept only its last 12,000 characters | Start and end are kept |
| `npm test*` allow rules also approved chained commands | Allow rules cover one command; deny rules see every part |
| Remembered commands matched exactly | A trailing `*` makes a prefix |
| Chat code could run commands, but requests for chat with commands were refused | A chat with explicit execution approval runs commands and Python in its own folder |
