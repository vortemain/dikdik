import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Oracle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TODO_FILE": str(self.dir / "tasks.json")}

    def todo(self, *args, env=None, cwd=None):
        done = subprocess.run([sys.executable, str(HERE / "todo.py"), *args], cwd=cwd or self.dir, env=env or self.env,
                              capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout.decode("utf-8").replace("\r\n", "\n"), done.stderr.decode("utf-8", "replace")

    def lines(self, *args):
        code, out, _ = self.todo(*args)
        self.assertEqual(code, 0)
        return [line for line in out.strip().split("\n") if line]

    def test_empty_list(self):
        self.assertEqual(self.lines("list"), ["No tasks"])

    def test_add_list_done_remove(self):
        self.assertEqual(self.lines("add", "Buy milk"), ["Added 1: Buy milk"])
        self.assertEqual(self.lines("add", "Call Ana"), ["Added 2: Call Ana"])
        self.assertEqual(self.lines("list"), ["[ ] 1 Buy milk", "[ ] 2 Call Ana"])
        self.assertEqual(self.todo("done", "1")[0], 0)
        self.assertEqual(self.lines("list"), ["[x] 1 Buy milk", "[ ] 2 Call Ana"])
        self.assertEqual(self.lines("list", "--open"), ["[ ] 2 Call Ana"])
        self.assertEqual(self.todo("remove", "2")[0], 0)
        self.assertEqual(self.lines("list"), ["[x] 1 Buy milk"])
        self.assertIn(self.lines("list", "--open"), ([], ["No tasks"]))  # the spec allows either

    def test_ids_are_never_reused(self):
        self.lines("add", "one")
        self.lines("add", "two")
        self.todo("remove", "2")
        self.assertEqual(self.lines("add", "three"), ["Added 3: three"])

    def test_unicode_text(self):
        self.assertEqual(self.lines("add", "Café ✓ 日本"), ["Added 1: Café ✓ 日本"])
        self.assertEqual(self.lines("list"), ["[ ] 1 Café ✓ 日本"])

    def test_errors_go_to_stderr_with_exit_code_1(self):
        self.lines("add", "only")
        for args in (("done", "99"), ("remove", "99"), ("done",), ("add",), ("explode",), ("done", "abc")):
            with self.subTest(args=args):
                code, out, err = self.todo(*args)
                self.assertEqual(code, 1)
                self.assertTrue(err.strip(), "a message on stderr")
        self.assertEqual(self.lines("list"), ["[ ] 1 only"])  # nothing changed

    def test_default_file_is_todo_json_in_the_current_folder(self):
        env = {key: value for key, value in self.env.items() if key != "TODO_FILE"}
        with tempfile.TemporaryDirectory() as other:
            self.assertEqual(self.todo("add", "here", env=env, cwd=other)[0], 0)
            self.assertTrue((Path(other) / "todo.json").is_file())
        self.assertFalse((self.dir / "tasks.json").exists())


if __name__ == "__main__":
    unittest.main()
