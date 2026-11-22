"""run_code: a program in any installed language that can call Dikdik's read-only tools.

The program runs like any approved command (local_exec: the user's approval first, a time limit,
the process tree stopped on cancel, resource limits). Its source, and for compiled languages its
build, live in a temporary ``.dikdik-run-*`` folder inside the folder it runs in, removed afterwards,
so project dependencies (node_modules, go.mod) resolve as they would for the project's own code.

Tool calls go to a loopback HTTP endpoint for this one run: POST /call with the run's random token
in the X-Dikdik-Token header and {"tool", "arguments"} as JSON. Only allow-listed read-only tools,
up to a call cap, through the same code (and folder boundaries) as the model's own calls; the
endpoint closes when the run ends. Helpers for Python, JavaScript, PowerShell and Bash are written
next to the program. Only what it prints returns, capped head and tail.
This is not a sandbox: like run_command, the program can do what the user can.
"""

from __future__ import annotations

import ast
import hmac
import json
import os
import re
import secrets
import shutil
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from . import local_exec
from .shell_tools import head_tail, relative_workdir, resolve_workdir

OUTPUT_CAP = 12000
MAX_CALLS = 200
REQUEST_LIMIT = 1024 * 1024
MAX_FILES = 20
# Read-only tools a program may call, when the conversation itself offers them.
CALLABLE = ("read_document", "statistics", "web_fetch", "web_search", "literature_search")
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?!/)|~[\\/])\S")
QUOTED = re.compile(r'"((?:[^"\\\n]|\\.){1,500})"|\'([^\'\n]{1,500})\'')
EXE = ".exe" if os.name == "nt" else ""

# name: label, source file, runners (program, arguments before the source), compiled build steps.
LANGUAGES: dict[str, dict] = {
    "python": dict(label="Python", source="script.py"),
    "javascript": dict(label="JavaScript", source="script.js",
                       runners=(("node", ()), ("bun", ("run",)), ("deno", ("run", "--allow-all")))),
    "typescript": dict(label="TypeScript", source="script.ts",
                       runners=(("bun", ("run",)), ("deno", ("run", "--allow-all")), ("node", ("--experimental-strip-types",)))),
    "bash": dict(label="Bash", source="script.sh", runners=(("bash", ()),)),
    "powershell": dict(label="PowerShell", source="script.ps1",
                       runners=(("pwsh", ("-NoProfile", "-NonInteractive", "-File")),
                                ("powershell", ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File")))),
    "ruby": dict(label="Ruby", source="script.rb", runners=(("ruby", ()),)),
    "php": dict(label="PHP", source="script.php", runners=(("php", ()),)),
    "perl": dict(label="Perl", source="script.pl", runners=(("perl", ()),)),
    "lua": dict(label="Lua", source="script.lua", runners=(("lua", ()), ("luajit", ()))),
    "r": dict(label="R", source="script.R", runners=(("Rscript", ("--no-save", "--no-restore")),)),
    "julia": dict(label="Julia", source="script.jl", runners=(("julia", ()),)),
    "java": dict(label="Java", source="Main.java", runners=(("java", ()),)),
    "go": dict(label="Go", source="main.go", compiled=True, runners=(("go", ()),)),
    "rust": dict(label="Rust", source="main.rs", compiled=True, runners=(("rustc", ()),)),
    "c": dict(label="C", source="main.c", compiled=True, runners=(("gcc", ()), ("clang", ()), ("cc", ()))),
    "cpp": dict(label="C++", source="main.cpp", compiled=True, runners=(("g++", ()), ("clang++", ()))),
    "csharp": dict(label="C#", source="Program.cs", compiled=True, runners=(("dotnet", ()),)),
}
ALIASES = {"py": "python", "python3": "python", "js": "javascript", "node": "javascript", "nodejs": "javascript",
           "ts": "typescript", "sh": "bash", "shell": "bash", "pwsh": "powershell", "ps1": "powershell", "ps": "powershell",
           "rb": "ruby", "pl": "perl", "rscript": "r", "jl": "julia", "golang": "go", "rs": "rust", "c++": "cpp",
           "cxx": "cpp", "c#": "csharp", "cs": "csharp", "dotnet": "csharp", ".net": "csharp"}
SHELLS = {"bash", "powershell"}


def language_name(language) -> str:
    key = str(language or "").strip().lower()
    return ALIASES.get(key, key)


# --- what this computer has ------------------------------------------------------------
_CACHE: dict = {}


def forget() -> None:
    """Look again next time (a toolchain was just installed)."""
    _CACHE.clear()


def _version(program: str, *arguments: str) -> str:
    from .toolchain import _version as version
    return version([program, *arguments])


def _find(program: str, path: str) -> str | None:
    if program == "bash" and os.name == "nt":
        return local_exec._git_bash()  # System32's bash.exe is WSL, not a shell for this folder
    if program == "powershell" and os.name != "nt":
        return None
    found = shutil.which(program, path=path)
    return found if found and "windowsapps" not in found.lower() else None


def _node_strips_types(node: str) -> bool:
    match = re.search(r"v?(\d+)\.(\d+)", _version(node, "--version"))
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= (22, 6)


def detect(path: str | None = None) -> dict[str, dict]:
    """The languages that can run here: {language: {"program", "runner"}}. Python always can (Dikdik's
    own). Cached per search path, so once per session unless something is installed."""
    search = path if path is not None else local_exec.child_env().get("PATH", os.environ.get("PATH", ""))
    if search in _CACHE:
        return _CACHE[search]
    found: dict[str, dict] = {"python": {"program": "python", "runner": "python"}}
    seen: dict[str, str | None] = {}
    for language, spec in LANGUAGES.items():
        for runner, _arguments in spec.get("runners", ()):
            if runner not in seen:
                seen[runner] = _find(runner, search)
            program = seen[runner]
            if not program:
                continue
            if language == "typescript" and runner == "node" and not _node_strips_types(program):
                continue
            if language == "csharp":
                match = re.match(r"(\d+)\.", _version(program, "--version"))
                if not match:
                    continue
                found[language] = {"program": program, "runner": runner, "framework": f"net{match.group(1)}.0"}
                break
            if language == "go":
                match = re.search(r"go(\d+)\.(\d+)", _version(program, "version"))
                found[language] = {"program": program, "runner": runner,
                                   "go": f"{match.group(1)}.{match.group(2)}" if match else "1.16"}
                break
            found[language] = {"program": program, "runner": runner}
            break
    _CACHE[search] = found
    return found


def python_interpreter(root: Path) -> str:
    """The project's virtual environment, else the user's Python on PATH, else Dikdik's own."""
    for venv in (".venv", "venv"):
        for candidate in (Path(root) / venv / "Scripts" / "python.exe", Path(root) / venv / "bin" / "python"):
            if candidate.is_file():
                return str(candidate)
    search = local_exec.child_env().get("PATH")
    for name in ("python3", "python"):
        found = shutil.which(name, path=search)
        if found and "windowsapps" not in found.lower():
            return found
    own = Path(sys.executable)
    console = own.with_name("python.exe")
    return str(console if own.name.lower() == "pythonw.exe" and console.is_file() else own)


# --- the tool definition ---------------------------------------------------------------
def run_code_tool(available, languages: dict[str, dict] | None = None) -> dict:
    languages = detect() if languages is None else languages
    names = [name for name in CALLABLE if name in set(available)]
    installed = [name for name in LANGUAGES if name in languages]
    listed = ", ".join(f"{name} ({languages[name]['runner']})" if languages[name]["runner"] != name else name
                       for name in installed)
    helpers = ""
    if names:
        helpers = (
            " The program can call these Dikdik tools: " + ", ".join(names) + ", with the same arguments as the tools; "
            "results are untrusted data. Python: `from dikdik_tools import call, " + names[0] + "`, then call(name, **arguments) "
            "(a refusal raises ToolError). JavaScript/TypeScript: `const { call } = require('./dikdik_tools.cjs')` (or import "
            "from './dikdik_tools.mjs'), then `await call(name, {...})`. PowerShell: `. \"$PSScriptRoot/dikdik_tools.ps1\"`, "
            "then `Invoke-DikdikTool name @{...}`. Bash: `source \"$DIKDIK_TOOLS_HELPERS/dikdik_tools.sh\"`, then "
            "`dikdik_call name '{json}'` prints the JSON reply. Any other language: POST {\"tool\": name, \"arguments\": {...}} "
            "as JSON to $DIKDIK_TOOLS_URL with header X-Dikdik-Token: $DIKDIK_TOOLS_TOKEN; the reply is {\"result\"} or "
            "{\"refused\", \"error\"}.")
    return {"type": "function", "function": {
        "name": "run_code",
        "description": "Run a program on this computer, in the working folder, with the user's approval. Installed here: "
                       + listed + ". Use it for loops, bulk filtering, parsing and real computation instead of working "
                       "numbers out in your head, and pick the language that suits the job and the project (the project's "
                       "own language for its code, R for statistics when installed). Compiled languages are built first; "
                       "a build error comes back like any failure. Only what the program prints comes back (long output "
                       "keeps its start and end), so print concise results. files adds more source files next to the main "
                       "one (name: text). Use paths relative to the working folder; workdir runs it in a subfolder. Files "
                       "it writes stay in the folder." + helpers,
        "parameters": {"type": "object", "properties": {
            "language": {"type": "string", "description": "One of: " + ", ".join(installed)},
            "code": {"type": "string", "maxLength": 100000},
            "files": {"type": "object", "additionalProperties": {"type": "string"},
                      "description": "More source files next to the main one: relative name to text"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
            "workdir": {"type": "string"}},
            "required": ["language", "code"], "additionalProperties": False}}}


# --- checks before anything runs -------------------------------------------------------
def _outside(text: str, base: Path) -> bool:
    text = text.strip()
    if len(text) > 500 or "\n" in text or not ABSOLUTE.match(text) or text.startswith("/dev/"):
        return False
    posix = re.match(r"^/([A-Za-z])(/.*)?$", text)  # Git Bash /c/... spelling
    if posix and os.name == "nt":
        text = f"{posix.group(1)}:{posix.group(2) or '/'}"
    try:
        target = Path(os.path.expanduser(text)).resolve()
    except (OSError, ValueError):
        return False
    return target.exists() and target != base and base not in target.parents


def _refuse(text: str, base: Path, what: str) -> None:
    raise ValueError(f"{what} runs in the working folder ({base.as_posix()}); {text} is outside it. "
                     "Use paths relative to the working folder. Nothing ran.")


def check_python(code, root: Path) -> None:
    """Refuse Python that does not parse, or names an existing absolute path outside the folder."""
    if not isinstance(code, str) or not code.strip() or len(code) > 100000:
        raise ValueError("code must contain 1-100000 characters")
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise ValueError(f"The script does not parse: {error.msg} (line {error.lineno}). Nothing ran.") from None
    base = Path(root).resolve()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _outside(node.value, base):
            _refuse(node.value.strip(), base, "Python")


def check_code(language, code, root: Path, files=None) -> str:
    """The language's canonical name, after refusing what is not installed, empty or oversized code,
    unsafe extra file names, and quoted absolute paths to existing places outside the folder."""
    name = language_name(language)
    if name not in LANGUAGES:
        raise ValueError(f"Unknown language {language!r}; known: {', '.join(LANGUAGES)}")
    available = detect()
    if name not in available:
        raise ValueError(f"{LANGUAGES[name]['label']} is not installed on this computer; installed: "
                         f"{', '.join(n for n in LANGUAGES if n in available)}. Nothing ran.")
    _check_files(files)
    base = Path(root).resolve()
    if name == "python":
        check_python(code, root)
    else:
        if not isinstance(code, str) or not code.strip() or len(code) > 100000:
            raise ValueError("code must contain 1-100000 characters")
        if name in SHELLS:
            local_exec.check_project_search(code)
            local_exec.check_command_scope(code, root)
        for match in QUOTED.finditer(code):
            text = match.group(1) if match.group(1) is not None else match.group(2)
            if _outside(text, base):
                _refuse(text.strip(), base, LANGUAGES[name]["label"])
    for text in (files or {}).values():
        for match in QUOTED.finditer(text):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            if _outside(value, base):
                _refuse(value.strip(), base, LANGUAGES[name]["label"])
    return name


def _check_files(files) -> None:
    if files in (None, {}):
        return
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError(f"files must map at most {MAX_FILES} relative file names to their text")
    for name, text in files.items():
        if not isinstance(name, str) or not isinstance(text, str) or len(text) > 100000:
            raise ValueError("files must map relative file names to text of at most 100000 characters")
        parts = name.replace("\\", "/").split("/")
        if not name or name.startswith(("/", "~")) or ":" in name or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"files: {name!r} must be a relative name inside the program's folder, without ..")


# --- the loopback tool endpoint --------------------------------------------------------
class _Server(HTTPServer):
    allow_reuse_address = False  # on Windows SO_REUSEADDR would let another process take the port

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # no reverse DNS lookup of 127.0.0.1
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class ToolBridge:
    """A loopback HTTP endpoint for one run. It refuses requests without the run's token, from
    a browser (an Origin header, another Host), tools outside the allow-list and calls past the
    cap, and is closed when the run ends. Calls are handled one at a time, in arrival order."""

    def __init__(self, call_tool: Callable[[str, dict], dict] | None, allowed, max_calls: int = MAX_CALLS):
        self.call_tool, self.allowed, self.max_calls = call_tool, frozenset(allowed or ()), max_calls
        self.token = secrets.token_hex(24)
        self.calls = 0
        self.refused = 0
        self._open = False
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10
            server_version = "dikdik-tools"
            sys_version = ""

            def log_message(self, *args):
                pass

            def do_POST(self):
                bridge._handle(self)

            def do_GET(self):
                bridge._send(self, 405, {"refused": True, "error": "POST JSON to /call"})

            do_PUT = do_DELETE = do_OPTIONS = do_PATCH = do_HEAD = do_GET

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/call"

    def env(self) -> dict[str, str]:
        return {"DIKDIK_TOOLS_URL": self.url, "DIKDIK_TOOLS_TOKEN": self.token}

    def __enter__(self):
        self._open = True
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._open = False
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()

    @staticmethod
    def _send(handler, status: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode("utf-8")
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:
            pass

    def _handle(self, handler) -> None:
        if not self._open:
            return self._send(handler, 410, {"refused": True, "error": "This run has ended"})
        if handler.path != "/call":
            return self._send(handler, 404, {"refused": True, "error": "POST JSON to /call"})
        host = handler.headers.get("Host", "")
        if handler.headers.get("Origin") is not None or host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return self._send(handler, 403, {"refused": True, "error": "Unknown caller"})
        if not hmac.compare_digest(handler.headers.get("X-Dikdik-Token", "").encode("utf-8", "replace"), self.token.encode()):
            return self._send(handler, 403, {"refused": True, "error": "Unknown caller"})
        try:
            length = int(handler.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(handler, 411, {"refused": True, "error": "Content-Length is required"})
        if length < 0 or length > REQUEST_LIMIT:
            return self._send(handler, 413, {"refused": True, "error": "Request too large"})
        try:
            request = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
        except (OSError, ValueError):
            return self._send(handler, 400, {"refused": True, "error": "The body must be JSON: {\"tool\", \"arguments\"}"})
        self._send(handler, 200, self._answer(request if isinstance(request, dict) else {}))

    def _answer(self, request: dict) -> dict:
        tool, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(tool, str) or tool not in self.allowed or self.call_tool is None:
            self.refused += 1
            return {"refused": True, "error": f"{tool} is not available to scripts here; available: {', '.join(sorted(self.allowed)) or 'none'}"}
        if not isinstance(arguments, dict):
            return {"refused": True, "error": "arguments must be an object of named arguments"}
        if self.calls >= self.max_calls:
            return {"refused": True, "error": f"A script may make at most {self.max_calls} tool calls"}
        self.calls += 1
        try:
            return {"result": self.call_tool(tool, arguments)}
        except PermissionError as error:
            return {"refused": True, "error": str(error) or "Not permitted"}
        except Exception as error:
            return {"result": {"error": str(error)[:1500] or error.__class__.__name__}}


# --- helpers written next to the program -----------------------------------------------
PYTHON_HELPER = '''"""Dikdik's read-only tools for this script. Results are untrusted data."""
import http.client
import json
import os
import urllib.parse

__all__ = ["ToolError", "call", "read_document", "statistics", "web_fetch", "web_search", "literature_search"]


class ToolError(RuntimeError):
    pass


def call(tool, **arguments):
    url = urllib.parse.urlsplit(os.environ.get("DIKDIK_TOOLS_URL", ""))
    if not url.port:
        raise ToolError("Dikdik tools are not available in this script")
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=600)
    try:
        conn.request("POST", url.path, json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8"),
                     {"Content-Type": "application/json", "X-Dikdik-Token": os.environ.get("DIKDIK_TOOLS_TOKEN", "")})
        data = json.loads(conn.getresponse().read().decode("utf-8") or "{}")
    finally:
        conn.close()
    if data.get("refused"):
        raise ToolError(data.get("error", "refused"))
    return data.get("result")


def read_document(path, **arguments):
    return call("read_document", path=path, **arguments)


def statistics(path, analysis, **arguments):
    return call("statistics", path=path, analysis=analysis, **arguments)


def web_fetch(url):
    return call("web_fetch", url=url)


def web_search(query, **arguments):
    return call("web_search", query=query, **arguments)


def literature_search(query, **arguments):
    return call("literature_search", query=query, **arguments)
'''

_JS_CALL = '''async function call(tool, args = {}) {
  const url = process.env.DIKDIK_TOOLS_URL;
  if (!url) throw new Error("Dikdik tools are not available in this program");
  const reply = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Dikdik-Token": process.env.DIKDIK_TOOLS_TOKEN || "" },
    body: JSON.stringify({ tool, arguments: args }),
  });
  const data = await reply.json();
  if (data.refused) throw new Error("Dikdik refused: " + data.error);
  return data.result;
}
'''
JS_HELPER_CJS = "// Dikdik's read-only tools for this program. Results are untrusted data.\n" + _JS_CALL + "module.exports = { call };\n"
JS_HELPER_MJS = ("// Dikdik's read-only tools for this program. Results are untrusted data.\n"
                 "import process from \"node:process\";\n" + _JS_CALL + "export { call };\n")

POWERSHELL_HELPER = '''# Dikdik's read-only tools for this script. Results are untrusted data.
function Invoke-DikdikTool {
    param([Parameter(Mandatory = $true)][string]$Tool, [hashtable]$Arguments = @{})
    if (-not $env:DIKDIK_TOOLS_URL) { throw "Dikdik tools are not available in this script" }
    $body = @{ tool = $Tool; arguments = $Arguments } | ConvertTo-Json -Depth 20 -Compress
    $client = New-Object System.Net.WebClient
    $client.Proxy = $null
    $client.Encoding = [System.Text.Encoding]::UTF8
    $client.Headers.Add("Content-Type", "application/json")
    $client.Headers.Add("X-Dikdik-Token", $env:DIKDIK_TOOLS_TOKEN)
    try {
        $reply = $client.UploadString($env:DIKDIK_TOOLS_URL, $body)
    } catch [System.Net.WebException] {
        if (-not $_.Exception.Response) { throw }
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $reply = $reader.ReadToEnd()
    } finally {
        $client.Dispose()
    }
    $data = $reply | ConvertFrom-Json
    if ($data.refused) { throw "Dikdik refused: $($data.error)" }
    return $data.result
}
'''

BASH_HELPER = '''# Dikdik's read-only tools for this script. Results are untrusted data.
# dikdik_call TOOL '{"json": "arguments"}' prints the JSON reply: {"result": ...} or {"refused": true, "error": ...}.
dikdik_call() {
  local tool="$1" args="${2:-}"
  [ -n "$args" ] || args='{}'
  if [ -z "${DIKDIK_TOOLS_URL:-}" ]; then echo "Dikdik tools are not available in this script" >&2; return 2; fi
  printf 'header = "X-Dikdik-Token: %s"\\n' "$DIKDIK_TOOLS_TOKEN" | curl -sS --noproxy '*' -K - -X POST \\
    -H "Content-Type: application/json" --data-binary "{\\"tool\\":\\"$tool\\",\\"arguments\\":$args}" "$DIKDIK_TOOLS_URL"
}
'''

HELPERS = {"dikdik_tools.py": PYTHON_HELPER, "dikdik_tools.cjs": JS_HELPER_CJS, "dikdik_tools.mjs": JS_HELPER_MJS,
           "dikdik_tools.ps1": POWERSHELL_HELPER, "dikdik_tools.sh": BASH_HELPER}


# --- running ---------------------------------------------------------------------------
def _write(path: Path, text: str, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8", newline="\n")


def _commands(name: str, info: dict, root: Path, folder: Path) -> tuple[list[str] | None, list[str]]:
    """(build argv or None, run argv) for the program in ``folder``."""
    spec = LANGUAGES[name]
    source = folder / spec["source"]
    program = info["program"]
    arguments = dict(spec.get("runners", ())).get(info["runner"], ())
    binary = str(folder / ("program" + EXE))
    if name == "python":
        return None, [python_interpreter(root), "-X", "utf8", str(source)]
    if name == "go":
        return [program, "build", "-o", binary, "."], [binary]
    if name == "rust":
        return [program, "--edition", "2021", "-O", "-o", binary, str(source)], [binary]
    if name in ("c", "cpp"):
        sources = sorted(str(p) for p in folder.rglob("*") if p.suffix in (".c", ".cc", ".cpp", ".cxx"))
        return [program, "-O2", "-o", binary, *sources], [binary]
    if name == "csharp":
        return ([program, "build", "-nologo", "-v", "q", "-o", str(folder / "out")],
                [program, str(folder / "out" / "program.dll")])
    if name == "bash":
        return None, [program, source.as_posix()]
    return None, [program, *arguments, str(source)]


def _cleanup(folder: Path) -> None:
    for _attempt in range(5):  # a just-stopped program can hold its files briefly on Windows
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.2)


def run(root: Path, language, code: str, cancel: threading.Event, *, files=None, timeout: float = 120, workdir=None,
        call_tool: Callable[[str, dict], dict] | None = None, allowed=(), on_chunk=None,
        env: dict[str, str] | None = None, limits: dict | None = None) -> dict:
    """Run ``code`` in ``language`` in ``root`` (or its ``workdir``); ``call_tool(name, arguments)`` serves the allowed tools."""
    name = check_code(language, code, root, files)
    info = detect()[name]
    spec = LANGUAGES[name]
    folder_path = resolve_workdir(root, workdir)
    scratch = folder_path / f".dikdik-run-{secrets.token_hex(6)}"
    started = time.monotonic()
    try:
        scratch.mkdir()
        for helper, text in HELPERS.items():
            _write(scratch / helper, text, bom=helper.endswith(".ps1"))
        for relative, text in (files or {}).items():
            _write(scratch / relative.replace("\\", "/"), text)
        _write(scratch / spec["source"], code, bom=name == "powershell" and info["runner"] == "powershell")
        if name == "go" and not (scratch / "go.mod").exists():
            _write(scratch / "go.mod", "module program\n")
        if name == "csharp":
            _write(scratch / "program.csproj", (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                f'<TargetFramework>{info["framework"]}</TargetFramework><ImplicitUsings>enable</ImplicitUsings>'
                '<Nullable>enable</Nullable><AssemblyName>program</AssemblyName></PropertyGroup></Project>\n'))
        build, command = _commands(name, info, root, scratch)
        label = f"run_code {name}"
        with ToolBridge(call_tool, set(allowed) & set(CALLABLE)) as bridge:
            child = {**(env or {}), **bridge.env(), "DIKDIK_TOOLS_HELPERS": str(scratch)}
            result = None
            if build is not None:
                build_env = {**(env or {}), **({"GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod"} if name == "go" else {}),
                             **({"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"} if name == "csharp" else {})}
                result = local_exec.run(scratch, f"build {name}", cancel, timeout=timeout, on_chunk=on_chunk,
                                        env=build_env or None, limits=limits, argv=build)
                if result.get("status") != "passed":
                    result["phase"] = "build"
            if result is None or result.get("status") == "passed":
                remaining = max(1.0, timeout - (time.monotonic() - started))
                built = result
                result = local_exec.run(folder_path, label, cancel, timeout=remaining, on_chunk=on_chunk, env=child,
                                        limits=limits, argv=command)
                if built is not None and built.get("output", "").strip():
                    result["output"] = built["output"].rstrip() + "\n" + str(result.get("output", ""))
        result["tool_calls"] = bridge.calls
        if bridge.refused:
            result["refused_tool_calls"] = bridge.refused
    finally:
        _cleanup(scratch)
    output = str(result.get("output", ""))
    for spelling in (str(scratch), scratch.as_posix()):
        output = output.replace(spelling + os.sep, "").replace(spelling + "/", "").replace(spelling, ".")
    result["output"] = head_tail(output, OUTPUT_CAP)
    result["language"] = name
    result["workdir"] = relative_workdir(root, folder_path)
    return result
