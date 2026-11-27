"""Connect to host (ADR-009) against a fake `ssh`: bootstrap, host keys, tunnel, failures.

tests/fixtures/fake_ssh.py stands in for OpenSSH. It runs the bootstrap's Python steps
on this computer with a fake home folder, so the install, the remote server start and
the tunnel are real processes; no real SSH server is involved.
"""

import base64
import hashlib
import http.client
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dikdik import ssh_remote
from dikdik.engine import Engine
from dikdik.remote import RemoteConfig
from dikdik.server import make_server
from dikdik.ssh_remote import SSHConnections, SSHError, SSHSession

FAKE = str(Path(__file__).parent / "fixtures" / "fake_ssh.py")


class HostListTests(unittest.TestCase):
    def test_config_hosts_follow_includes_and_skip_patterns(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "conf.d").mkdir()
            (root / "config").write_text(
                "# comment\nInclude conf.d/*.conf\n\nHost devbox build-1 *.internal !bad\n  HostName 10.0.0.5\n  User ada\n"
                "Host=gpu\n  HostName=gpu.example.com\nHost *\n  User nobody\nMatch host x\n  User ignored\n", encoding="utf-8")
            (root / "conf.d" / "work.conf").write_text("Host work\n  User grace\n", encoding="utf-8")
            hosts = {h["name"]: h for h in ssh_remote.config_hosts(root / "config")}
            self.assertEqual(sorted(hosts), ["build-1", "devbox", "gpu", "work"])
            self.assertEqual((hosts["devbox"]["hostname"], hosts["devbox"]["user"]), ("10.0.0.5", "ada"))
            self.assertEqual(hosts["gpu"]["hostname"], "gpu.example.com")
            self.assertEqual(hosts["work"]["user"], "grace")
            self.assertEqual(ssh_remote.config_hosts(root / "missing"), [])

    def test_find_ssh_uses_the_path(self):
        with tempfile.TemporaryDirectory() as folder:
            name = "ssh.exe" if os.name == "nt" else "ssh"
            fake = Path(folder) / name
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": folder}):
                self.assertEqual(Path(ssh_remote.find_ssh()).resolve(), fake.resolve())

    def test_no_ssh_client_is_explained(self):
        manager = SSHConnections()
        with mock.patch.object(ssh_remote, "find_ssh", return_value=None):
            self.assertEqual(manager.hosts()["available"], False)
            self.assertIn("OpenSSH", manager.hosts()["reason"])
            with self.assertRaisesRegex(SSHError, "OpenSSH"):
                manager.connect("box", "~/app")

    def test_hosts_and_folders_are_checked(self):
        for good in ("devbox", "ada@10.0.0.5", "ssh://ada@host.example.com:2222", "user@[::1]"):
            self.assertEqual(ssh_remote.check_host(good), good)
        for bad in ("", "-oProxyCommand=calc", "a b", "host;rm", "x" * 300):
            with self.subTest(bad=bad), self.assertRaises(SSHError):
                ssh_remote.check_host(bad)
        for good in ("~", "~/projects/app", "/srv/my app"):
            self.assertEqual(ssh_remote.check_folder(good), good)
        for bad in ("", "projects/app", "C:\\x", "/a\nb"):
            with self.subTest(bad=bad), self.assertRaises(SSHError):
                ssh_remote.check_folder(bad)


class BootstrapPieceTests(unittest.TestCase):
    def test_probe_picks_the_newest_python_that_is_new_enough(self):
        found = ssh_remote.parse_probe("DIKDIK-PY 3.10.12 /usr/bin/python3\nDIKDIK-PY 3.13.2 /opt/py/bin/python3.13\n"
                                       "DIKDIK-PY 3.14.0 relative/python\nDIKDIK-OS Linux\nDIKDIK-STAMP abc\n")
        self.assertEqual(found["python"]["path"], "/opt/py/bin/python3.13")
        self.assertEqual((found["os"], found["stamp"], len(found["pythons"])), ("Linux", "abc", 3))
        self.assertIsNone(ssh_remote.parse_probe("DIKDIK-PY 3.12.1 /usr/bin/python3\n")["python"])

    def test_probe_runs_under_sh_whatever_the_login_shell(self):
        command = ssh_remote.probe_command()
        self.assertTrue(command.startswith("sh -c "))
        script = shlex.split(command)[2]
        self.assertIn("python3.13", script)
        self.assertIn(f".dikdik-remote/{ssh_remote.VERSION}/stamp", script)

    def test_remote_commands_quote_every_argument(self):
        command = ssh_remote.remote_command("/usr/bin/python3", ssh_remote.RUN, "0.8.0", "/srv/it's here", "--ssh-session", "a b")
        self.assertEqual(shlex.split(command), ["/usr/bin/python3", "-I", "-c", ssh_remote.RUN, "0.8.0", "/srv/it's here",
                                                "--ssh-session", "a b"])

    def test_archive_is_deterministic_and_leaves_out_native_and_cached_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "pkg"
            (root / "web").mkdir(parents=True)
            (root / "__pycache__").mkdir()
            (root / "__init__.py").write_text("x = 1\n", encoding="utf-8")
            (root / "web" / "app.js").write_text("ok", encoding="utf-8")
            for skipped in ("__pycache__/a.cpython-313.pyc", "dikdik_index.cp313-win_amd64.pyd", "native.so", "b.pyc"):
                (root / skipped).write_bytes(b"\0")
            first, digest = ssh_remote.package_archive(root)
            second, again = ssh_remote.package_archive(root)
            self.assertEqual((first, digest), (second, again))
            self.assertEqual(digest, hashlib.sha256(first).hexdigest())
            with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
                self.assertEqual(archive.getnames(), ["dikdik/__init__.py", "dikdik/web/app.js"])

    def test_install_script_unpacks_safely_and_stamps(self):
        with tempfile.TemporaryDirectory() as home:
            data, digest = ssh_remote.package_archive()
            env = {**os.environ, "HOME": home, "USERPROFILE": home}
            done = subprocess.run([sys.executable, "-I", "-c", ssh_remote.INSTALL, "9.9.9", digest], input=data,
                                  capture_output=True, env=env, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr.decode())
            target = Path(home) / ".dikdik-remote" / "9.9.9"
            self.assertTrue((target / "dikdik" / "server.py").is_file())
            self.assertFalse(list(target.rglob("*.pyd")))
            self.assertEqual((target / "stamp").read_text(encoding="utf-8").strip(), digest)
            evil = io.BytesIO()
            with tarfile.open(fileobj=evil, mode="w:gz") as archive:
                info = tarfile.TarInfo("../escaped.txt")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            refused = subprocess.run([sys.executable, "-I", "-c", ssh_remote.INSTALL, "9.9.9", "x"], input=evil.getvalue(),
                                     capture_output=True, env=env, timeout=60)
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((Path(home) / ".dikdik-remote" / "escaped.txt").exists())
            self.assertEqual((target / "stamp").read_text(encoding="utf-8").strip(), digest)

    def test_fingerprints_match_openssh_format(self):
        blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
        line = f"|1|salt=|hash= ssh-ed25519 {base64.b64encode(blob).decode()}\n@cert-authority * ssh-rsa !!notbase64\n"
        expected = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        self.assertEqual(ssh_remote.fingerprints(line), [{"type": "ssh-ed25519", "fingerprint": expected}])

    def test_failures_are_explained_without_echoing_secrets(self):
        cases = {
            "@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @\nHost key verification failed.": ("host_key_changed", "ssh-keygen -R"),
            "No ED25519 host key is known for box and you have requested strict checking.": ("host_key_unknown", "known_hosts"),
            "ada@box: Permission denied (publickey,password).": ("auth", "ssh-copy-id box"),
            "ssh: Could not resolve hostname box: Name or service not known": ("unreachable", "could not be found"),
            "ssh: connect to host box port 22: Connection refused": ("unreachable", "refused"),
            "ssh: connect to host box port 22: Connection timed out": ("unreachable", "did not answer"),
            "'sh' is not recognized as an internal or external command": ("unsupported", "Linux and macOS"),
        }
        for stderr, (kind, words) in cases.items():
            with self.subTest(kind=kind):
                found, text = ssh_remote.explain(stderr, "box")
                self.assertEqual(found, kind)
                self.assertIn(words, text)
        kind, text = ssh_remote.explain("something odd: Authorization: Bearer abcdefghijklmnop", "box")
        self.assertEqual(kind, "failed")
        self.assertNotIn("abcdefghijklmnop", text)


class FakeSSH:
    """A manager wired to tests/fixtures/fake_ssh.py with its own state folder."""

    def __init__(self, test, **scenario):
        self.temp = tempfile.TemporaryDirectory()
        test.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        (self.state / "home" / "proj").mkdir(parents=True)
        (self.state / "home" / "proj" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.scenario(**scenario)
        patcher = mock.patch.dict(os.environ, {"FAKE_SSH_DIR": str(self.state)})
        patcher.start()
        test.addCleanup(patcher.stop)
        self.manager = SSHConnections(ssh=[sys.executable, FAKE])
        test.addCleanup(self.manager.close_all)

    def scenario(self, **values):
        (self.state / "scenario.json").write_text(json.dumps(values), encoding="utf-8")

    def calls(self):
        path = self.state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def wait(self, cid, states, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            conn = next(c for c in self.manager.list() if c["id"] == cid)
            if conn["state"] in states:
                return conn
            time.sleep(0.1)
        raise AssertionError(f"connection stayed {conn['state']} ({conn['step']})")


def get(port, path, token=None, host=None, method="GET", body=None, origin=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Host": host or f"localhost:{port}", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if origin:
        headers["Origin"] = origin
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, (json.loads(data) if data else None)


def answers(port):
    try:
        return get(port, "/health")[0] == 200
    except OSError:
        return False


class ConnectionTests(unittest.TestCase):
    def test_first_connection_asks_about_the_host_key_then_installs_starts_and_tunnels(self):
        fake = FakeSSH(self, host_key="unknown")
        started = fake.manager.connect("ada@devbox", "~/proj")
        conn = fake.wait(started["id"], {"host_key", "failed"})
        self.assertEqual(conn["state"], "host_key", conn["error"])
        self.assertFalse((fake.state / "known_hosts").exists())
        fingerprint = conn["host_keys"][0]["fingerprint"]
        self.assertTrue(fingerprint.startswith("SHA256:"))
        with self.assertRaisesRegex(SSHError, "not the one"):
            fake.manager.trust(started["id"], "SHA256:somethingelse")
        fake.manager.trust(started["id"], fingerprint)
        conn = fake.wait(started["id"], {"connected", "failed"})
        self.assertEqual(conn["state"], "connected", conn["error"])
        self.assertIn("ssh-ed25519", (fake.state / "known_hosts").read_text(encoding="utf-8"))
        self.assertNotIn("token", json.dumps(fake.manager.list()))

        calls = fake.calls()
        for call in calls:
            options = [o.lower() for o in call["options"]]
            self.assertNotIn("stricthostkeychecking=no", options)
            if "-G" not in call["flags"]:
                self.assertIn("batchmode=yes", options)
                self.assertEqual(call["argv"][call["argv"].index(call["host"]) - 1], "--")
        scratch = [c for c in calls if "StrictHostKeyChecking=accept-new" in c["options"]]
        self.assertEqual(len(scratch), 1)
        self.assertEqual(scratch[0]["options"][0], "StrictHostKeyChecking=accept-new")
        self.assertIn("PubkeyAuthentication=no", scratch[0]["options"])
        self.assertTrue(any(o.startswith("UserKnownHostsFile=") for o in scratch[0]["options"]))
        tunnel = next(c for c in calls if "-N" in c["flags"])
        self.assertIn("ExitOnForwardFailure=yes", tunnel["options"])
        self.assertTrue(tunnel["forwards"][0].startswith(f"127.0.0.1:{conn['port']}:127.0.0.1:"))
        run = next(c for c in calls if c["command"] and "--ssh-session" in c["command"])
        self.assertIn(f"--forwarded-port {conn['port']}", run["command"])
        self.assertIn("--port 0 --no-browser", run["command"])

        link = fake.manager.link(started["id"])
        self.assertTrue(link["url"].startswith(f"http://localhost:{conn['port']}/#token="))
        token = link["url"].split("#token=")[1]
        status, project = get(conn["port"], "/api/project", token)
        self.assertEqual((status, project["name"], project["ssh_session"]), (200, "proj", {"host": "ada@devbox"}))
        self.assertEqual(get(conn["port"], "/api/project")[0], 401)
        self.assertEqual(get(conn["port"], "/api/session/local", method="POST", body={},
                             origin=f"http://localhost:{conn['port']}")[0], 403)
        status, _ = get(conn["port"], "/api/session", method="POST", body={"token": token}, origin=f"http://localhost:{conn['port']}")
        self.assertEqual(status, 200)

        fake.manager.disconnect(started["id"])
        self.assertEqual(fake.manager.list()[0]["state"], "closed")
        deadline = time.monotonic() + 20
        while answers(conn["port"]) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(answers(conn["port"]))
        with self.assertRaisesRegex(SSHError, "not connected"):
            fake.manager.link(started["id"])

        again = fake.manager.connect("ada@devbox", "~/proj")
        self.assertEqual(fake.wait(again["id"], {"connected", "failed"})["state"], "connected")
        installs = [c for c in fake.calls() if c["command"] and "DIKDIK-INSTALLED" in c["command"]]
        self.assertEqual(len(installs), 1)  # the stamp matched: nothing sent the second time
        self.assertEqual(sum(1 for c in fake.calls() if "accept-new" in json.dumps(c["options"])), 1)

    def test_disconnect_in_the_remote_window_ends_the_connection(self):
        fake = FakeSSH(self)
        started = fake.manager.connect("devbox", "~/proj")
        conn = fake.wait(started["id"], {"connected", "failed"})
        self.assertEqual(conn["state"], "connected", conn["error"])
        token = fake.manager.link(started["id"])["url"].split("#token=")[1]
        origin = f"http://localhost:{conn['port']}"
        status, _ = get(conn["port"], "/api/ssh/session/disconnect", token, method="POST", body={}, origin=origin)
        self.assertEqual(status, 200)
        conn = fake.wait(started["id"], {"closed", "failed"}, timeout=30)
        self.assertEqual((conn["state"], conn["error"]), ("closed", None))

    def test_refused_key_explains_how_to_set_one_up(self):
        fake = FakeSSH(self, auth="denied")
        conn = fake.wait(fake.manager.connect("devbox", "~/proj")["id"], {"failed"})
        self.assertEqual(conn["kind"], "auth")
        self.assertIn("ssh-keygen", conn["error"])
        self.assertIn("never asks for passwords", conn["error"])

    def test_changed_host_key_stops_and_changes_nothing(self):
        fake = FakeSSH(self, host_key="changed")
        conn = fake.wait(fake.manager.connect("devbox", "~/proj")["id"], {"failed", "host_key"})
        self.assertEqual((conn["state"], conn["kind"]), ("failed", "host_key_changed"))
        self.assertFalse((fake.state / "known_hosts").exists())
        self.assertFalse(any("accept-new" in json.dumps(c["options"]) for c in fake.calls()))

    def test_declined_host_key_is_not_saved(self):
        fake = FakeSSH(self, host_key="unknown")
        started = fake.manager.connect("devbox", "~/proj")
        fake.wait(started["id"], {"host_key"})
        fake.manager.disconnect(started["id"])
        self.assertEqual(fake.wait(started["id"], {"closed"})["state"], "closed")
        time.sleep(0.3)
        self.assertFalse((fake.state / "known_hosts").exists())
        self.assertEqual(len(fake.calls()), 2)

    def test_python_too_old_or_missing(self):
        fake = FakeSSH(self, pythons=[["3.10.12", "/usr/bin/python3"]])
        conn = fake.wait(fake.manager.connect("devbox", "~/proj")["id"], {"failed"})
        self.assertEqual(conn["kind"], "python")
        self.assertIn("Python 3.10.12", conn["error"])
        self.assertIn("3.13 or newer", conn["error"])
        fake.scenario(pythons=[])
        conn = fake.wait(fake.manager.connect("devbox", "~/other")["id"], {"failed"})
        self.assertIn("no Python", conn["error"])

    def test_unreachable_host_and_start_failure(self):
        fake = FakeSSH(self, unreachable=True)
        conn = fake.wait(fake.manager.connect("nowhere", "~/proj")["id"], {"failed"})
        self.assertEqual(conn["kind"], "unreachable")
        fake.scenario(start="fail")
        conn = fake.wait(fake.manager.connect("devbox", "/missing")["id"], {"failed"})
        self.assertEqual(conn["kind"], "start")
        self.assertIn("Workspace /missing does not exist", conn["error"])

    def test_tunnel_failure_stops_the_host_process(self):
        fake = FakeSSH(self, tunnel="fail")
        started = fake.manager.connect("devbox", "~/proj")
        conn = fake.wait(started["id"], {"failed", "connected"})
        self.assertEqual(conn["kind"], "tunnel")
        self.assertIn("Could not request local forwarding", conn["error"])
        run = fake.manager.get(started["id"]).procs["run"]
        self.assertIsNotNone(run.poll())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_repository_is_cloned_on_the_host_then_opened_and_reused(self):
        fake = FakeSSH(self)
        source = fake.state / "source" / "demo"
        source.mkdir(parents=True)
        (source / "app.py").write_text("print('demo')\n", encoding="utf-8")
        for args in (["init", "--quiet"], ["add", "app.py"],
                     ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "init"]):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        url = source.as_uri()
        started = fake.manager.connect("devbox", "~/code", url)
        self.assertEqual((started["folder"], started["parent"], started["repo"]), ("~/code/demo", "~/code", url))
        conn = fake.wait(started["id"], {"connected", "failed"})
        self.assertEqual(conn["state"], "connected", conn["error"])
        self.assertTrue((fake.state / "home" / "code" / "demo" / "app.py").is_file())
        token = fake.manager.link(started["id"])["url"].split("#token=")[1]
        self.assertEqual(get(conn["port"], "/api/project", token)[1]["name"], "demo")
        clone_call = next(c for c in fake.calls() if c["command"] and "DIKDIK-CLONED" in c["command"])
        self.assertIn("core.hooksPath=", clone_call["command"])
        fake.manager.disconnect(started["id"])

        again = fake.manager.connect("devbox", "~/code", url)
        self.assertEqual(fake.wait(again["id"], {"connected", "failed"})["state"], "connected")
        fake.manager.disconnect(again["id"])

        (fake.state / "home" / "code" / "other").mkdir()
        clash = fake.wait(fake.manager.connect("devbox", "~/code", url.rsplit("/", 1)[0] + "/other")["id"], {"connected", "failed"})
        self.assertEqual(clash["kind"], "clone")
        self.assertIn("is not a clone of that repository", clash["error"])

    def test_repository_urls_are_checked_before_connecting(self):
        fake = FakeSSH(self)
        for bad in ("-upload-pack=x", "ftp://example.com/a.git", "https://user:secret@github.com/a/b"):
            with self.subTest(bad=bad), self.assertRaises(SSHError):
                fake.manager.connect("devbox", "~/code", bad)
        self.assertEqual(ssh_remote.check_repository("git@github.com:ada/demo.git", ""), ("git@github.com:ada/demo.git", "~/projects", "demo"))
        with self.assertRaises(SSHError):
            ssh_remote.check_repository("https://github.com/ada/demo", "relative/path")

    def test_routes(self):
        fake = FakeSSH(self, auth="denied")
        route = fake.manager.route
        self.assertIsNone(route("GET", "/api/projects", {}, True))
        self.assertEqual(route("GET", "/api/ssh/hosts", {}, True)[0], 200)
        self.assertEqual(route("POST", "/api/ssh/connections", {"host": "-oProxyCommand=x", "folder": "~/a"}, True)[0], 400)
        status, conn = route("POST", "/api/ssh/connections", {"host": "box", "folder": "~/a"}, True)
        self.assertEqual(status, 202)
        self.assertEqual(route("POST", f"/api/ssh/connections/{conn['id']}/link", {}, False)[0], 403)
        self.assertEqual(route("POST", f"/api/ssh/connections/{conn['id']}/link", {}, True)[0], 400)
        self.assertEqual(route("DELETE", "/api/ssh/connections/0123456789abcdef", {}, True)[0], 404)
        self.assertEqual(route("GET", "/api/ssh/connections", {}, True)[1]["connections"][0]["id"], conn["id"])


class SessionServerTests(unittest.TestCase):
    def serve(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        (base / "proj").mkdir()
        server = make_server(Engine(base / "proj", base / "data"), 0, "tok-for-tests-only", **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: server.projects.engine.close())
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_ssh_session_answers_to_the_tunnel_port_only_with_a_token(self):
        server = self.serve(ssh_session=SSHSession("ada@devbox", 45678))
        port = server.server_port
        for host in ("localhost:45678", "127.0.0.1:45678", f"127.0.0.1:{port}"):
            self.assertEqual(get(port, "/api/project", "tok-for-tests-only", host=host)[0], 200, host)
        self.assertEqual(get(port, "/api/project", "tok-for-tests-only", host="localhost:45679")[0], 403)
        self.assertEqual(get(port, "/api/project", "tok-for-tests-only", host="localhost:45678", origin="http://evil.example")[0], 403)
        self.assertEqual(get(port, "/api/session/local", host="localhost:45678", method="POST", body={},
                             origin="http://localhost:45678")[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/session", body=json.dumps({"token": "tok-for-tests-only"}),
                     headers={"Host": "localhost:45678", "Origin": "http://localhost:45678", "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        self.assertTrue(response.getheader("Set-Cookie").startswith("dikdik_session_45678="))
        status, hosts = get(port, "/api/ssh/hosts", "tok-for-tests-only", host="localhost:45678")
        self.assertEqual((status, hosts["available"]), (200, False))
        self.assertIn("remote window", hosts["reason"])

    def test_local_window_has_no_session_and_server_mode_cannot_connect(self):
        local = self.serve()
        status, project = get(local.server_port, "/api/project", "tok-for-tests-only", host=f"127.0.0.1:{local.server_port}")
        self.assertEqual((status, project["ssh_session"]), (200, None))
        self.assertEqual(get(local.server_port, "/api/ssh/session/disconnect", "tok-for-tests-only", method="POST", body={},
                             host=f"127.0.0.1:{local.server_port}", origin=f"http://127.0.0.1:{local.server_port}")[0], 403)
        hosted = self.serve(remote=RemoteConfig(listen="127.0.0.1", public_url="https://dikdik.example.com", behind_proxy=True))
        status, hosts = get(hosted.server_port, "/api/ssh/hosts", "tok-for-tests-only", host="dikdik.example.com",
                            origin="https://dikdik.example.com")
        self.assertEqual((status, hosts["available"]), (200, False))

    def test_api_keys_cannot_fetch_a_remote_sign_in_link(self):
        manager = mock.Mock(spec=SSHConnections)
        manager.route.side_effect = lambda method, path, body, owner: (403, {"error": "no"}) if path.startswith("/api/ssh/") else None
        server = self.serve(ssh=manager)
        port = server.server_port
        status, created = get(port, "/api/keys", "tok-for-tests-only", host=f"127.0.0.1:{port}", method="POST",
                              body={"name": "ci", "scopes": ["admin"]}, origin=f"http://127.0.0.1:{port}")
        self.assertEqual(status, 201)
        get(port, "/api/ssh/connections/0123456789abcdef/link", created["token"], host=f"127.0.0.1:{port}", method="POST",
            body={}, origin=f"http://127.0.0.1:{port}")
        self.assertEqual(manager.route.call_args.args[3], False)
        get(port, "/api/ssh/connections/0123456789abcdef/link", "tok-for-tests-only", host=f"127.0.0.1:{port}", method="POST",
            body={}, origin=f"http://127.0.0.1:{port}")
        self.assertEqual(manager.route.call_args.args[3], True)

    def test_session_flags_go_together(self):
        for args in (["--ssh-session", "box"], ["--forwarded-port", "5000"],
                     ["--ssh-session", "box", "--forwarded-port", "70000"]):
            with self.subTest(args=args):
                done = subprocess.run([sys.executable, "-m", "dikdik.server", *args, "--port", "0"],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 2)
                self.assertIn("--ssh-session", done.stderr)


if __name__ == "__main__":
    unittest.main()
