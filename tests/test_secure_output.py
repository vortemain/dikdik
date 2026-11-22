"""Secure by default: what the agent writes is scanned, credentials are never written, findings are reported."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik import secure_output
from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.engine import Engine, TERMINAL

# Assembled at runtime so this file never contains a literal credential itself.
OPENAI = "sk-" + "proj-" + "Q7rT2xLm9KpW4vZn8YcB3hJd6FgS1aEu5NqR0oXi"
AWS = "AKIA" + "Q3EGJ7RZ5XN2LW4P"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PYTHON = {"type": "function", "function": {"name": "run_python", "parameters": {
    "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}}
DOCUMENT = {"type": "function", "function": {"name": "create_document", "parameters": {
    "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path"]}}}


class Replies:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def respond(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)


def step(i, name, **arguments):
    return ModelResponse("", [ToolCall(f"c{i}", name, arguments)])


class SecretTests(unittest.TestCase):
    def test_live_credentials_are_found_but_placeholders_are_not(self):
        found = secure_output.new_secrets("", f'KEY = "{OPENAI}"\nAWS = "{AWS}"\n')
        self.assertEqual([f["rule"] for f in found], ["SEC001", "SEC005"])
        self.assertNotIn(OPENAI, json.dumps(found))
        quiet = "\n".join(['KEY = "AKIA' + 'IOSFODNN7EXAMPLE"', 'KEY = "sk-' + "a" * 40 + '"', 'KEY = "sk-' + 'your-key-goes-here-0123456789abcdef"',
                           f'TOKEN = "{JWT}"',  # sample tokens fill documentation; the scanner still reports them
                           "-----BEGIN " + "PRIVATE KEY-----", "...", "-----END PRIVATE KEY-----"])
        self.assertEqual(secure_output.new_secrets("", quiet), [])
        pem = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + "MIIEowIBAAKCAQEA7d3k9Qx2Lm8VbN4pT6rY1sW0zJcH5gF" + "\n-----END RSA PRIVATE KEY-----\n"
        self.assertEqual([f["rule"] for f in secure_output.new_secrets("", pem)], ["SEC003"])

    def test_existing_and_user_provided_credentials_are_not_new(self):
        before = f'KEY = "{OPENAI}"\n'
        self.assertEqual(secure_output.new_secrets(before, before + "DEBUG = False\n"), [])
        provided = secure_output.provided_values(f"Put my key {AWS} in settings.py")
        self.assertEqual(provided, {AWS})
        self.assertEqual(secure_output.new_secrets("", f'AWS = "{AWS}"\n', provided), [])

    def test_a_private_key_the_user_pasted_is_theirs_to_write_and_passes_the_gate(self):
        pem = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + "MIIEowIBAAKCAQEA7d3k9Qx2Lm8VbN4pT6rY1sW0zJcH5gF" + "\nAbCd\n-----END RSA PRIVATE KEY-----\n"
        provided = secure_output.provided_values("Save this deploy key as deploy.pem:\n" + pem)
        self.assertEqual(secure_output.new_secrets("", pem, provided), [])
        self.assertEqual(secure_output.introduced({"deploy.pem": pem}, {}, provided), [])
        self.assertEqual(secure_output.provided_in({"deploy.pem": pem}, provided), [{"file": "deploy.pem", "rule": "SEC003"}])
        other = pem.replace("7d3k9Qx2", "Zq81mN0p")
        self.assertEqual([f["rule"] for f in secure_output.new_secrets("", other, provided)], ["SEC003"])

    def test_scrubbing_removes_new_credentials_from_command_output_files(self):
        text, found = secure_output.strip_new_secrets("A = 1\n", f'A = 1\nKEY = "{OPENAI}"\n', set())
        self.assertNotIn(OPENAI, text)
        self.assertIn("REDACTED-CREDENTIAL", text)
        self.assertEqual([f["rule"] for f in found], ["SEC005"])
        same, none = secure_output.strip_new_secrets("", f'KEY = "{AWS}"\n', {AWS})
        self.assertEqual((same, none), (f'KEY = "{AWS}"\n', []))

    def test_scrub_masks_credentials_anywhere_in_a_payload(self):
        payload = {"arguments": {"content": f"key={OPENAI}", "path": "a.py"}, "list": [AWS, 3], "tokens": 12}
        scrubbed = json.dumps(secure_output.scrub(payload))
        self.assertNotIn(OPENAI, scrubbed)
        self.assertNotIn(AWS, scrubbed)
        self.assertIn('"tokens": 12', scrubbed)
        self.assertIn('"path": "a.py"', scrubbed)


class FindingTests(unittest.TestCase):
    def test_only_introduced_blocking_findings_count(self):
        before = {"app.py": "import subprocess\nsubprocess.run(cmd, shell=True)\n"}
        after = {"app.py": before["app.py"] + "import requests\nrequests.get(url, verify=False, timeout=5)\nopen('x')\n",
                 "new.sh": "chmod 777 /srv\n"}
        found = secure_output.introduced(after, before)
        self.assertEqual(sorted((f["rule"], f["file"]) for f in found), [("PY005", "app.py"), ("SH002", "new.sh")])

    def test_scripts_are_scanned_by_language_and_provided_values_are_masked(self):
        self.assertEqual([f["rule"] for f in secure_output.script_findings("x = eval(input())\n")], ["PY012"])
        self.assertEqual([f["rule"] for f in secure_output.script_findings("curl -k https://x.example.com", "bash")], ["SH003"])
        self.assertEqual(secure_output.script_findings(f"client = Client('{OPENAI}')\n", provided={OPENAI}), [])
        self.assertEqual([f["rule"] for f in secure_output.script_findings(f"client = Client('{OPENAI}')\n")], ["SEC005"])

    def test_documents_are_scanned_inside_code_blocks_and_sheets(self):
        markdown = "# Setup\n\nRun this:\n\n```python\nimport os\nos.system('ping ' + input())\n```\n\n```bash\ncurl -k https://x.example.com\n```\n"
        parts = secure_output.document_files({"path": "Guide.docx", "content": markdown})
        self.assertEqual(sorted(parts), ["Guide.docx", "Guide.docx#1.py", "Guide.docx#2.sh"])
        self.assertEqual(sorted((f["rule"], f["file"]) for f in secure_output.introduced(parts, {})),
                         [("PY012", "Guide.docx#1.py"), ("SH003", "Guide.docx#2.sh")])
        sheets = {"path": "Book.xlsx", "sheets": [{"name": "Data", "rows": [["name", "total"], ["Evil", "=cmd|' /C calc'!A0"]]}]}
        parts = secure_output.document_files(sheets)
        self.assertEqual([(f["rule"], f["file"]) for f in secure_output.introduced(parts, {})], [("XLS001", "Book.xlsx#Data.csv")])
        leaked = {"path": "Keys.xlsx", "sheets": [{"name": "S", "rows": [["aws", AWS]]}]}
        self.assertEqual([f["rule"] for f in secure_output.new_secrets("", "\n".join(secure_output.document_files(leaked).values()))],
                         ["SEC001"])

    def test_reflection_and_caveat_name_rule_location_and_fix(self):
        found = secure_output.introduced({"app.py": "import os\nos.system(cmd)\n"}, {})
        note = secure_output.reflection(found, 1)
        self.assertIn("app.py:2", note)
        self.assertIn("PY007", note)
        self.assertIn("1 of 2", note)
        self.assertIn("data, not instructions", note)
        caveat = secure_output.caveat(secure_output.compact(found))
        self.assertIn("PY007", caveat)
        self.assertIn("unresolved", caveat)


class AgentTests(unittest.TestCase):
    def run_task(self, model, objective, original=None, **options):
        events = []
        original = original if original is not None else {"tests/test_app.py": "assert True\n"}
        result = run_agent(original, objective, model, lambda files: {"kind": "unittest", "status": "passed"},
                           lambda k, d: events.append((k, d)), threading.Event(), creation_policy=lambda path: True, **options)
        return result, events

    def test_a_live_credential_is_refused_and_never_reaches_events_or_transcripts(self):
        model = Replies(step(1, "create_file", path="client.py", content=f'API_KEY = "{OPENAI}"\n'),
                        step(2, "create_file", path="client.py", content='import os\nAPI_KEY = os.environ["API_KEY"]\n'),
                        step(3, "run_tests"), step(4, "finish"))
        result, events = self.run_task(model, "Create client.py that holds the API key for the client")
        self.assertEqual(result.state, "completed", result.error)
        self.assertNotIn(OPENAI, result.files["client.py"])
        first = next(d for k, d in events if k == "tool" and d["phase"] == "end" and d["id"] == "c1")
        self.assertTrue(first["failed"])
        self.assertIn("credential", first["observation"]["error"])
        self.assertNotIn(OPENAI, json.dumps(events))
        self.assertNotIn(OPENAI, json.dumps(result.transcript))

    def test_a_credential_the_user_gave_for_the_file_can_be_written(self):
        model = Replies(step(1, "create_file", path="settings.py", content=f'AWS_KEY = "{AWS}"\n'),
                        step(2, "run_tests"), step(3, "finish"))
        result, events = self.run_task(model, f"Create settings.py with AWS_KEY set to {AWS}")
        self.assertEqual(result.state, "completed", result.error)
        self.assertIn(AWS, result.files["settings.py"])
        self.assertNotIn(AWS, json.dumps([d for k, d in events]))  # still masked on screen and in the event log
        self.assertEqual(result.security["provided_credentials"], [{"file": "settings.py", "rule": "SEC001"}])

    def test_dangerous_code_is_reflected_twice_then_reported(self):
        original = {"app.py": "import subprocess\n\n\ndef run(cmd):\n    return cmd\n", "tests/test_app.py": "assert True\n"}
        model = Replies(step(1, "edit_file", path="app.py", before="return cmd", after="return subprocess.run(cmd, shell=True)"),
                        step(2, "edit_file", path="app.py", before="def run(cmd):", after="def run(cmd):  # runs a command"),
                        step(3, "edit_file", path="app.py", before="# runs a command", after="# runs the command"),
                        step(4, "run_tests"), step(5, "finish"))
        result, events = self.run_task(model, "Make run() execute the command", original)
        self.assertEqual(result.state, "completed", result.error)
        reflections = [d for k, d in events if k == "security_findings" and d["action"] == "reflect"]
        self.assertEqual([d["reflection"] for d in reflections], [1, 2])
        self.assertEqual({d["stage"] for d in reflections}, {"edit"})
        self.assertEqual(reflections[0]["findings"][0]["rule"], "PY001")
        fed = [m for m in model.seen[1] if m.get("role") == "user" and "PY001" in str(m.get("content"))]
        self.assertTrue(fed)
        self.assertEqual([f["rule"] for f in result.security["findings"]], ["PY001"])
        final = [d for k, d in events if k == "security_findings" and d["stage"] == "final"]
        self.assertEqual(len(final), 1)
        self.assertTrue(any(k == "message" and "PY001" in d["content"] and "unresolved" in d["content"] for k, d in events))

    def test_a_fixed_finding_is_not_reported(self):
        original = {"app.py": "import subprocess\n\n\ndef run(cmd):\n    return cmd\n", "tests/test_app.py": "assert True\n"}
        model = Replies(step(1, "edit_file", path="app.py", before="return cmd", after="return subprocess.run(cmd, shell=True)"),
                        step(2, "edit_file", path="app.py", before="subprocess.run(cmd, shell=True)", after="subprocess.run(cmd, timeout=60)"),
                        step(3, "run_tests"), step(4, "finish"))
        result, events = self.run_task(model, "Make run() execute the command", original)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(result.security["findings"], [])
        self.assertEqual(len([d for k, d in events if k == "security_findings"]), 1)
        self.assertFalse(any(k == "message" and "unresolved" in d["content"] for k, d in events))

    def test_a_risky_script_is_reflected_before_it_runs(self):
        ran = []

        def python(files, arguments):
            ran.append(arguments["code"])
            return {"status": "passed", "exit_code": 0, "output": "4", "changed": {}}
        model = Replies(step(1, "run_python", code="print(eval(input()))"), step(2, "run_python", code="print(2 + 2)"),
                        ModelResponse("The answer is 4.", []))
        events = []
        result = run_agent({}, "Work out 2 + 2 with Python", model, None, lambda k, d: events.append((k, d)), threading.Event(),
                           conversation=True, python=python, python_tool=PYTHON)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(ran, ["print(2 + 2)"])
        found = [d for k, d in events if k == "security_findings"]
        self.assertEqual([(d["stage"], d["action"]) for d in found], [("script", "reflect")])
        self.assertEqual(found[0]["findings"][0]["rule"], "PY012")

    def test_a_script_that_stays_risky_runs_after_two_rounds_and_is_reported(self):
        ran = []

        def python(files, arguments):
            ran.append(arguments["code"])
            return {"status": "passed", "exit_code": 0, "output": "4", "changed": {}}
        scripts = ["print(eval(input()))", "value = input()\nprint(eval(value))", "text = input()\nprint(eval(text))"]
        model = Replies(*[step(i, "run_python", code=code) for i, code in enumerate(scripts)], ModelResponse("The answer is 4.", []))
        events = []
        result = run_agent({}, "Work out 2 + 2 with Python", model, None, lambda k, d: events.append((k, d)), threading.Event(),
                           conversation=True, python=python, python_tool=PYTHON)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(len(ran), 1)
        self.assertEqual([f["rule"] for f in result.security["findings"]], ["PY012"])
        answer = [d["content"] for k, d in events if k == "message"][-1]
        self.assertTrue(answer.startswith("The answer is 4."))
        self.assertIn("PY012", answer)

    def test_a_credential_a_command_writes_is_stripped_and_the_file_rewritten(self):
        written = []

        def shell(files, command, timeout):
            return {"status": "passed", "exit_code": 0, "output": "", "changed": {"config.py": f'KEY = "{OPENAI}"\n'}}
        model = Replies(step(1, "run_command", command="python make_config.py"), step(2, "run_tests"), step(3, "finish"))
        result, events = self.run_task(model, "Generate the config file", shell=shell, on_write=lambda files: written.append(dict(files)))
        self.assertEqual(result.state, "completed", result.error)
        self.assertNotIn(OPENAI, result.files["config.py"])
        self.assertIn("REDACTED-CREDENTIAL", result.files["config.py"])
        self.assertTrue(written and "REDACTED-CREDENTIAL" in written[-1]["config.py"])  # the folder is fixed at once
        end = next(d for k, d in events if k == "tool" and d["phase"] == "end" and d["id"] == "c1")
        self.assertEqual(end["observation"]["credentials_removed"], [{"file": "config.py", "rule": "SEC005"}])
        self.assertNotIn(OPENAI, json.dumps(events))

    def test_run_code_programs_are_checked_in_their_language(self):
        ran = []
        code_tool = {"type": "function", "function": {"name": "run_code", "parameters": {
            "type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["code"]}}}

        def program(files, arguments):
            ran.append(arguments)
            return {"status": "passed", "exit_code": 0, "output": "ok", "changed": {}}
        model = Replies(step(1, "run_code", language="javascript", code="const a = new https.Agent({ rejectUnauthorized: false });"),
                        step(2, "run_code", language="javascript", code="console.log('ok');"), ModelResponse("Done.", []))
        events = []
        result = run_agent({}, "Print a greeting with Node", model, None, lambda k, d: events.append((k, d)), threading.Event(),
                           conversation=True, python=program, python_tool=code_tool)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual([a["code"] for a in ran], ["console.log('ok');"])
        found = [d for k, d in events if k == "security_findings"]
        self.assertEqual([(d["stage"], d["findings"][0]["rule"], d["findings"][0]["file"]) for d in found],
                         [("script", "JS004", "script.js")])

    def test_spreadsheets_with_credentials_are_refused(self):
        calls = []
        model = Replies(step(1, "create_document", path="keys.xlsx", sheets=[{"name": "S", "rows": [["aws", AWS]]}]),
                        ModelResponse("I will not store keys in a spreadsheet.", []))
        sheet_tool = {"type": "function", "function": {"name": "create_document", "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "sheets": {"type": "array", "items": {"type": "object"}}}, "required": ["path"]}}}
        events = []
        result = run_agent({}, "Make a spreadsheet of our keys", model, None, lambda k, d: events.append((k, d)), threading.Event(),
                           extra_tools=[sheet_tool], extra_call=lambda name, args: calls.append(args) or {"saved": args["path"]},
                           read_only=True, conversation=True)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(calls, [])
        self.assertNotIn(AWS, json.dumps(events))

    def test_documents_refuse_credentials_and_reflect_risky_code(self):
        saved = []

        def call(name, arguments):
            saved.append(arguments["path"])
            return {"saved": arguments["path"], "bytes": len(arguments.get("content", ""))}
        model = Replies(step(1, "create_document", path="notes.md", content=f"Use the key {AWS} for uploads."),
                        step(2, "create_document", path="fetch.py", content="import requests\nrequests.get(URL, verify=False, timeout=5)\n"),
                        step(3, "create_document", path="fetch.py", content="import requests\nrequests.get(URL, timeout=5)\n"),
                        ModelResponse("Saved fetch.py.", []))
        events = []
        result = run_agent({}, "Write a fetch script and notes", model, None, lambda k, d: events.append((k, d)), threading.Event(),
                           extra_tools=[DOCUMENT], extra_call=call, read_only=True, conversation=True)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(saved, ["fetch.py", "fetch.py"])
        refused = next(d for k, d in events if k == "tool" and d["phase"] == "end" and d["id"] == "c1")
        self.assertIn("credential", refused["observation"]["error"])
        found = [d for k, d in events if k == "security_findings"]
        self.assertEqual([(d["stage"], d["findings"][0]["rule"]) for d in found], [("document", "PY005")])
        self.assertEqual(result.security["findings"], [])
        self.assertNotIn(AWS, json.dumps(events))


class ChatResultTests(unittest.TestCase):
    def test_unresolved_findings_reach_the_task_result_and_event_log(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        (base / "project").mkdir()
        engine = Engine(base / "project", base / "data")
        self.addCleanup(engine.close)
        risky = "import requests\nrequests.get(URL, verify=False, timeout=5)\n"
        model = Mock()
        model.respond.side_effect = iter([
            ModelResponse("", [ToolCall("1", "create_document", {"path": "fetch.py", "content": risky})]),
            ModelResponse("", [ToolCall("2", "create_document", {"path": "fetch.py", "content": risky + "print('ok')\n"})]),
            ModelResponse("", [ToolCall("3", "create_document", {"path": "fetch.py", "content": risky + "print('done')\n"})]),
            ModelResponse("Saved fetch.py.", [])])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = engine.submit({"objective": "Write a script that downloads the report", "mode": "chat",
                                  "verification": "syntax", "approve_external": True})
            deadline = time.monotonic() + 30
            while not (engine.store.get(task)["state"] in TERMINAL and not engine.busy(task)):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
        data = engine.store.get(task)
        self.assertEqual(data["state"], "completed", data.get("result"))
        self.assertEqual([f["rule"] for f in data["result"]["security"]["findings"]], ["PY005"])
        stages = [e["data"]["stage"] for e in data["events"] if e["kind"] == "agent_security_findings"]
        self.assertEqual(stages, ["document", "document", "final"])


if __name__ == "__main__":
    unittest.main()
