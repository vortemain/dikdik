"""A dev server Dikdik started never outlives Dikdik: Windows ends it with Dikdik's process,
even when Dikdik is killed or exits for an update; the updater stops live previews first."""
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def answering(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


@unittest.skipUnless(os.name == "nt", "the job object is Windows-only; POSIX stops servers on close")
class WindowsLifetimeTests(unittest.TestCase):
    def test_a_background_server_ends_when_dikdik_is_killed(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with tempfile.TemporaryDirectory() as folder:
            script = textwrap.dedent(f"""
                import os, socket, sys, time
                sys.path.insert(0, {str(ROOT)!r})
                from pathlib import Path
                from dikdik.local_exec import BackgroundShells
                shells = BackgroundShells()
                shells.start(Path({folder!r}), f'"{{sys.executable}}" -m http.server {port} --bind 127.0.0.1')
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        socket.create_connection(("127.0.0.1", {port}), timeout=0.5).close()
                        break
                    except OSError:
                        time.sleep(0.2)
                print("up", flush=True)
                os._exit(0)  # no cleanup at all: the way a crash, a kill or an update exits
            """)
            done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
            self.assertIn("up", done.stdout, done.stderr)
            deadline = time.monotonic() + 10
            while answering(port) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertFalse(answering(port), "the dev server outlived the process that started it")


class UpdaterStopsLivePreviewsTests(unittest.TestCase):
    def test_exiting_for_an_update_stops_running_previews_first(self):
        from dikdik import local_preview, updater
        live = local_preview.LiveServer()
        stopped = []
        with patch.object(local_preview.LiveServer, "stop", lambda self: stopped.append(self)), \
                patch("dikdik.updater.os._exit") as exit_now:
            updater._exit_soon(0)
            deadline = time.monotonic() + 5
            while not exit_now.called and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertIn(live, stopped)
        exit_now.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
