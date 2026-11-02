"""Commands work on a Windows computer with nothing extra installed (no Git Bash): POSIX-style
agent commands are translated for PowerShell, exit codes survive, and Python output is UTF-8."""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import local_exec, windows_shell
from dikdik.agent import ModelResponse, run_agent


def script(command, **kwargs):
    """Only the translated body, without the shared prelude."""
    return windows_shell.powershell_command(command, **kwargs)[len(windows_shell.PRELUDE):]


class TranslationTests(unittest.TestCase):
    def test_chains_become_status_checks_in_windows_powershell(self):
        body = script("cd src && npm test || echo failed", legacy=True)
        self.assertNotIn("&&", body)
        self.assertNotIn("||", body)
        self.assertIn("cd src \n$__dk_ok = $?\nif ($__dk_ok) {\n npm test \n$__dk_ok = $?\n}", body)
        self.assertIn("if (-not $__dk_ok) {\n echo failed\n$__dk_ok = $?\n}", body)
        self.assertTrue(body.endswith("exit $LASTEXITCODE } else { exit 1 } }\nexit 0"))

    def test_powershell_7_keeps_its_own_chains(self):
        self.assertTrue(script("a && b", legacy=False).startswith("a && b\n"))

    def test_quoted_text_is_left_alone(self):
        body = script("echo 'a && b $TOKEN' && echo \"x || y\"", legacy=True, env_names=("TOKEN",))
        self.assertIn("'a && b $TOKEN'", body)
        self.assertIn('"x || y"', body)
        self.assertEqual(body.count("if ($__dk_ok)"), 1)

    def test_escaped_quotes_and_operators_stay_literal(self):
        body = script(r'echo "say \"hi && bye\"" && echo next', legacy=True)
        self.assertEqual(body.count("if ($__dk_ok)"), 1)  # only the real chain
        self.assertIn(r'echo "say \"hi && bye\"" ', body)
        self.assertEqual(script(r"echo \"&& echo b", legacy=True),
                         "echo \\\"\n$__dk_ok = $?\nif ($__dk_ok) {\n echo b\n$__dk_ok = $?\n}"
                         "\nif (-not $__dk_ok) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }\nexit 0")
        self.assertEqual(windows_shell._unquoted('echo a\\"'), [('echo a\\"', "")])  # an escape at the very end
        self.assertEqual(windows_shell._unquoted("echo a\\"), [("echo a\\", "")])
        self.assertEqual(windows_shell._unquoted("say 'it\\'s"), [("say ", ""), ("'", "mark"), ("it\\", "'"), ("'", "mark"), ("s", "")])  # no escapes inside single quotes

    def test_variable_names_that_share_a_prefix(self):
        body = script("echo $GH $GH_TOKEN", legacy=True, env_names=("GH", "GH_TOKEN"))
        self.assertTrue(body.startswith("echo $env:GH $env:GH_TOKEN\n"), body[:60])

    def test_approved_variables_and_dev_null(self):
        body = script('curl -H "Authorization: $GH_TOKEN" ${GH_TOKEN} $GH_TOKEN_X 2>/dev/null >/dev/null &>/dev/null',
                      legacy=True, env_names=("GH_TOKEN",))
        self.assertIn('"Authorization: $env:GH_TOKEN"', body)
        self.assertIn(" $env:GH_TOKEN $GH_TOKEN_X", body)  # only exact approved names
        self.assertIn("2>$null >$null *>$null", body)

    def test_the_model_is_told_which_shell_it_has(self):
        with patch.object(local_exec, "shell_kind", return_value="powershell"):
            self.assertIn("Windows PowerShell 5.1", local_exec.describe_shell())
            self.assertIn("$env:NAME", local_exec.describe_shell())
        with patch.object(local_exec, "shell_kind", return_value="bash"):
            self.assertTrue(local_exec.describe_shell().startswith("commands run in bash"))
        seen = []

        class Model:
            def respond(self, messages, tools):
                seen.append(messages[0]["content"])
                return ModelResponse("Done.", [])

        run_agent({"a.py": "x = 1\n"}, "Look", Model(), None, lambda *a: None, threading.Event(),
                  shell=lambda *a: {}, shell_note="commands run in PowerShell 7")
        self.assertIn("shell=True (commands run in PowerShell 7)", seen[0])


@unittest.skipUnless(os.name == "nt", "Windows only")
class ChildEnvironmentTests(unittest.TestCase):
    def test_python_output_is_utf8_and_a_python_is_always_reachable(self):
        with patch.dict(os.environ, {"PATH": r"C:\nowhere"}, clear=False):
            os.environ.pop("PYTHONIOENCODING", None)
            env = local_exec.child_env({"SECRET": "v"})
        self.assertEqual((env["PYTHONIOENCODING"], env["SECRET"]), ("utf-8", "v"))
        self.assertTrue(env["PATH"].startswith(str(Path(sys.executable).parent)))
        with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            self.assertEqual(local_exec.child_env()["PYTHONIOENCODING"], "cp1252")  # the user's choice wins


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell only")
class WindowsPowerShellTests(unittest.TestCase):
    """Real commands, with Git Bash hidden and Git's tools taken off PATH."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.root / "build").mkdir()
        path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if "git" not in p.lower())
        for active in (patch.object(local_exec, "_git_bash", return_value=None),
                       patch.object(local_exec, "shell_kind", return_value="powershell"),
                       patch.dict(os.environ, {"PATH": path})):
            active.start()
            self.addCleanup(active.stop)

    def run_command(self, command, **kwargs):
        return local_exec.run(self.root, command, threading.Event(), timeout=60, **kwargs)

    def test_posix_style_commands_work(self):
        cases = [
            ("mkdir -p src/app && cd src && python -c \"import os; print(os.getcwd().endswith('src'))\"", "passed", "True"),
            ("python -c \"import sys; sys.exit(1)\" || echo fallback", "passed", "fallback"),
            ("rm -rf build && rm -rf missing && echo removed", "passed", "removed"),
            ("touch new.txt && ls -la && head -n 1 notes.txt && tail -n 1 notes.txt", "passed", "new.txt"),
            ("export MODE=fast && python -c \"import os; print(os.environ['MODE'])\" 2>/dev/null", "passed", "fast"),
            ("python -c \"print('é ✓ 日本')\"", "passed", "é ✓ 日本"),
        ]
        for command, status, expected in cases:
            with self.subTest(command=command):
                result = self.run_command(command)
                self.assertEqual(result["status"], status, result["output"])
                self.assertIn(expected, result["output"])
        self.assertFalse((self.root / "build").exists())
        self.assertEqual(self.run_command("head -n 1 notes.txt")["output"].strip(), "one")

    def test_exit_codes_and_secrets_survive(self):
        failed = self.run_command("python -c \"import sys; sys.exit(3)\" && echo never")
        self.assertEqual((failed["status"], failed["exit_code"]), ("failed", 3))
        self.assertNotIn("never", failed["output"])
        secret = self.run_command("echo \"token=$DEMO_TOKEN\"", env={"DEMO_TOKEN": "tok-12345"})
        self.assertIn("token=[redacted DEMO_TOKEN]", secret["output"])
        self.assertNotIn("tok-12345", secret["output"])

    def test_project_scripts_run_despite_the_default_script_policy(self):
        (self.root / "tool.ps1").write_text("Write-Output 'script ran'\n", encoding="utf-8")
        with patch.dict(os.environ, {"PSExecutionPolicyPreference": "Restricted"}):  # a fresh Windows install
            result = self.run_command("./tool.ps1")  # how npm.ps1 and npx.ps1 are found
        self.assertEqual(result["status"], "passed", result["output"])
        self.assertIn("script ran", result["output"])


if __name__ == "__main__":
    unittest.main()
