"""Local mode: the agent edits the real folder and runs commands on this computer, with undo."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL

ENV = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}
LOCAL = {"mode": "agent", "verification": "local", "approve_external": True, "approve_execution": True}
PY = Path(sys.executable).as_posix()


class Script:
    def __init__(self, replies, hook=None):
        self.replies, self.hook, self.seen = list(replies), hook, 0

    def respond(self, messages, tools):
        self.seen += 1
        if self.hook:
            self.hook(self.seen, messages)
        return self.replies.pop(0)


def call(i, name, **args):
    return ModelResponse("", [ToolCall(f"c{i}", name, args)])


FIX = {"path": "calc.py", "before": "a - b", "after": "a + b"}
TEST = "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"


class LocalModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        (self.root / "test_calc.py").write_text(TEST, encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def run_task(self, script, **extra):
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            task = self.engine.submit({"objective": "Fix add", **LOCAL, **extra})
            return task, self.idle(task)

    def idle(self, task, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.engine.store.get(task)
            if state["state"] in TERMINAL and not self.engine.busy(task):
                return state
            time.sleep(0.02)
        self.fail("task did not finish")

    def calc(self):
        return (self.root / "calc.py").read_text(encoding="utf-8")

    def test_edits_land_in_the_folder_and_undo_puts_them_back(self):
        task, state = self.run_task(Script([call(1, "edit_file", **FIX), call(2, "run_tests"), call(3, "finish")]))
        self.assertEqual(state["state"], "completed", state["result"])
        self.assertIn("a + b", self.calc())
        result = state["result"]
        self.assertTrue(result["direct"])
        self.assertTrue(result["tests_verified"])  # the real unittest ran on this computer and passed
        self.assertEqual(result["checks"][-1]["runs_on"], "this computer")
        self.assertEqual(result["changed_files"], ["calc.py"])
        with self.assertRaisesRegex(ValueError, "already in your files"):
            self.engine.apply(task)
        self.assertEqual(self.engine.undo(task), {"restored": ["calc.py"], "conflicts": []})
        self.assertIn("a - b", self.calc())
        with self.assertRaisesRegex(ValueError, "already undone"):
            self.engine.undo(task)

    def test_redo_reapplies_an_undone_turn_and_the_cycle_repeats(self):
        task, state = self.run_task(Script([call(1, "edit_file", **FIX), call(2, "run_tests"), call(3, "finish")]))
        self.assertEqual(state["state"], "completed", state["result"])
        self.assertEqual(self.engine.undo(task), {"restored": ["calc.py"], "conflicts": []})
        self.assertIn("a - b", self.calc())
        self.assertEqual(self.engine.redo(task), {"restored": ["calc.py"], "conflicts": []})
        self.assertIn("a + b", self.calc())
        with self.assertRaisesRegex(ValueError, "not been undone"):
            self.engine.redo(task)
        # The cycle repeats as long as nobody touched the file.
        self.engine.undo(task)
        self.assertIn("a - b", self.calc())
        self.assertEqual(self.engine.redo(task), {"restored": ["calc.py"], "conflicts": []})
        self.assertIn("a + b", self.calc())

    def test_redo_conflicts_when_the_user_changed_the_file_after_undo(self):
        task, state = self.run_task(Script([call(1, "edit_file", **FIX), call(2, "run_tests"), call(3, "finish")]))
        self.engine.undo(task)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a * b  # mine\n", encoding="utf-8")
        self.assertEqual(self.engine.redo(task), {"restored": [], "conflicts": ["calc.py"]})
        self.assertIn("a * b", self.calc())  # the user's own edit stays

    def test_resume_can_finish_previously_verified_local_edits_without_changing_them_again(self):
        profile = self.engine.agents.create({'name': 'Two calls', 'max_model_calls': 2})
        first = Script([call(1, 'edit_file', **FIX), call(2, 'run_tests')])
        second = Script([call(3, 'finish')])
        with patch('dikdik.engine.CompatibleAgentModel', side_effect=[first, second]):
            task = self.engine.submit({'objective': 'Fix add', **LOCAL, 'agent_profile': profile['id']})
            paused = self.idle(task)
            self.assertEqual(paused['state'], 'paused', paused['result'])
            self.assertIn('a + b', self.calc())
            self.assertNotIn('error', paused['result'])
            self.engine.resume(task, LOCAL)
            done = self.idle(task)
        self.assertEqual(done['state'], 'completed', done['result'])
        self.assertTrue(done['result']['tests_verified'])
        self.assertIn('a + b', self.calc())
        self.assertEqual(len(second.replies), 0)

    def test_commands_run_in_the_project_folder(self):
        command = f'"{PY}" -c "open(\'made.py\', \'w\').write(\'made = True\\n\'); print(\'ran here\')"'
        task, state = self.run_task(Script([call(1, "run_command", command=command), ModelResponse("Done.", [])]))
        self.assertEqual((self.root / "made.py").read_text(encoding="utf-8"), "made = True\n")
        terminal = [e["data"] for e in state["events"] if e["kind"] == "terminal_output"]
        self.assertIn("ran here", "".join(t["output"] for t in terminal))
        self.assertEqual(terminal[-1]["exit_code"], 0)
        self.engine.undo(task)
        self.assertFalse((self.root / "made.py").exists())  # a file the turn created goes away again

    def test_agent_does_not_launch_a_whole_computer_filename_search(self):
        with patch('dikdik.local_exec.run') as execute:
            _, state = self.run_task(Script([call(1, 'run_command', command='find / -name "WebsiteLinks.tsx" 2>/dev/null'),
                                             ModelResponse('I should look in the selected project instead.', [])]))
        execute.assert_not_called()
        errors = [e['data']['observation'] for e in state['events'] if e['kind'] == 'agent_tool' and e['data'].get('phase') == 'end']
        self.assertTrue(any('list_files' in item.get('error', '') for item in errors))

    def test_a_file_changed_by_someone_else_is_not_overwritten(self):
        def meanwhile(n, messages):
            if n == 2:  # after the agent's first edit was written, the user edits the same file
                (self.root / "calc.py").write_text("def add(a, b):\n    return b + a  # mine\n", encoding="utf-8")
        script = Script([call(1, "edit_file", **FIX), call(2, "run_command", command="echo hi"), ModelResponse("Done.", [])], meanwhile)
        with patch.dict(os.environ, {}):
            task, state = self.run_task(script, confirmation="never")
        self.assertIn("# mine", self.calc())
        self.assertIn("changed outside the agent", state["result"]["error"])

    def test_undo_leaves_files_you_changed_afterwards(self):
        task, _ = self.run_task(Script([call(1, "edit_file", **FIX), ModelResponse("Done.", [])]))
        (self.root / "calc.py").write_text("# rewritten by me\n", encoding="utf-8")
        self.assertEqual(self.engine.undo(task), {"restored": [], "conflicts": ["calc.py"]})
        self.assertEqual(self.calc(), "# rewritten by me\n")

    def test_ask_mode_and_always_allow(self):
        decided = threading.Event()

        def approve():
            for _ in range(200):
                waiting = self.engine.confirmations.waiting(task_box[0]) if task_box else []
                if waiting:
                    self.engine.confirm(task_box[0], {"id": waiting[0], "accept": True, "remember": True})
                    decided.set()
                    return
                time.sleep(0.05)

        task_box = []
        script = Script([call(1, "run_command", command="echo first"), call(2, "run_command", command="echo first"), ModelResponse("Done.", [])])
        threading.Thread(target=approve, daemon=True).start()
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            task_box.append(self.engine.submit({"objective": "Echo", **LOCAL, "confirmation": "ask"}))
            state = self.idle(task_box[0])
        self.assertTrue(decided.is_set())
        asked = [e for e in state["events"] if e["kind"] == "confirmation_requested"]
        self.assertEqual(len(asked), 1)  # the second identical command was remembered
        self.assertEqual(self.engine.allowed_commands(), ["echo first"])
        self.assertEqual(self.engine.forget_command("echo first"), [])

    def test_local_needs_its_approval_and_agent_mode(self):
        for request in ({**LOCAL, "approve_execution": False}, {**LOCAL, "mode": "model"}):
            with self.assertRaises(ValueError):
                self.engine.submit({"objective": "x", **request})

    def test_large_svg_model_edit_undo_and_full_diff_are_not_limited_by_initial_cache(self):
        import zipfile
        import io
        (self.root / 'calc.py').write_bytes(b'def add(a, b):\n    return a + b\n')
        path = 'client/public/Cover.svg'
        target = self.root / path
        target.parent.mkdir(parents=True)
        original = '<svg>' + 'x' * 600000 + 'fill="red"</svg>'
        target.write_bytes(original.encode())
        script = Script([
            call(1, 'search_text', query='fill="red"', path='client/public'),
            call(2, 'read_file', path=path, start_column=600006, max_chars=10),
            call(3, 'edit_file', path=path, before='fill="red"', after='fill="blue"'),
            call(4, 'run_tests'), call(5, 'finish'),
        ])
        task, state = self.run_task(script)
        self.assertEqual(state['state'], 'completed', state['result'])
        self.assertEqual(state['result']['changed_files'], [path])
        self.assertEqual(target.read_bytes().decode(), original.replace('red', 'blue'))
        self.assertTrue(state['result']['diff_truncated'])
        self.assertLess(len(state['result']['diff']), 1000000)
        patch = self.engine.full_diff(task)
        self.assertGreater(len(patch), 1000000)
        self.assertIn(b'fill="red"', patch)
        self.assertIn(b'fill="blue"', patch)
        with zipfile.ZipFile(io.BytesIO(self.engine.archive(task))) as bundle:
            self.assertEqual(bundle.read('changes.patch'), patch)
            self.assertEqual(bundle.read('candidate/' + path).decode(), target.read_bytes().decode())
        self.assertEqual(self.engine.undo(task), {'restored': [path], 'conflicts': []})
        self.assertEqual(target.read_bytes().decode(), original)

    def test_large_file_external_change_is_not_overwritten(self):
        path = 'Cover.svg'
        original = '<svg>' + 'x' * 300000 + 'fill="red"</svg>'
        (self.root / path).write_bytes(original.encode())
        def outside_change(step, messages):
            if step == 2:
                (self.root / path).write_bytes((original + 'outside').encode())
        script = Script([call(1, 'read_file', path=path, start_column=300006),
                         call(2, 'edit_file', path=path, before='red', after='blue'),
                         ModelResponse('File changed outside; please review.', [])], hook=outside_change)
        task, state = self.run_task(script)
        self.assertFalse(state['result']['source_modified'])
        self.assertEqual((self.root / path).read_bytes().decode(), original + 'outside')

    def test_large_file_edits_resume_with_their_baseline_and_keep_undo(self):
        (self.root / 'calc.py').write_bytes(b'def add(a, b):\n    return a + b\n')
        path = 'Cover.svg'
        original = '<svg>' + 'x' * 300000 + 'fill="red"</svg>'
        (self.root / path).write_bytes(original.encode())
        def pause(step, messages):
            if step == 2:
                self.engine.pause(next(iter(self.engine.active)))
        task, state = self.run_task(Script([call(1, 'edit_file', path=path, before='fill="red"', after='fill="blue"'),
                                            ModelResponse('Pausing here.', [])], hook=pause))
        self.assertEqual(state['state'], 'paused', state['result'])
        self.assertEqual((self.root / path).read_bytes().decode(), original.replace('red', 'blue'))
        continuation = Script([call(10, 'read_file', path=path, start_column=300006),
                               call(11, 'edit_file', path=path, before='fill="blue"', after='fill="green"'),
                               call(12, 'run_tests'), call(13, 'finish')])
        with patch('dikdik.engine.CompatibleAgentModel', return_value=continuation):
            self.engine.resume(task, LOCAL)
            state = self.idle(task)
        self.assertEqual(state['state'], 'completed', state['result'])
        self.assertEqual((self.root / path).read_bytes().decode(), original.replace('red', 'green'))
        self.assertEqual(self.engine.undo(task), {'restored': [path], 'conflicts': []})
        self.assertEqual((self.root / path).read_bytes().decode(), original.replace('red', 'blue'))

    # --- background shells -------------------------------------------------------
    def test_background_shell_starts_polls_and_stops(self):
        started = self.engine.start_background("echo first && sleep 0.3 && echo second")
        self.assertEqual(started["status"], "running")
        self.assertTrue(started["id"])
        time.sleep(0.15)
        polled = self.engine.poll_background(started["id"])
        self.assertEqual(polled["status"], "running")
        self.assertIn("first", polled["output"])
        time.sleep(0.3)
        # Give the drain thread a moment to read the final output after exit
        time.sleep(0.1)
        polled = self.engine.poll_background(started["id"])
        self.assertIn("second", polled["output"])
        self.assertEqual(polled["status"], "exited")

    def test_background_shell_stop_terminates_a_running_command(self):
        started = self.engine.start_background("sleep 30")
        self.assertEqual(started["status"], "running")
        stopped = self.engine.stop_background(started["id"])
        self.assertEqual(stopped["status"], "stopped")

    def test_background_shell_list_shows_all_shells(self):
        a = self.engine.start_background("sleep 0.5")
        b = self.engine.start_background("sleep 0.5")
        listing = {s["id"] for s in self.engine.list_background()}
        self.assertEqual({a["id"], b["id"]}, listing)
        self.engine.stop_background(a["id"])
        self.engine.stop_background(b["id"])

    def test_background_shell_poll_missing_id(self):
        with self.assertRaises(KeyError):
            self.engine.poll_background("nonexistent")


if __name__ == "__main__":
    unittest.main()
