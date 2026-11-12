"""Setting a computer up for a project: diagnosis, project needs, install plans, PATH refresh."""
import json
import os
import threading
import unittest
from unittest.mock import patch

from dikdik import local_exec, toolchain
from dikdik.agent import ModelResponse, ToolCall, run_agent

WINDOWS = {"os": "windows", "arch": "AMD64", "managers": ["winget", "choco"], "elevation": "windows",
           "installed": {"node": {"program": r"C:\nodejs\node.exe", "version": "v22"}, "python": {"program": r"C:\py\python.exe", "version": "3.13"}}}
LINUX_USER = {"os": "linux", "arch": "x86_64", "managers": ["apt"], "elevation": "none", "installed": {}}
LINUX_ROOT = {**LINUX_USER, "elevation": "root"}
MAC = {"os": "macos", "arch": "arm64", "managers": ["brew"], "elevation": "none", "installed": {}}


class DiagnosisTests(unittest.TestCase):
    def test_every_shell_says_not_found_differently(self):
        cases = {
            "'cmake' is not recognized as an internal or external command,\noperable program or batch file.": ("program", "cmake", "cmake"),
            "go : The term 'go' is not recognized as the name of a cmdlet, function, script file": ("program", "go", "go"),
            "bash: line 1: cargo: command not found": ("program", "cargo", "rust"),
            "/bin/sh: 1: javac: not found": ("program", "javac", "java"),
            "zsh: command not found: flutter": ("program", "flutter", "flutter"),
            "env: 'node': No such file or directory": ("program", "node", "node"),
            "error: linker `link.exe` not found\n  = note: program not found": ("program", "link.exe", "msvc"),
            "Microsoft Visual C++ 14.0 or greater is required. Get it with Build Tools": ("program", "link.exe", "msvc"),
            "The operation couldn't be completed. Unable to locate a Java Runtime. No Java runtime present, requesting install.": ("program", "javac", "java"),
        }
        for output, (kind, name, tool) in cases.items():
            with self.subTest(output=output[:40]):
                found = toolchain.diagnose(output)
                self.assertEqual((found["kind"], found.get("tool")), (kind, tool), found)
                if tool != "msvc":
                    self.assertEqual(found["name"], name)

    def test_modules_and_ordinary_failures(self):
        self.assertEqual(toolchain.diagnose("ModuleNotFoundError: No module named 'cv2'"), {"kind": "python_module", "name": "cv2"})
        self.assertEqual(toolchain.diagnose("Error: Cannot find module '@vitejs/plugin-react/dist'")["name"], "@vitejs/plugin-react")
        self.assertEqual(toolchain.diagnose("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod' imported from x")["name"], "zod")
        self.assertIsNone(toolchain.diagnose("Error: Cannot find module './util'"))  # a path: a code problem, not an install
        self.assertIsNone(toolchain.diagnose("AssertionError: 2 != 3\nFAILED tests/test_app.py"))
        self.assertEqual(toolchain.diagnose("error: externally-managed-environment")["kind"], "externally_managed")
        self.assertEqual(toolchain.diagnose("/usr/bin/python3: No module named pip")["kind"], "no_pip")

    def test_installers_are_recognised(self):
        for command in ["winget install --id Git.Git -e", "brew install go", "sudo -n apt-get install -y cmake", "npm install -g pnpm",
                        "python -m pip install --user poetry", "rustup default stable", "scoop install zig",
                        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"]:
            self.assertTrue(toolchain.is_install(command), command)
        for command in ["npm install", "pip --version", "go build ./...", "cargo build"]:
            self.assertFalse(toolchain.is_install(command), command)


class ProjectTests(unittest.TestCase):
    def needs(self, files):
        return {need["tool"]: need for need in toolchain.project_needs(files, files.get)}

    def test_each_ecosystem_names_its_toolchain_and_pins(self):
        cases = [
            ({"package.json": '{"engines": {"node": ">=20"}}', "pnpm-lock.yaml": ""}, {"node", "pnpm"}),
            ({"pyproject.toml": "", "uv.lock": "", ".python-version": "3.12\n"}, {"python", "uv"}),
            ({"go.mod": "module x\n\ngo 1.23\n"}, {"go"}),
            ({"pom.xml": "<java.version>21</java.version>"}, {"java", "maven"}),
            ({"pom.xml": "", "mvnw": ""}, {"java"}),
            ({"build.gradle.kts": "", "gradlew": ""}, {"java"}),
            ({"App.csproj": "", "global.json": '{"sdk": {"version": "8.0.100"}}'}, {"dotnet"}),
            ({"composer.json": ""}, {"php", "composer"}),
            ({"Gemfile": ""}, {"ruby", "bundler"}),
            ({"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n", "lib/main.dart": ""}, {"flutter"}),
            ({"pubspec.yaml": "name: cli\n", "bin/cli.dart": ""}, {"dart"}),
            ({"Package.swift": ""}, {"swift"}),
            ({"build.zig": ""}, {"zig"}),
            ({"sketch/sketch.ino": ""}, {"arduino"}),
            ({"api.proto": ""}, {"protoc"}),
            ({"main.tf": ""}, {"terraform"}),
            ({"Dockerfile": ""}, {"docker"}),
            ({"analysis.R": ""}, {"r"}),
        ]
        for files, expected in cases:
            with self.subTest(files=sorted(files)):
                self.assertEqual(set(self.needs(files)), expected)
        self.assertEqual(self.needs({"package.json": '{"engines": {"node": ">=20"}}'})["node"]["version"], ">=20")
        self.assertEqual(self.needs({"go.mod": "go 1.23\n"})["go"]["version"], "1.23")
        self.assertEqual(self.needs({"pom.xml": "<java.version>21</java.version>"})["java"]["version"], "21")
        self.assertEqual(self.needs({"App.csproj": "", "global.json": '{"sdk": {"version": "8.0.100"}}'})["dotnet"]["version"], "8.0.100")

    def test_native_code_needs_a_compiler_and_rust_needs_msvc_on_windows(self):
        with patch.object(toolchain, "platform_name", return_value="windows"):
            self.assertEqual(set(self.needs({"Cargo.toml": ""})), {"rust", "msvc"})
            self.assertEqual(set(self.needs({"CMakeLists.txt": "", "src/main.cpp": ""})), {"cmake", "cc"})
        with patch.object(toolchain, "platform_name", return_value="linux"):
            self.assertEqual(set(self.needs({"Cargo.toml": ""})), {"rust"})
            self.assertEqual(set(self.needs({"Makefile": "", "main.c": ""})), {"make", "cc"})

    def test_node_modules_are_never_read_as_project_files(self):
        self.assertEqual(set(self.needs({"package.json": "", "node_modules/x/Cargo.toml": "", "node_modules/y/setup.py": ""})), {"node"})

    def test_dependency_commands_follow_the_lockfile_and_isolate_python(self):
        commands = lambda files, os_name: [step["command"] for step in toolchain.dependency_commands(files, os_name=os_name)]
        self.assertEqual(commands({"package.json": "", "package-lock.json": ""}, "linux"), ["npm ci"])
        self.assertEqual(commands({"package.json": "", "yarn.lock": ""}, "linux"), ["yarn install"])
        self.assertEqual(commands({"requirements.txt": ""}, "windows"), ["python -m venv .venv", r".venv\Scripts\python -m pip install -r requirements.txt"])
        self.assertEqual(commands({"pyproject.toml": ""}, "linux"), ["python -m venv .venv", ".venv/bin/python -m pip install -e ."])
        self.assertEqual(commands({"uv.lock": "", "pyproject.toml": ""}, "linux"), ["uv sync"])
        self.assertEqual(commands({"Cargo.toml": "", "go.mod": ""}, "linux"), ["cargo fetch", "go mod download"])
        self.assertEqual(commands({"pom.xml": "", "mvnw.cmd": "", "mvnw": ""}, "windows"), ["mvnw.cmd -q dependency:resolve"])


class PlanTests(unittest.TestCase):
    def test_windows_uses_winget_first_with_non_interactive_flags(self):
        plan = toolchain.install_plan("go", WINDOWS)
        self.assertEqual([o["via"] for o in plan["options"]], ["winget", "choco"])
        self.assertIn("winget install --id GoLang.Go --exact --silent --accept-package-agreements", plan["options"][0]["command"])
        self.assertEqual(plan["verify"], "go version")
        msvc = toolchain.install_plan("msvc", WINDOWS)
        self.assertIn("Microsoft.VisualStudio.Workload.VCTools", msvc["options"][0]["command"])

    def test_language_managers_come_first_once_the_language_is_here(self):
        self.assertEqual(toolchain.install_plan("pnpm", WINDOWS)["options"][0]["command"], "npm install -g pnpm")
        plan = toolchain.install_plan("pnpm", {**WINDOWS, "installed": {}})
        self.assertEqual(plan["options"][0]["via"], "winget")
        self.assertEqual(plan["install_first"], ["node"])

    def test_linux_without_a_password_free_sudo_asks_the_user_with_the_exact_command(self):
        plan = toolchain.install_plan("cmake", LINUX_USER)
        self.assertTrue(plan["options"][0]["needs_admin"])
        self.assertIn("Ask the user to run: sudo apt-get update && sudo apt-get install -y cmake", plan["next_step"])
        root = toolchain.install_plan("cmake", LINUX_ROOT)
        self.assertEqual(root["options"][0]["command"], "apt-get update && apt-get install -y cmake")
        self.assertNotIn("next_step", root)

    def test_official_installers_and_follow_up_commands(self):
        rust = toolchain.install_plan("rust", LINUX_USER)
        script = next(o for o in rust["options"] if o["via"] == "official installer")
        self.assertIn("sh.rustup.rs", script["command"])
        self.assertEqual(script["then"], ["rustup default stable"])
        self.assertEqual(rust["verify"], "cargo --version")
        compiler = toolchain.install_plan("cc", MAC)
        self.assertEqual(compiler["options"][0]["command"], "xcode-select --install")
        self.assertTrue(compiler["options"][0]["interactive"])

    def test_unknown_tools_are_researched_and_windows_only_tools_skipped(self):
        self.assertIn("official install instructions", toolchain.install_plan("haskell-stack", WINDOWS)["next_step"])
        self.assertFalse(toolchain.install_plan("msvc", LINUX_ROOT)["applies"])


class RemedyTests(unittest.TestCase):
    def test_missing_toolchain_gets_install_and_verify_commands(self):
        fix = toolchain.remedy("bash: zig: command not found", {"build.zig": ""}, WINDOWS)
        self.assertEqual(fix["missing"], "Zig")
        self.assertIn("winget install --id zig.zig", fix["next_step"])
        self.assertIn("`zig --version`", fix["next_step"])

    def test_an_installed_tool_is_just_retried(self):
        fix = toolchain.remedy("'node' is not recognized as an internal or external command", {}, WINDOWS)
        self.assertIn("Run the command again", fix["next_step"])
        self.assertEqual(fix["installed_at"], r"C:\nodejs\node.exe")

    def test_project_binaries_come_from_the_project_dependencies(self):
        files = {"package.json": '{"devDependencies": {"vite": "5"}}', "pnpm-lock.yaml": ""}
        fix = toolchain.remedy("'vite' is not recognized as an internal or external command", files, WINDOWS)
        self.assertIn("`pnpm install`", fix["next_step"])

    def test_python_and_node_modules(self):
        fix = toolchain.remedy("ModuleNotFoundError: No module named 'PIL'", {"app.py": "", "requirements.txt": "flask\n"}, WINDOWS)
        self.assertIn("python -m pip install pillow", fix["next_step"])
        self.assertIn("add pillow to requirements.txt", fix["next_step"])
        listed = toolchain.remedy("ModuleNotFoundError: No module named 'flask'", {"requirements.txt": "flask\n", ".venv/pyvenv.cfg": ""}, WINDOWS)
        self.assertIn(r".venv\Scripts\python -m pip install -r requirements.txt", listed["next_step"])
        add = toolchain.remedy("Error: Cannot find module 'zod'", {"package.json": '{"dependencies": {}}', "yarn.lock": ""}, WINDOWS)
        self.assertIn("`yarn add zod`", add["next_step"])
        install = toolchain.remedy("Error: Cannot find module 'zod'", {"package.json": '{"dependencies": {"zod": "3"}}'}, WINDOWS)
        self.assertIn("listed in package.json but not installed: run `npm install`", install["next_step"])

    def test_build_tools_present_but_inactive(self):
        machine = {**WINDOWS, "installed": {**WINDOWS["installed"], "msvc": {"program": "cl", "version": ""}}}
        fix = toolchain.remedy("error: linker `link.exe` not found", {"Cargo.toml": ""}, machine)
        self.assertIn("rustup default stable-msvc", fix["next_step"])


class PathTests(unittest.TestCase):
    def setUp(self):
        local_exec._PATH_CACHE.clear()
        self.addCleanup(local_exec._PATH_CACHE.clear)

    def test_tools_installed_after_start_are_found_without_reordering_path(self):
        new = os.path.join("C:" if os.name == "nt" else "/opt", "NewTool", "bin")
        with patch.object(local_exec, "installed_paths", return_value=[new, "/usr/bin" if os.name != "nt" else r"C:\Windows"]):
            current = os.pathsep.join(["/usr/bin" if os.name != "nt" else r"C:\Windows", "/home/me/bin"])
            merged = local_exec.with_installed_paths(current)
        self.assertEqual(merged.split(os.pathsep), current.split(os.pathsep) + [new])  # appended once, nothing duplicated

    def test_child_env_always_carries_the_refreshed_path(self):
        with patch.object(local_exec, "installed_paths", return_value=[os.path.join(os.sep, "fresh", "tool")]):
            env = local_exec.child_env({"EXTRA": "1"})
        self.assertTrue(env["PATH"].endswith(os.path.join(os.sep, "fresh", "tool")))
        self.assertEqual(env["EXTRA"], "1")

    def test_installed_paths_are_cached_briefly_and_forgotten_after_installs(self):
        with patch.object(local_exec.Path, "is_dir", return_value=False):
            first = local_exec.installed_paths()
            self.assertIs(local_exec.installed_paths(), first)
            local_exec.forget_installed()
            self.assertIsNot(local_exec.installed_paths(), first)


class AgentIntegrationTests(unittest.TestCase):
    def test_a_failed_command_comes_back_with_the_fix_for_this_computer(self):
        class Script:
            def __init__(self):
                self.seen = []
                self.steps = iter([ModelResponse("", [ToolCall("1", "run_command", {"command": "zig build"})]),
                                   ModelResponse("", [ToolCall("2", "finish", {"summary": "stopped"})])])

            def respond(self, messages, tools):
                self.seen.append(messages)
                return next(self.steps)

        def shell(files, command, timeout, *rest):
            return {"status": "failed", "exit_code": 127, "output": "bash: line 1: zig: command not found", "changed": {}}

        model = Script()
        with patch.object(toolchain, "probe", return_value=WINDOWS):
            run_agent({"build.zig": "const std = @import(\"std\");\n"}, "Build it", model, lambda files: {"kind": "syntax", "status": "passed"},
                      lambda kind, data: None, threading.Event(), shell=shell)
        result = json.loads(model.seen[1][-1]["content"])
        self.assertEqual(result["diagnosis"]["missing"], "Zig")
        self.assertIn("winget install --id zig.zig", result["next_step"])


if __name__ == "__main__":
    unittest.main()
