"""Launch a frozen desktop host and exercise its authenticated local API."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    bundle = ROOT / "dist/desktop/Dikdik"
    executable = bundle / ("Dikdik.exe" if sys.platform == "win32" else "Dikdik")
    if sys.platform == "darwin":
        executable = bundle.with_suffix(".app") / "Contents/MacOS/Dikdik"
    with tempfile.TemporaryDirectory(prefix="dikdik-desktop-smoke-", ignore_cleanup_errors=True) as directory:
        base = Path(directory).resolve()
        project, data = base / "project", base / "data"
        project.mkdir()
        (project / "app.py").write_text("value = 1\n")
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        with (base / "desktop.log").open("wb") as log:
            process = subprocess.Popen([str(executable), str(project), "--data", str(data), "--port", str(port)],
                                       stdout=log, stderr=log, start_new_session=os.name != "nt")
            try:
                def api(path, body=None):
                    headers = {"Content-Type": "application/json"}
                    token = data / "web.token"
                    if token.exists():
                        headers["Authorization"] = "Bearer " + token.read_text().strip()
                    request = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                        data=json.dumps(body).encode() if body is not None else None, headers=headers)
                    with urllib.request.urlopen(request, timeout=45) as response:
                        return json.load(response)

                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Frozen desktop exited before readiness")
                    try:
                        status = api("/api/desktop/notifications")
                        if status.get("desktop") and status.get("ready"):
                            break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen desktop did not initialize its native host")
                    time.sleep(.5)
                if sys.platform == "win32":
                    assert status.get("available"), status
                assert api("/api/server-info")["version"] == version
                task = api("/api/tasks", {"objective": "Desktop bundle smoke check", "mode": "offline",
                    "verification": "syntax", "approve_external": False, "approve_execution": False,
                    "edits": [{"path": "app.py", "before": "value = 1", "after": "value = 2"}]})["id"]
                deadline = time.monotonic() + 60
                while True:
                    state = api(f"/api/tasks/{task}")["state"]
                    if state in {"completed", "failed", "blocked"}:
                        assert state == "completed", state
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen desktop task did not finish")
                    time.sleep(.5)
                session = api("/api/browser/sessions", {"approve_browser": True, "approve_execution": True})["id"]
                capture = api(f"/api/tasks/{session}/browser", {"url": "https://example.com/", "approve_browser": True, "approve_execution": True})
                assert capture.get("status") == "passed", capture
                print(f"Frozen desktop {version}: native host, authenticated task and bundled Chromium passed")
            except Exception:
                log.flush()
                output = (base / "desktop.log").read_text(errors="replace")[-12000:]
                token_file = data / "web.token"
                if token_file.exists():
                    output = output.replace(token_file.read_text().strip(), "<redacted>")
                print(output)
                raise
            finally:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Cocoa may defer Python signal handlers while its GUI loop
                    # is idle. Stop only this fixture's isolated process group.
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
