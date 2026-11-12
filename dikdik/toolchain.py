"""Software toolchains: what a project needs from this computer, what is already here, and how to
install and verify the rest, so an agent sets a machine up instead of stopping at "not found".

Covers web, backend, mobile, desktop, systems, data and embedded work: Python, Node.js and its
package managers, Go, Rust, Java (Maven, Gradle, Kotlin), .NET, C/C++ (compilers, CMake, Make,
Ninja, the Microsoft C++ Build Tools), PHP, Ruby, Dart/Flutter, Swift, Zig, R, Docker, databases,
Android platform tools, Arduino, Protocol Buffers, FFmpeg, Terraform and kubectl.

Install commands use the package manager this computer has: winget, Scoop or Chocolatey on
Windows, Homebrew on macOS, apt, dnf, pacman or zypper on Linux (non-interactive; system package
managers need administrator rights, which an agent cannot type, so that case is reported). The
winget and Chocolatey package names were checked against the live catalogs.

Pure functions (``diagnose``, ``project_needs``, ``install_plan``, ``remedy``) take what they need
as arguments; ``probe`` looks at this computer and caches the answer briefly.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Callable, Iterable

# key: label, programs (any one found means installed), version argv tail, packages per manager.
# npm/pip/gem: installed with that language's own manager. script_posix/script_windows: the
# official installer when no package exists. post: commands that finish an install.
TOOLS: dict[str, dict] = {
    "python": dict(label="Python 3", programs=("python3", "python", "py"), winget="Python.Python.3.13", scoop="python", choco="python",
                   brew="python@3.13", apt="python3 python3-pip python3-venv", dnf="python3 python3-pip", pacman="python python-pip",
                   zypper="python3 python3-pip", docs="https://www.python.org/downloads/"),
    "node": dict(label="Node.js with npm", programs=("node",), winget="OpenJS.NodeJS.LTS", scoop="nodejs-lts", choco="nodejs-lts",
                 brew="node", apt="nodejs npm", dnf="nodejs npm", pacman="nodejs npm", zypper="nodejs npm", docs="https://nodejs.org/en/download"),
    "pnpm": dict(label="pnpm", programs=("pnpm",), needs=("node",), npm="pnpm", winget="pnpm.pnpm", brew="pnpm", docs="https://pnpm.io/installation"),
    "yarn": dict(label="Yarn", programs=("yarn",), needs=("node",), npm="yarn", winget="Yarn.Yarn", brew="yarn", docs="https://yarnpkg.com/getting-started/install"),
    "bun": dict(label="Bun", programs=("bun",), winget="Oven-sh.Bun", scoop="bun", brew="oven-sh/bun/bun", npm="bun",
                script_posix="curl -fsSL https://bun.sh/install | bash", docs="https://bun.sh/docs/installation"),
    "deno": dict(label="Deno", programs=("deno",), winget="DenoLand.Deno", scoop="deno", choco="deno", brew="deno",
                 script_posix="curl -fsSL https://deno.land/install.sh | sh", docs="https://docs.deno.com/runtime/getting_started/installation/"),
    "uv": dict(label="uv (Python packaging)", programs=("uv",), winget="astral-sh.uv", scoop="uv", brew="uv", pip="uv",
               script_posix="curl -LsSf https://astral.sh/uv/install.sh | sh", docs="https://docs.astral.sh/uv/getting-started/installation/"),
    "poetry": dict(label="Poetry", programs=("poetry",), needs=("python",), pip="poetry", brew="poetry", docs="https://python-poetry.org/docs/"),
    "go": dict(label="Go", programs=("go",), version=("version",), winget="GoLang.Go", scoop="go", choco="golang", brew="go",
               apt="golang-go", dnf="golang", pacman="go", zypper="go", docs="https://go.dev/doc/install"),
    "rust": dict(label="Rust (rustup, cargo)", programs=("cargo", "rustc"), winget="Rustlang.Rustup", scoop="rustup", choco="rustup.install",
                 brew="rustup", pacman="rustup", post=("rustup default stable",),
                 script_posix="curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
                 docs="https://www.rust-lang.org/tools/install", windows_needs=("msvc",)),
    "java": dict(label="Java JDK 21", programs=("javac",), version=("-version",), winget="EclipseAdoptium.Temurin.21.JDK", choco="temurin21",
                 brew="openjdk@21", apt="openjdk-21-jdk", dnf="java-21-openjdk-devel", pacman="jdk21-openjdk", zypper="java-21-openjdk-devel",
                 docs="https://adoptium.net/installation/"),
    "maven": dict(label="Apache Maven", programs=("mvn",), needs=("java",), scoop="maven", choco="maven", brew="maven", apt="maven",
                  dnf="maven", pacman="maven", zypper="maven", docs="https://maven.apache.org/install.html"),
    "gradle": dict(label="Gradle", programs=("gradle",), needs=("java",), scoop="gradle", choco="gradle", brew="gradle", apt="gradle",
                   pacman="gradle", docs="https://gradle.org/install/"),
    "kotlin": dict(label="Kotlin compiler", programs=("kotlinc",), needs=("java",), scoop="kotlin", choco="kotlinc", brew="kotlin",
                   pacman="kotlin", docs="https://kotlinlang.org/docs/command-line.html"),
    "dotnet": dict(label=".NET SDK 8", programs=("dotnet",), winget="Microsoft.DotNet.SDK.8", scoop="dotnet-sdk", choco="dotnet-8.0-sdk",
                   brew="--cask dotnet-sdk", apt="dotnet-sdk-8.0", dnf="dotnet-sdk-8.0", pacman="dotnet-sdk", docs="https://dotnet.microsoft.com/download"),
    "cc": dict(label="C/C++ compiler", programs=("gcc", "clang", "cc"), winget="BrechtSanders.WinLibs.POSIX.UCRT", scoop="gcc", choco="mingw",
               brew_command="xcode-select --install", apt="build-essential", dnf="gcc gcc-c++ make", pacman="base-devel",
               zypper="gcc gcc-c++ make", docs="https://code.visualstudio.com/docs/cpp/config-mingw"),
    "msvc": dict(label="Microsoft C++ Build Tools", programs=(), windows_only=True, winget="Microsoft.VisualStudio.2022.BuildTools",
                 winget_override="--wait --quiet --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended",
                 docs="https://visualstudio.microsoft.com/visual-cpp-build-tools/"),
    "cmake": dict(label="CMake", programs=("cmake",), winget="Kitware.CMake", scoop="cmake", choco="cmake", brew="cmake", apt="cmake",
                  dnf="cmake", pacman="cmake", zypper="cmake", docs="https://cmake.org/download/"),
    "make": dict(label="GNU Make", programs=("make", "mingw32-make"), winget="ezwinports.make", scoop="make", choco="make", brew="make",
                 apt="make", dnf="make", pacman="make", zypper="make"),
    "ninja": dict(label="Ninja", programs=("ninja",), winget="Ninja-build.Ninja", scoop="ninja", choco="ninja", brew="ninja",
                  apt="ninja-build", dnf="ninja-build", pacman="ninja", zypper="ninja"),
    "git": dict(label="Git", programs=("git",), winget="Git.Git", scoop="git", choco="git", brew="git", apt="git", dnf="git",
                pacman="git", zypper="git", docs="https://git-scm.com/downloads"),
    "php": dict(label="PHP", programs=("php",), winget="PHP.PHP.8.3", scoop="php", choco="php", brew="php", apt="php-cli", dnf="php-cli",
                pacman="php", zypper="php8", docs="https://www.php.net/downloads"),
    "composer": dict(label="Composer", programs=("composer",), needs=("php",), scoop="composer", choco="composer", brew="composer",
                     apt="composer", pacman="composer", docs="https://getcomposer.org/download/"),
    "ruby": dict(label="Ruby", programs=("ruby",), winget="RubyInstallerTeam.RubyWithDevKit.3.3", scoop="ruby", choco="ruby", brew="ruby",
                 apt="ruby-full", dnf="ruby ruby-devel", pacman="ruby", zypper="ruby-devel", docs="https://www.ruby-lang.org/en/documentation/installation/"),
    "bundler": dict(label="Bundler", programs=("bundle",), needs=("ruby",), gem="bundler"),
    "dart": dict(label="Dart SDK", programs=("dart",), winget="Google.DartSDK", scoop="dart", choco="dart-sdk", brew="dart-lang/dart/dart",
                 docs="https://dart.dev/get-dart"),
    "flutter": dict(label="Flutter", programs=("flutter",), scoop="extras/flutter", choco="flutter", brew="--cask flutter",
                    snap="flutter --classic", docs="https://docs.flutter.dev/get-started/install"),
    "swift": dict(label="Swift", programs=("swift",), winget="Swift.Toolchain", brew_command="xcode-select --install",
                  docs="https://www.swift.org/install/"),
    "zig": dict(label="Zig", programs=("zig",), winget="zig.zig", scoop="zig", choco="zig", brew="zig", pacman="zig",
                snap="zig --classic --beta", docs="https://ziglang.org/download/"),
    "r": dict(label="R", programs=("Rscript",), winget="RProject.R", scoop="r", choco="r.project", brew="r", apt="r-base", dnf="R",
              pacman="r", docs="https://cran.r-project.org/"),
    "docker": dict(label="Docker", programs=("docker",), winget="Docker.DockerDesktop", choco="docker-desktop", brew="--cask docker",
                   apt="docker.io", pacman="docker", note="Docker Desktop must be started (and may ask to sign out or restart) before docker commands work.",
                   docs="https://docs.docker.com/get-docker/"),
    "postgresql": dict(label="PostgreSQL 16", programs=("psql",), winget="PostgreSQL.PostgreSQL.16", scoop="postgresql", choco="postgresql16",
                       brew="postgresql@16", apt="postgresql", dnf="postgresql-server", pacman="postgresql", docs="https://www.postgresql.org/download/"),
    "adb": dict(label="Android platform tools (adb)", programs=("adb",), winget="Google.PlatformTools", scoop="adb", choco="adb",
                brew="--cask android-platform-tools", apt="adb", dnf="android-tools", pacman="android-tools"),
    "arduino": dict(label="Arduino CLI", programs=("arduino-cli",), winget="ArduinoSA.CLI", scoop="arduino-cli", choco="arduino-cli",
                    brew="arduino-cli", docs="https://arduino.github.io/arduino-cli/latest/installation/"),
    "platformio": dict(label="PlatformIO", programs=("pio", "platformio"), needs=("python",), pip="platformio"),
    "protoc": dict(label="Protocol Buffers compiler", programs=("protoc",), winget="Google.Protobuf", scoop="protobuf", choco="protoc",
                   brew="protobuf", apt="protobuf-compiler", dnf="protobuf-compiler", pacman="protobuf"),
    "ffmpeg": dict(label="FFmpeg", programs=("ffmpeg",), version=("-version",), winget="Gyan.FFmpeg", scoop="ffmpeg", choco="ffmpeg",
                   brew="ffmpeg", apt="ffmpeg", pacman="ffmpeg"),
    "terraform": dict(label="Terraform", programs=("terraform",), winget="Hashicorp.Terraform", scoop="terraform", choco="terraform",
                      brew="hashicorp/tap/terraform", docs="https://developer.hashicorp.com/terraform/install"),
    "kubectl": dict(label="kubectl", programs=("kubectl",), version=("version", "--client"), winget="Kubernetes.kubectl", scoop="kubectl",
                    choco="kubernetes-cli", brew="kubectl", snap="kubectl --classic"),
}
# Programs a failing command names, mapped to the tool that provides them.
PROGRAMS: dict[str, str] = {program: key for key, tool in TOOLS.items() for program in tool["programs"]}
PROGRAMS.update({"npm": "node", "npx": "node", "corepack": "node", "pip": "python", "pip3": "python", "bunx": "bun", "uvx": "uv",
                 "rustup": "rust", "java": "java", "g++": "cc", "c++": "cc", "clang++": "cc", "cl": "msvc", "cl.exe": "msvc",
                 "link.exe": "msvc", "gem": "ruby", "docker-compose": "docker", "R": "r", "kotlin": "kotlin", "mvnw": "maven"})
# Import names that differ from their PyPI package.
PYPI = {"cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml", "sklearn": "scikit-learn", "skimage": "scikit-image",
        "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "jwt": "PyJWT", "Crypto": "pycryptodome", "dateutil": "python-dateutil",
        "attr": "attrs", "magic": "python-magic", "serial": "pyserial", "usb": "pyusb", "OpenGL": "PyOpenGL", "docx": "python-docx",
        "pptx": "python-pptx", "fitz": "pymupdf", "win32api": "pywin32", "win32con": "pywin32", "win32com": "pywin32",
        "gi": "PyGObject", "telegram": "python-telegram-bot", "discord": "discord.py", "MySQLdb": "mysqlclient",
        "psycopg2": "psycopg2-binary", "google.protobuf": "protobuf", "jose": "python-jose", "multipart": "python-multipart",
        "Levenshtein": "python-Levenshtein", "socketio": "python-socketio", "engineio": "python-engineio", "zmq": "pyzmq",
        "sentencepiece": "sentencepiece", "torch": "torch", "tensorflow": "tensorflow", "wx": "wxPython", "kivy": "kivy"}
WINDOWS_MANAGERS, MAC_MANAGERS, LINUX_MANAGERS = ("winget", "scoop", "choco"), ("brew",), ("apt", "dnf", "pacman", "zypper", "snap", "brew")
MANAGER_PROGRAMS = {"apt": "apt-get"}
# Commands that install software, so Dikdik allows them time and looks for new tools afterwards.
INSTALLER = re.compile(r"\b(?:winget\s+install|scoop\s+install|choco\s+install|brew\s+install|apt(?:-get)?\s+install|dnf\s+install|"
                       r"pacman\s+-S|zypper\s+(?:--non-interactive\s+)?install|snap\s+install|npm\s+(?:install|i)\s+(?:-g|--global)|"
                       r"(?:pip|pip3|pipx|uv\s+tool|gem|cargo|go|dotnet\s+tool)\s+install|rustup\s+(?:default|toolchain|target)|"
                       r"xcode-select\s+--install|sh\.rustup\.rs|bun\.sh/install|deno\.land/install|astral\.sh/uv)", re.I)


def is_install(command: str) -> bool:
    return bool(INSTALLER.search(command or ""))


def platform_name() -> str:
    return "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"


# --- this computer ---------------------------------------------------------------------
_CACHE: dict = {}


def forget() -> None:
    """Drop what was learned about this computer (an install just ran)."""
    _CACHE.clear()


def _search_path() -> str:
    try:
        from .local_exec import child_env
        return child_env().get("PATH", os.environ.get("PATH", ""))
    except Exception:
        return os.environ.get("PATH", "")


def _which(program: str, path: str) -> str | None:
    return shutil.which(program, path=path)


def _version(command: list[str]) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", done.stdout or done.stderr or "").strip().splitlines()  # no colour codes
    return text[0][:120] if text else ""


def _msvc_installed() -> bool:
    if os.name != "nt":
        return False
    vswhere = os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe")
    if not os.path.isfile(vswhere):
        return False
    try:
        done = subprocess.run([vswhere, "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                               "-property", "installationPath"], capture_output=True, text=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return bool(done.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _elevation() -> str:
    """How system package managers can run here: "windows" (installers raise their own UAC prompt),
    "root", "sudo" (sudo works without a password) or "none" (it would ask for one)."""
    if os.name == "nt":
        return "windows"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "root"
    if not shutil.which("sudo"):
        return "none"
    try:
        return "sudo" if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0 else "none"
    except (OSError, subprocess.SubprocessError):
        return "none"


def probe(tools: Iterable[str] | None = None, *, versions: bool = True) -> dict:
    """This computer: OS, package managers and the tools present (with versions). Cached for a minute."""
    wanted = sorted(set(tools) if tools is not None else set(TOOLS))
    key = (tuple(wanted), versions)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    path, name = _search_path(), platform_name()
    order = WINDOWS_MANAGERS if name == "windows" else MAC_MANAGERS if name == "macos" else LINUX_MANAGERS
    managers = [m for m in order if _which(MANAGER_PROGRAMS.get(m, m), path)]
    installed: dict[str, dict] = {}

    def look(tool_key):
        tool = TOOLS[tool_key]
        if tool_key == "msvc":
            return tool_key, ({"program": "cl", "version": "Visual Studio C++ tools"} if _msvc_installed() else None)
        for program in tool["programs"]:
            found = _which(program, path)
            if found and "windowsapps" not in found.lower():  # the Store's python.exe stub is not Python
                version = _version([found, *tool.get("version", ("--version",))]) if versions else ""
                return tool_key, {"program": found, "version": version}
        if tool_key == "cc" and _msvc_installed():  # Visual Studio's cl compiles C and C++ (CMake and cargo find it)
            return tool_key, {"program": "cl", "version": "Visual Studio C++ tools (cl)"}
        return tool_key, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        for tool_key, found in pool.map(look, [k for k in wanted if k in TOOLS]):
            if found:
                installed[tool_key] = found
    result = {"os": name, "arch": _arch(), "managers": managers, "installed": installed, "elevation": _elevation()}
    _CACHE[key] = (time.monotonic(), result)
    return result


def _arch() -> str:
    import platform
    return platform.machine() or "unknown"


# --- what a project needs ---------------------------------------------------------------
def project_needs(paths: Iterable[str], read: Callable[[str], str | None] = lambda path: None) -> list[dict]:
    """The toolchains a project's files call for, each with the file that says so and any
    version it pins. ``paths`` are POSIX-style paths relative to the project root."""
    paths = [p for p in paths if "node_modules/" not in p and not p.startswith(".git/")]
    names = {PurePosixPath(p).name for p in paths}
    suffixes = {PurePosixPath(p).suffix.lower() for p in paths}
    top = {p for p in paths if "/" not in p}
    needs: dict[str, dict] = {}

    def need(tool, why, version=""):
        if tool not in needs:
            needs[tool] = {"tool": tool, "label": TOOLS[tool]["label"], "why": why, **({"version": version} if version else {})}

    def text(path):
        try:
            return read(path) or ""
        except Exception:
            return ""

    if "package.json" in names:
        package = text("package.json") if "package.json" in top else ""
        engines = re.search(r'"node"\s*:\s*"([^"]+)"', package)
        nvmrc = text(".nvmrc").strip() if ".nvmrc" in top else ""
        need("node", "package.json", nvmrc or (engines.group(1) if engines else ""))
        for lock, tool in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")):
            if lock in names:
                need(tool, lock)
    if names & {"deno.json", "deno.jsonc"}:
        need("deno", "deno.json")
    python_files = names & {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"} or ".py" in suffixes
    if python_files:
        pinned = text(".python-version").strip() if ".python-version" in top else ""
        need("python", next(iter(sorted(names & {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"})), "Python files"), pinned)
        if "uv.lock" in names:
            need("uv", "uv.lock")
        if "poetry.lock" in names:
            need("poetry", "poetry.lock")
        if "platformio.ini" in names:
            need("platformio", "platformio.ini")
    if "Cargo.toml" in names:
        toolchain = text("rust-toolchain.toml") or text("rust-toolchain")
        channel = re.search(r'channel\s*=\s*"([^"]+)"', toolchain)
        need("rust", "Cargo.toml", channel.group(1) if channel else toolchain.strip()[:20])
    if "go.mod" in names:
        go = re.search(r"(?m)^go\s+(\d+(?:\.\d+)*)", text("go.mod"))
        need("go", "go.mod", go.group(1) if go else "")
    if "pom.xml" in names:
        java = re.search(r"<(?:maven\.compiler\.release|java\.version|maven\.compiler\.source)>(\d+)<", text("pom.xml"))
        need("java", "pom.xml", java.group(1) if java else "")
        if "mvnw" not in names and "mvnw.cmd" not in names:
            need("maven", "pom.xml")
    if names & {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}:
        need("java", "build.gradle")
        if "gradlew" not in names and "gradlew.bat" not in names:
            need("gradle", "build.gradle")
    if ".kt" in suffixes and not names & {"build.gradle", "build.gradle.kts", "pom.xml"}:
        need("kotlin", "Kotlin files")
    if suffixes & {".csproj", ".fsproj", ".vbproj", ".sln"} or "global.json" in names:
        sdk = re.search(r'"version"\s*:\s*"([^"]+)"', text("global.json")) if "global.json" in top else None
        need("dotnet", next((p for p in sorted(paths) if PurePosixPath(p).suffix.lower() in {".sln", ".csproj", ".fsproj"}), "global.json"),
             sdk.group(1) if sdk else "")
    native = suffixes & {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
    if "CMakeLists.txt" in names:
        need("cmake", "CMakeLists.txt")
    if native or "CMakeLists.txt" in names:
        need("msvc" if platform_name() == "windows" and suffixes & {".vcxproj"} else "cc", "C/C++ sources")
    if "Makefile" in names or "makefile" in names:
        need("make", "Makefile")
    if "meson.build" in names or "build.ninja" in names:
        need("ninja", "meson.build" if "meson.build" in names else "build.ninja")
    if "composer.json" in names:
        need("php", "composer.json")
        need("composer", "composer.json")
    elif ".php" in suffixes:
        need("php", "PHP files")
    if "Gemfile" in names:
        need("ruby", "Gemfile", text(".ruby-version").strip() if ".ruby-version" in top else "")
        need("bundler", "Gemfile")
    if "pubspec.yaml" in names:
        flutter = "flutter:" in text("pubspec.yaml") or any(p.startswith(("android/", "ios/")) for p in paths)
        need("flutter" if flutter else "dart", "pubspec.yaml")
    if "Package.swift" in names or ".swift" in suffixes:
        need("swift", "Package.swift" if "Package.swift" in names else "Swift files")
    if "build.zig" in names:
        need("zig", "build.zig")
    if ".r" in suffixes or ".rmd" in suffixes or "renv.lock" in names:
        need("r", "renv.lock" if "renv.lock" in names else "R files")
    if suffixes & {".ino"} or "sketch.yaml" in names:
        need("arduino", ".ino sketch")
    if ".proto" in suffixes:
        need("protoc", ".proto files")
    if ".tf" in suffixes:
        need("terraform", ".tf files")
    if names & {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"}:
        need("docker", next(iter(sorted(names & {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"}))))
    for key in list(needs):
        for required in TOOLS[key].get("needs", ()):
            need(required, f"needed by {TOOLS[key]['label']}")
        if platform_name() == "windows":
            for required in TOOLS[key].get("windows_needs", ()):
                need(required, f"{TOOLS[key]['label']} links with it on Windows")
    return list(needs.values())


def dependency_commands(paths: Iterable[str], *, os_name: str | None = None) -> list[dict]:
    """How to fetch a project's own dependencies (after its toolchain is installed)."""
    paths = [p for p in paths if "node_modules/" not in p]
    top = {p for p in paths if "/" not in p}
    windows = (os_name or platform_name()) == "windows"
    venv_python = r".venv\Scripts\python" if windows else ".venv/bin/python"
    steps = []
    if "package.json" in top:
        manager = "bun" if top & {"bun.lock", "bun.lockb"} else "pnpm" if "pnpm-lock.yaml" in top else "yarn" if "yarn.lock" in top else "npm"
        command = {"npm": "npm ci" if "package-lock.json" in top else "npm install", "pnpm": "pnpm install", "yarn": "yarn install",
                   "bun": "bun install"}[manager]
        steps.append({"command": command, "why": f"JavaScript dependencies ({manager})", "skip_if_exists": "node_modules"})
    if "uv.lock" in top:
        steps.append({"command": "uv sync", "why": "Python dependencies (uv)"})
    elif "poetry.lock" in top:
        steps.append({"command": "poetry install", "why": "Python dependencies (Poetry)"})
    elif top & {"requirements.txt", "pyproject.toml", "setup.py"}:
        target = "-r requirements.txt" if "requirements.txt" in top else "-e ."
        steps.append({"command": "python -m venv .venv", "why": "an isolated Python environment for this project", "skip_if_exists": ".venv"})
        steps.append({"command": f"{venv_python} -m pip install {target}", "why": "Python dependencies, inside .venv"})
    for marker, command, why in (("Cargo.toml", "cargo fetch", "Rust crates"), ("go.mod", "go mod download", "Go modules"),
                                 ("composer.json", "composer install --no-interaction", "PHP packages"),
                                 ("Gemfile", "bundle install", "Ruby gems"), ("Package.swift", "swift package resolve", "Swift packages")):
        if marker in top:
            steps.append({"command": command, "why": why})
    if "pubspec.yaml" in top:
        steps.append({"command": "flutter pub get" if any(p.startswith(("android/", "ios/", "lib/")) for p in paths) else "dart pub get",
                      "why": "Dart packages"})
    if any(PurePosixPath(p).suffix.lower() in {".sln", ".csproj", ".fsproj"} for p in top):
        steps.append({"command": "dotnet restore", "why": ".NET packages"})
    if "pom.xml" in top:
        steps.append({"command": ("mvnw.cmd" if windows else "./mvnw") + " -q dependency:resolve" if "mvnw" in top or "mvnw.cmd" in top
                      else "mvn -q dependency:resolve", "why": "Maven dependencies"})
    if top & {"build.gradle", "build.gradle.kts"} and top & {"gradlew", "gradlew.bat"}:
        steps.append({"command": ("gradlew.bat" if windows else "./gradlew") + " dependencies -q", "why": "Gradle dependencies"})
    return steps


# --- how to install ----------------------------------------------------------------------
def _manager_command(manager: str, package: str, tool: dict, elevation: str) -> str | None:
    sudo = "" if elevation == "root" else "sudo -n "
    if manager == "winget":
        override = f' --override "{tool["winget_override"]}"' if tool.get("winget_override") else ""
        return (f"winget install --id {package} --exact --silent --accept-package-agreements --accept-source-agreements "
                f"--disable-interactivity{override}")
    if manager == "scoop":
        bucket = package.split("/")[0] if "/" in package else ""
        return (f"scoop bucket add {bucket}; " if bucket else "") + f"scoop install {package}"
    if manager == "choco":
        return f"choco install {package} -y --no-progress"
    if manager == "brew":
        return f"brew install {package}"
    if manager == "apt":
        return f"{sudo}apt-get update && {sudo}apt-get install -y {package}"
    if manager == "dnf":
        return f"{sudo}dnf install -y {package}"
    if manager == "pacman":
        return f"{sudo}pacman -S --needed --noconfirm {package}"
    if manager == "zypper":
        return f"{sudo}zypper --non-interactive install {package}"
    if manager == "snap":
        return f"{sudo}snap install {package}"
    return None


def install_plan(tool_key: str, machine: dict) -> dict:
    """The commands that install a tool on this computer, ordered best first, and how to verify it.
    ``machine`` is ``probe()``'s result."""
    tool = TOOLS.get(tool_key)
    if tool is None:
        return {"tool": tool_key, "known": False,
                "next_step": f"{tool_key} is not in Dikdik's catalog: find its official install instructions for {machine.get('os')} "
                             "(web_search or web_fetch when available), install it non-interactively, then verify it runs."}
    name, managers = machine.get("os", platform_name()), machine.get("managers", [])
    if tool.get("windows_only") and name != "windows":
        return {"tool": tool_key, "label": tool["label"], "known": True, "applies": False, "next_step": f"{tool['label']} is only needed on Windows."}
    elevation = machine.get("elevation", "none")
    elevated = elevation in {"windows", "root", "sudo"}
    options: list[dict] = []
    prerequisites = [need for need in tool.get("needs", ()) if need not in machine.get("installed", {})]
    for language, field, template in (("npm", "npm", "npm install -g {}"), ("pip", "pip", "python -m pip install --user {}"),
                                      ("gem", "gem", "gem install {}")):
        if tool.get(field):
            options.append({"via": language, "command": template.format(tool[field])})
    for manager in managers:
        if name == "macos" and manager == "brew" and tool.get("brew_command"):
            options.append({"via": "xcode", "command": tool["brew_command"], "interactive": True,
                            "note": "macOS shows a dialog for the Command Line Tools; the user must click Install."})
            continue
        package = tool.get(manager)
        if package:
            command = _manager_command(manager, package, tool, elevation)
            needs_admin = manager in {"apt", "dnf", "pacman", "zypper", "snap"} and not elevated
            options.append({"via": manager, "command": command, **({"needs_admin": True} if needs_admin else {})})
    script = tool.get("script_posix") if name != "windows" else None
    if script:
        options.append({"via": "official installer", "command": script})
    for command in tool.get("post", ()):
        for option in options:
            if option["via"] not in {"npm", "pip", "gem"}:
                option["then"] = list(option.get("then", [])) + [command]
    # The language's own manager first only when that language is already here.
    language_ready = {"npm": "node" in machine.get("installed", {}), "pip": "python" in machine.get("installed", {}),
                      "gem": "ruby" in machine.get("installed", {})}
    options.sort(key=lambda o: (o.get("needs_admin", False), o.get("interactive", False),
                                0 if o["via"] in language_ready and language_ready[o["via"]] else 1 if o["via"] not in language_ready else 2))
    program = tool["programs"][0] if tool["programs"] else ("cl" if tool_key == "msvc" else tool_key)
    # The Build Tools have no program on PATH; environment_check finds them through vswhere in any shell.
    verify = (f"{program} " + " ".join(tool.get("version", ("--version",)))) if tool["programs"] else "environment_check (lists msvc once installed)"
    plan = {"tool": tool_key, "label": tool["label"], "known": True, "options": options[:4], "verify": verify,
            **({"install_first": prerequisites} if prerequisites else {}),
            **({"note": tool["note"]} if tool.get("note") else {}), **({"docs": tool["docs"]} if tool.get("docs") else {})}
    if not options:
        plan["next_step"] = (f"No package manager here offers {tool['label']}. Follow its official instructions"
                             + (f" ({tool['docs']})" if tool.get("docs") else "") + f" for {name}, installing non-interactively.")
    elif all(o.get("needs_admin") for o in options):
        plan["next_step"] = ("Installing this needs administrator rights, which you cannot enter. Ask the user to run: "
                             + options[0]["command"].replace("sudo -n ", "sudo ") + " — then verify with: " + verify)
    return plan


# --- reading a failure -------------------------------------------------------------------
_PROGRAM = r"([A-Za-z0-9_][\w.+-]{0,60})"
DIAGNOSES = [
    (re.compile(r"linker `link\.exe` not found|Microsoft Visual C\+\+ 14\.0 or greater is required|"
                r"error MSB8020|could not find (?:any instance of )?Visual Studio|vcvarsall\.bat", re.I), "tool", "msvc"),
    (re.compile(r"No Java runtime present|JAVA_HOME is not set|JAVA_HOME environment variable is not defined", re.I), "tool", "java"),
    (re.compile(r"xcrun: error: invalid active developer path", re.I), "tool", "cc"),
    (re.compile(r"error: externally-managed-environment", re.I), "externally_managed", ""),
    (re.compile(r"No module named pip\b", re.I), "no_pip", ""),
    (re.compile(r"'" + _PROGRAM + r"' is not recognized as an internal or external command"), "program", None),
    (re.compile(r"The term '" + _PROGRAM + r"' is not recognized as (?:the )?(?:a )?name of a cmdlet"), "program", None),
    (re.compile(r"(?:zsh: )?command not found: " + _PROGRAM), "program", None),
    (re.compile(r"(?m)(?:^|[\s:])" + _PROGRAM + r": command not found"), "program", None),
    (re.compile(r"(?m)^(?:/bin/sh|sh|bash|/usr/bin/env)(?:: line \d+)?: (?:\d+: )?" + _PROGRAM + r": not found"), "program", None),
    (re.compile(r"env: '?" + _PROGRAM + r"'?: No such file or directory"), "program", None),
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'|ImportError: No module named '?([\w.]+)"), "python_module", None),
    (re.compile(r"Cannot find (?:module|package) '([^']+)'"), "node_module", None),
]


def diagnose(output: str) -> dict | None:
    """What a failed command's output says is missing, or None when it is an ordinary failure."""
    text = (output or "")[-20000:]
    for pattern, kind, fixed in DIAGNOSES:
        match = pattern.search(text)
        if not match:
            continue
        name = fixed if fixed is not None else next((g for g in match.groups() if g), "")
        if kind == "program":
            program = name.removesuffix(".exe") if name.lower().endswith(".exe") and name != "cl.exe" and name != "link.exe" else name
            return {"kind": "program", "name": program, **({"tool": PROGRAMS[program]} if program in PROGRAMS else {})}
        if kind == "tool":
            return {"kind": "program", "name": TOOLS[name]["programs"][0] if TOOLS[name]["programs"] else name, "tool": name}
        if kind == "python_module":
            return {"kind": "python_module", "name": name}
        if kind == "node_module":
            if name.startswith((".", "/")) or re.match(r"^[A-Za-z]:[\\/]", name):
                return None  # a path in the project: a code problem, not an install
            package = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
            return {"kind": "node_module", "name": package}
        return {"kind": kind, "name": name}
    return None


def remedy(output: str, files: dict | Iterable[str], machine: dict | None = None) -> dict | None:
    """For a failed command: what is missing and the exact commands that fix it on this computer."""
    found = diagnose(output)
    if not found:
        return None
    paths = list(files)
    top = {p for p in paths if "/" not in p}
    read = (lambda path: files.get(path)) if isinstance(files, dict) else (lambda path: None)
    if machine is None:  # look only at what this failure concerns: fast, even on a full machine
        tool = found.get("tool")
        machine = probe({"python", "node", "ruby", *([tool, *TOOLS[tool].get("needs", ())] if tool in TOOLS else [])}, versions=False)
    windows = machine.get("os") == "windows"
    python = r".venv\Scripts\python" if windows and ".venv" in {p.split("/")[0] for p in paths} else "python"
    kind, name = found["kind"], found["name"]
    if kind == "program":
        tool = found.get("tool")
        if tool == "msvc" and "msvc" in machine.get("installed", {}):
            return {**found, "next_step": "The Microsoft C++ Build Tools are installed but not active for this build. For Rust use the MSVC "
                                          "toolchain (`rustup default stable-msvc`); for CMake let it find Visual Studio "
                                          "(`cmake -G \"Visual Studio 17 2022\" ...`); for Python packages upgrade pip, setuptools and wheel. Then retry."}
        if tool and tool in machine.get("installed", {}) and tool != "msvc":
            return {**found, "installed_at": machine["installed"][tool]["program"],
                    "next_step": f"{TOOLS[tool]['label']} is installed ({machine['installed'][tool]['program']}); Dikdik now finds it on PATH. "
                                 "Run the command again."}
        if tool:
            plan = install_plan(tool, machine)
            first = plan.get("options", [{}])[0].get("command") if plan.get("options") else None
            return {**found, "missing": TOOLS[tool]["label"], "plan": plan,
                    "next_step": plan.get("next_step") or (
                        (f"Install {', '.join(TOOLS[t]['label'] for t in plan['install_first'])} first. " if plan.get("install_first") else "")
                        + f"{TOOLS[tool]['label']} is not installed. Install it with run_command: {first} — then verify with "
                        f"`{plan['verify']}` and run the failing command again.")}
        package = read("package.json") or ""
        if "package.json" in top and (f'"{name}"' in package or not package):
            install = next(step["command"] for step in dependency_commands(paths, os_name=machine.get("os")) if "JavaScript" in step["why"])
            return {**found, "missing": f"{name} from the project's JavaScript dependencies",
                    "next_step": f"`{name}` comes from node_modules. Install the project's dependencies with `{install}`, then run the command again "
                                 f"(or call it through the package manager, e.g. `npx {name}`)."}
        return {**found, "next_step": f"`{name}` is not installed and is not in Dikdik's catalog. Find out which package provides it on "
                                      f"{machine.get('os')} (the project's docs, web_search), install it non-interactively, verify it runs, then retry."}
    if kind == "python_module":
        root_module = name.split(".")[0]
        package = PYPI.get(name, PYPI.get(root_module, root_module.replace("_", "-")))
        requirements = (read("requirements.txt") or "").lower()
        if "requirements.txt" in top and (package.lower() in requirements or not requirements):
            command = f"{python} -m pip install -r requirements.txt"
        else:
            command = f"{python} -m pip install {package}"
        return {**found, "package": package, "next_step": f"The Python module `{name}` is missing. Install it with `{command}`"
                + ("" if "requirements.txt" not in top or package.lower() in requirements else f" and add {package} to requirements.txt")
                + ", then run the command again."}
    if kind == "node_module":
        package_json = read("package.json") or ""
        listed = f'"{name}"' in package_json
        step = dependency_commands(paths, os_name=machine.get("os"))
        install = next((s["command"] for s in step if "JavaScript" in s["why"]), "npm install")
        add = {"npm install": f"npm install {name}", "npm ci": f"npm install {name}", "pnpm install": f"pnpm add {name}",
               "yarn install": f"yarn add {name}", "bun install": f"bun add {name}"}.get(install, f"npm install {name}")
        return {**found, "next_step": (f"`{name}` is listed in package.json but not installed: run `{install}`" if listed
                                       else f"`{name}` is not a dependency yet: add it with `{add}`") + ", then run the command again."}
    if kind == "externally_managed":
        venv = r".venv\Scripts\python" if windows else ".venv/bin/python"
        return {**found, "next_step": "This Python refuses global installs (PEP 668). Create a project environment with "
                                      f"`python -m venv .venv` and install with `{venv} -m pip install ...`."}
    if kind == "no_pip":
        return {**found, "next_step": "This Python has no pip. Run `python -m ensurepip --upgrade`, then retry."}
    return found


def report(paths: list[str], read: Callable[[str], str | None], tool: str | None = None) -> dict:
    """environment_check: this computer, what the project needs, what is missing and how to install it."""
    needs = project_needs(paths, read)
    wanted = {need["tool"] for need in needs} | ({tool} if tool in TOOLS else set()) | {"git", "python", "node"}
    machine = probe(wanted)
    result = {"computer": {key: machine[key] for key in ("os", "arch", "managers", "elevation")},
              "installed": {key: value.get("version") or value.get("program") for key, value in machine["installed"].items()}}
    missing = [need for need in needs if need["tool"] not in machine["installed"]]
    result["project_needs"] = [{**need, "status": "installed" if need["tool"] in machine["installed"] else "missing"} for need in needs]
    if missing:
        result["install"] = [install_plan(need["tool"], machine) for need in missing]
    if tool:
        result["plan"] = install_plan(tool, machine)
    steps = dependency_commands(paths, os_name=machine["os"])
    if steps:
        result["dependencies"] = steps
    if not machine["managers"]:
        result["note"] = ("No package manager was found. " + ("winget ships with Windows 10/11 (App Installer from the Microsoft Store)."
                          if machine["os"] == "windows" else "Install Homebrew (https://brew.sh)." if machine["os"] == "macos" else
                          "Use the distribution's package manager."))
    result["next_step"] = ("Install what is missing (prerequisites first), verify each with its verify command, then install the "
                           "project's dependencies and build." if missing else
                           "Everything the project needs is installed. Install the project's dependencies if they are not yet, then build.")
    return result
