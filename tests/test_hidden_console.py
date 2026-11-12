"""Windows GUI launches start child processes without console windows."""

import subprocess
import unittest
from unittest.mock import patch

from dikdik import hidden_console
from dikdik.hidden_console import CREATE_NEW_CONSOLE, CREATE_NO_WINDOW, DETACHED_PROCESS, hidden_flags


class HiddenConsoleTests(unittest.TestCase):
    def test_flags_hide_console_unless_caller_wants_one(self):
        self.assertEqual(hidden_flags(0), CREATE_NO_WINDOW)
        self.assertEqual(hidden_flags(None), CREATE_NO_WINDOW)
        self.assertEqual(hidden_flags(0x200), 0x200 | CREATE_NO_WINDOW)
        self.assertEqual(hidden_flags(CREATE_NEW_CONSOLE), CREATE_NEW_CONSOLE)
        self.assertEqual(hidden_flags(DETACHED_PROCESS | 0x200), DETACHED_PROCESS | 0x200)

    def test_every_popen_gets_hidden_flags_once(self):
        seen = []
        class Popen:
            def __init__(self, args, *rest, **kwargs):
                seen.append((rest, kwargs))
        class Child(Popen):  # asyncio's Windows Popen subclasses and calls super().__init__
            def __init__(self, args, **kwargs):
                super().__init__(args, **kwargs)
        self.assertTrue(hidden_console.install(Popen))
        self.assertTrue(hidden_console.install(Popen))  # idempotent
        Popen(["git", "status"])
        Popen(["npm", "test"], creationflags=0x200)
        Child(["node"], creationflags=CREATE_NEW_CONSOLE)
        Popen(["x"], *([None] * 12), 0x200)  # creationflags passed positionally
        self.assertEqual(seen[0][1]["creationflags"], CREATE_NO_WINDOW)
        self.assertEqual(seen[1][1]["creationflags"], 0x200 | CREATE_NO_WINDOW)
        self.assertEqual(seen[2][1]["creationflags"], CREATE_NEW_CONSOLE)
        self.assertEqual(seen[3][0][12], 0x200 | CREATE_NO_WINDOW)
        self.assertNotIn("creationflags", seen[3][1])

    def test_noop_off_windows_or_with_a_console(self):
        original = subprocess.Popen.__init__
        with patch.object(hidden_console.sys, "platform", "linux"):
            self.assertFalse(hidden_console.install())
        with patch.object(hidden_console.sys, "platform", "win32"), \
                patch.object(hidden_console, "_has_console", return_value=True):
            self.assertFalse(hidden_console.install())
        self.assertIs(subprocess.Popen.__init__, original)


if __name__ == "__main__":
    unittest.main()
