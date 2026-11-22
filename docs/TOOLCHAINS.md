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
user approved; both go through the same approval gate as every command. Every chat offers
them, in the chat's own folder, and asks the user before each command (`confirmation: ask`).

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

### `run_code` (formerly `run_python`)

A program in any language installed on this computer runs in the working folder (or
its `workdir`) as an approved command: the same time limit, resource limits,
cancellation and process-tree stop. Arguments: `language`, `code`, optional `files`
(more source files next to the main one, name to text, at most 20), `workdir` and
`timeout_seconds`. `run_python` calls from earlier conversations still run, as
`run_code` in Python, and permission rules written for `run_python` still apply to
Python programs.

| Language | Runs with (first one found) | How |
| --- | --- | --- |
| python | the project's `.venv`, the user's Python, Dikdik's own | interpreted |
| javascript | node, bun, deno | interpreted |
| typescript | bun, deno, node 22.6 or later (`--experimental-strip-types`) | interpreted |
| bash | bash (Git Bash on Windows, never WSL's `bash.exe`) | interpreted |
| powershell | pwsh, Windows PowerShell | `-File`, `-NoProfile -NonInteractive`; `-ExecutionPolicy Bypass` only when the effective policy would refuse an unsigned local script |
| ruby, php, perl, lua, r (Rscript), julia | that interpreter | interpreted |
| java | java 11 or later | single-file source launch |
| go | go | `go build` with its own `go.mod`, `GOTOOLCHAIN=local` |
| rust | rustc | `rustc -O` |
| c, cpp | gcc/clang, g++/clang++ | `-O2` |
| csharp | dotnet | a throwaway console project, `dotnet build` |

Toolchains are found once per session (per search path) and again after an install;
the tool description lists only the languages found, so the model never offers one
that is missing. Aliases such as `js`, `ts`, `sh`, `pwsh`, `c#` and `golang` are
accepted.

The source, the helpers below and any build output live in a temporary
`.dikdik-run-*` folder inside the folder the program runs in, removed afterwards.
It holds a `.gitignore` of `*` so Git never lists it, Dikdik's file inventory skips it
like any dot-folder, and one left behind by an interrupted run is removed by the next
run in that folder once it is an hour old.
Inside the project means Node resolves the project's `node_modules`. A compiled
language is built there first (the build gets no tool token and counts against the
same time limit); a build error returns with `phase: "build"`. Only what the program
prints comes back, capped head and tail, with the temporary folder's path removed.

Before anything runs: Python must parse; quoted string literals in any language that
name an existing absolute path outside the working folder are refused; Bash and
PowerShell programs also get `run_command`'s checks for `cd` outside the folder and
whole-drive searches; extra file names must be relative, without `..`.

**Tool bridge.** For each run Dikdik opens a loopback HTTP endpoint on 127.0.0.1 with
a random port and a random 48-character token. A program calls a tool with
`POST $DIKDIK_TOOLS_URL`, header `X-Dikdik-Token: $DIKDIK_TOOLS_TOKEN`, body
`{"tool": "...", "arguments": {...}}`; the reply is `{"result": ...}` or
`{"refused": true, "error": "..."}`. Only read-only tools the conversation already
offers are allowed (`read_document`, `statistics`, `web_fetch`, `web_search`,
`literature_search`), at most 200 calls a run. Each request is read on its own thread,
so a caller that never finishes its request cannot stall the others, and the tool calls
themselves run one at a time; a call still waiting when the run ends is refused. Each call passes
the same permission rules, approvals and hooks as the model's own call, and reads the
same folder. Requests with a wrong token, an `Origin` header or a Host other than the
loopback address and port are refused (a web page cannot reach it), and the endpoint
closes when the run ends. Helpers sit next to the program:

| Language | Helper |
| --- | --- |
| Python | `from dikdik_tools import call, read_document` (raises `ToolError` on refusal) |
| JavaScript/TypeScript | `const { call } = require('./dikdik_tools.cjs')` or `import { call } from './dikdik_tools.mjs'`; `await call(name, {...})` |
| PowerShell | `. "$PSScriptRoot/dikdik_tools.ps1"`; `Invoke-DikdikTool name @{...}` |
| Bash | `source "$DIKDIK_TOOLS_HELPERS/dikdik_tools.sh"`; `dikdik_call name '{json}'` (needs curl) |

The helpers bypass HTTP proxies, and the Bash helper passes the token to curl on
standard input rather than on its command line.

Risk: a program is medium risk. Python is high when it starts programs, deletes files
or opens network connections itself. Other languages are high for the same reasons
(`child_process`, `os/exec`, `Process.Start`, `Remove-Item`, `fs.rm`, `fetch`,
`Invoke-WebRequest`, `HttpClient` and similar), and Bash and PowerShell also get
`run_command`'s checks (recursive deletes, installs, `git push`). The `web_fetch`
tool through the bridge is the bounded alternative to direct network access.

Choosing a language: the model is told to use the project's own language and
toolchain for the project's code and checks (Node for a JavaScript project, `go test`,
`cargo test`, `dotnet test`), R for statistics when Rscript is installed, and Python
otherwise.

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
