"""Local commands run under real memory and process limits, with peak memory reported."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import local_exec

PY = f'"{Path(sys.executable).as_posix()}"'
ALLOCATE = PY + ' -c "b = bytearray(600 * 1024 * 1024); b[::4096] = b\'x\' * len(b[::4096]); print(len(b))"'
SPAWN = PY + ' -c "import subprocess, sys, time; ps = [subprocess.Popen([sys.executable, \'-c\', \'import time; time.sleep(5)\']) for _ in range(40)]; print(len(ps)); [p.kill() for p in ps]"'


@unittest.skipUnless(os.name == "nt", "Job Object limits are Windows-specific")
class WindowsLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_command(self, command, limits):
        return local_exec.run(self.root, command, threading.Event(), timeout=120, limits=limits)

    def test_memory_limit_stops_runaway_allocation_and_reports_peak(self):
        limited = self.run_command(ALLOCATE, {"memory_mb": 256, "processes": 0})
        self.assertEqual(limited["status"], "failed", limited["output"][-500:])
        self.assertIn("MemoryError", limited["output"])
        # Windows counts the refused request in the job peak, so the peak reaches the limit and the note says so.
        self.assertIn("memory limit", limited["note"])
        unlimited = self.run_command(ALLOCATE, {"memory_mb": 2048, "processes": 0})
        self.assertEqual(unlimited["status"], "passed", unlimited["output"][-500:])
        self.assertGreater(unlimited["peak_memory_bytes"], 600 * 1024 * 1024)  # the allocation really happened

    def test_process_limit_stops_a_process_explosion(self):
        limited = self.run_command(SPAWN, {"memory_mb": 0, "processes": 16})
        self.assertEqual(limited["status"], "failed", limited["output"][-500:])
        self.assertEqual(self.run_command(SPAWN, {"memory_mb": 0, "processes": 0})["status"], "passed")

    def test_defaults_apply_and_leave_normal_commands_alone(self):
        result = local_exec.run(self.root, PY + ' -c "print(1)"', threading.Event(), timeout=60)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["limits"], {"memory_mb": 8192, "processes": 1024})


class SettingsTests(unittest.TestCase):
    def test_local_limits_setting_is_validated(self):
        from dikdik.engine import Engine
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "repo").mkdir()
            engine = Engine(Path(temp) / "repo", Path(temp) / "data")
            try:
                self.assertFalse(engine._local_limits()["explicit"])
                engine.update_app_settings({"local_limits": {"memory_mb": 4096}})
                self.assertEqual(engine._local_limits(), {"memory_mb": 4096, "processes": 1024, "explicit": True})
                engine.update_app_settings({"local_limits": {"memory_mb": 0, "processes": 0}})
                for bad in ({"memory_mb": 100}, {"processes": 5}, {"memory_mb": "8g"}, {"cpu": 2}, "lots"):
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        engine.update_app_settings({"local_limits": bad})
            finally:
                engine.close()

    def test_posix_memory_limit_only_when_explicit(self):
        self.assertIsNone(local_exec._posix_limits({"memory_mb": 8192}))
        if os.name != "nt":
            self.assertIsNotNone(local_exec._posix_limits({"memory_mb": 512, "explicit": True}))


if __name__ == "__main__":
    unittest.main()
