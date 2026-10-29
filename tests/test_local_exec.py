"""An exited shell must not leave its inherited output pipe hanging forever."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.local_exec import check_project_search, run


class LocalExecTests(unittest.TestCase):
    def orphan_command(self):
        # The child inherits stdout, but the original shell/process exits immediately.
        return [sys.executable, '-c',
                "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
                "print('parent exiting', flush=True)"]

    def test_timeout_kills_descendants_after_the_parent_exits(self):
        with tempfile.TemporaryDirectory() as temp, patch('dikdik.local_exec.shell_argv', return_value=(self.orphan_command(), 'python')):
            started = time.monotonic()
            result = run(Path(temp), 'orphan test', threading.Event(), timeout=.7)
        self.assertEqual(result['status'], 'timed_out')
        self.assertIn('parent exiting', result['output'])
        self.assertLess(time.monotonic() - started, 3, result)

    def test_stop_kills_descendants_after_the_parent_exits(self):
        cancel = threading.Event()
        timers = []

        def output(text):
            if 'parent exiting' in text:
                timer = threading.Timer(.2, cancel.set)
                timers.append(timer)
                timer.start()

        try:
            with tempfile.TemporaryDirectory() as temp, patch('dikdik.local_exec.shell_argv', return_value=(self.orphan_command(), 'python')):
                started = time.monotonic()
                result = run(Path(temp), 'orphan test', cancel, timeout=4, on_chunk=output)
            self.assertEqual(result['status'], 'cancelled')
            self.assertLess(time.monotonic() - started, 3, result)
        finally:
            cancel.set()
            for timer in timers:
                timer.join()

    def test_root_discovery_searches_are_rejected_with_project_guidance(self):
        for command in ('find / -name "WebsiteLinks.tsx" 2>/dev/null', 'find "/" -name x',
                        "find '/' -name x", 'cd src && find / -name x', 'find /c/ -name x',
                        'find C:/ -name x', 'find /mnt/c/ -name x', '/usr/bin/find / -name x'):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'list_files'):
                check_project_search(command)
        for command in ('find . -name "WebsiteLinks.tsx"', 'find src -name x', 'echo "find /"',
                        'find /workspace/src -name x', 'python -m pytest'):
            with self.subTest(command=command):
                check_project_search(command)
