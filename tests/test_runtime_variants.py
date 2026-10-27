"""Runtime variants: --version/--info, flag conflicts and honest port failures."""

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def run_server(*flags, timeout=90):
    return subprocess.run([sys.executable, "-m", "dikdik.server", *flags], timeout=timeout,
                          capture_output=True, text=True, cwd=Path(__file__).parent.parent)


class RuntimeVariantTests(unittest.TestCase):
    def test_version_is_standalone(self):
        done = run_server("--version")
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith("Dikdik 0."), done.stdout)
        self.assertEqual(done.stderr, "")

    def test_info_prints_runtime_json(self):
        done = run_server("--info")
        self.assertEqual(done.returncode, 0)
        info = json.loads(done.stdout)
        self.assertEqual(info["version"], __import__("dikdik").__version__)
        self.assertIn("platform", info)
        self.assertIn("rust_indexer", info)

    def test_version_and_info_are_mutually_exclusive(self):
        for flags in (["--version", "--info"], ["--version", "--port", "1"], ["--info", "-v"]):
            with self.subTest(flags=flags):
                done = run_server(*flags)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("standalone", done.stderr)

    def test_missing_workspace_refused_without_traceback(self):
        done = run_server()
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("Traceback", done.stderr + done.stdout)
        self.assertIn("workspace", done.stderr)

    def test_missing_workspace_path_refused_without_traceback(self):
        done = run_server("--workspace", "absent-not-created-directory")
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("Traceback", done.stderr + done.stdout)

    def test_port_in_use_refused_cleanly(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        with tempfile.TemporaryDirectory() as data:
            try:
                done = run_server("--workspace", Path(__file__).parent.parent / "examples" / "repair",
                                  "--data", data, "--port", str(port), "--no-browser")
            finally:
                listener.close()
            self.assertNotEqual(done.returncode, 0)
            self.assertNotIn("Traceback", done.stderr + done.stdout)
            self.assertIn("already in use", done.stderr + done.stdout)

    def test_locked_data_directory_refused_cleanly(self):
        import threading

        lock = Path(tempfile.mkdtemp()) / "service.lock"
        from dikdik.locking import DataLock
        holder = DataLock(lock)
        with tempfile.TemporaryDirectory() as workspace:
            done = run_server("--workspace", workspace, "--data", str(lock.parent), "--no-browser")
        holder.close()
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("Traceback", done.stderr + done.stdout)
        self.assertIn("already in use", done.stderr + done.stdout)


if __name__ == "__main__":
    unittest.main()
