"""Self-updater: version logic, asset selection, checksums, the background download, and the
platform hand-off that installs after Dikdik exits and reopens the new version."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from dikdik import updater


class VersionTests(unittest.TestCase):
    def test_parse_numeric_tuples_and_strips_prefix(self):
        self.assertEqual(updater.parse_version("0.2.0"), (0, 2, 0))
        self.assertEqual(updater.parse_version("v1.10.3"), (1, 10, 3))
        self.assertEqual(updater.parse_version("v0.3.0-rc1"), (0, 3, 0))  # prerelease suffix ignored

    def test_is_newer_compares_numerically(self):
        self.assertTrue(updater.is_newer("v0.3.0", "0.2.0"))
        self.assertFalse(updater.is_newer("0.2.0", "0.2.0"))
        self.assertFalse(updater.is_newer("v0.1.9", "0.2.0"))
        self.assertTrue(updater.is_newer("1.0", "0.9.9"))


class AssetTests(unittest.TestCase):
    def test_picks_the_installer_for_each_platform(self):
        with patch("dikdik.updater.sys.platform", "win32"):
            self.assertEqual(updater.platform_asset_name("v0.3.0"), "Dikdik-0.3.0-Windows-x64-Setup.exe")
            asset = {"name": "Dikdik-0.3.0-Windows-x64-Setup.exe", "browser_download_url": "u", "size": 1}
            self.assertIs(updater.pick_asset([{"name": "other"}, asset], "v0.3.0"), asset)
            self.assertIsNone(updater.pick_asset([], "v0.3.0"))
        with patch("dikdik.updater.sys.platform", "darwin"), patch("dikdik.updater.platform.machine", return_value="arm64"):
            self.assertEqual(updater.platform_asset_name("v0.3.0"), "Dikdik-0.3.0-macOS-arm64.dmg")
        with patch("dikdik.updater.sys.platform", "darwin"), patch("dikdik.updater.platform.machine", return_value="x86_64"):
            self.assertEqual(updater.platform_asset_name("v0.3.0"), "Dikdik-0.3.0-macOS-x86_64.dmg")
        with patch("dikdik.updater.sys.platform", "linux"):
            self.assertEqual(updater.platform_asset_name("v0.3.0"), "Dikdik-0.3.0-Linux-amd64.deb")
        self.assertEqual(updater.wheel_asset_name("v0.3.0"), "dikdik-0.3.0-py3-none-any.whl")

    def test_install_kind_follows_packaging(self):
        with patch("dikdik.updater.sys.frozen", False, create=True):
            self.assertEqual(updater.install_kind(), "python")
        for platform, kind in (("win32", "windows"), ("darwin", "macos"), ("linux", "linux")):
            with patch("dikdik.updater.sys.frozen", True, create=True), patch("dikdik.updater.sys.platform", platform):
                self.assertEqual(updater.install_kind(), kind)


def release(tag, **fields):
    found = {"tag_name": tag, "name": f"Dikdik {tag}", "body": "## Changes\n- faster", "html_url": "https://example/dikdik/" + tag,
             "assets": [{"name": updater.platform_asset_name(tag), "browser_download_url": "https://asset", "size": 2},
                        {"name": updater.wheel_asset_name(tag), "browser_download_url": "https://wheel", "size": 3},
                        {"name": "SHA256SUMS.txt", "browser_download_url": "https://checksums"}]}
    found.update(fields)
    return found


class CheckTests(unittest.TestCase):
    def setUp(self):
        updater._checked.clear()
        self.addCleanup(updater._checked.clear)

    def check(self, releases, kind="windows", version="0.2.0", **kwargs):
        with patch.object(updater, "__version__", version), patch.object(updater, "install_kind", return_value=kind), \
                patch.object(updater, "fetch_releases", return_value=releases) as fetch:
            return updater.check(**kwargs), fetch

    def test_a_packaged_app_can_install_a_newer_release(self):
        info, _ = self.check([release("v0.9.9")])
        self.assertTrue(info["available"])
        self.assertTrue(info["can_install"])
        self.assertEqual(info["latest"], "v0.9.9")
        self.assertEqual(info["current"], "0.2.0")
        self.assertEqual(info["asset"]["name"], updater.platform_asset_name("v0.9.9"))
        self.assertEqual(info["checksums_url"], "https://checksums")
        self.assertIn("faster", info["notes"])
        self.assertEqual(info["pip_command"], "")

    def test_a_python_install_gets_the_pip_command_never_an_installer(self):
        info, _ = self.check([release("v0.9.9")], kind="python")
        self.assertTrue(info["available"])
        self.assertFalse(info["can_install"])
        self.assertEqual(info["pip_command"], 'python -m pip install --upgrade "https://wheel"')
        with self.assertRaisesRegex(ValueError, "pip"):
            updater.start(info)

    def test_without_checksums_nothing_is_installed(self):
        bare = release("v0.9.9"); bare["assets"] = bare["assets"][:1]
        info, _ = self.check([bare])
        self.assertTrue(info["available"])
        self.assertFalse(info["can_install"])

    def test_not_available_for_the_same_version(self):
        info, _ = self.check([release("v0.2.0")])
        self.assertFalse(info["available"])
        self.assertFalse(info["can_install"])

    def test_a_check_is_reused_unless_forced(self):
        _, first = self.check([release("v0.9.9")])
        _, second = self.check([release("v0.9.9")])
        _, forced = self.check([release("v0.9.9")], force=True)
        self.assertEqual((first.call_count, second.call_count, forced.call_count), (1, 0, 1))

    def test_latest_release_selects_newest_published_prerelease_for_platform(self):
        # GitHub hides flagged prereleases from /releases/latest; 0.x publishes
        # every build that way, so the updater must pick from the release list.
        newest = release("v0.3.1", prerelease=True, published_at="2026-10-30T23:10:00Z")
        drafts = [release("v0.4.0", draft=True), release("v0.3.2", draft=True, prerelease=True)]
        missing_installer = release("v0.3.2"); missing_installer["assets"] = [{"name": "SHA256SUMS.txt"}]
        ordered = [newest, missing_installer, *drafts, release("v0.2.0", prerelease=True, published_at="2026-10-29T16:28:05Z")]
        self.assertIs(updater.latest_release(ordered), newest)
        # A draft with a higher number is invisible: selection falls to v0.2.0.
        older = release("v0.2.0", prerelease=True)
        self.assertIs(updater.latest_release([drafts[0], older]), older)
        with self.assertRaisesRegex(RuntimeError, "No published Dikdik release"):
            updater.latest_release([missing_installer, *drafts])


class BackgroundUpdateTests(unittest.TestCase):
    """start() downloads with progress, verifies, records the pending update and hands off."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "update"
        for name, value in (("update_dir", lambda: self.folder), ("_exit_soon", lambda *a: None)):
            patcher = patch.object(updater, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        updater._state.clear(); updater._state["state"] = "idle"
        self.addCleanup(updater._state.clear)
        self.info = {"latest": "v0.9.9", "url": "https://example/v0.9.9", "can_install": True, "checksums_url": "https://checksums",
                     "asset": {"name": "Dikdik-0.9.9-Windows-x64-Setup.exe", "url": "https://asset", "size": 15}}
        self.payload = b"installer-bytes"

    def fake_download(self, checksum=None, gate=None):
        def download(url, dest, timeout=updater.TIMEOUT, on_progress=None):
            if url == "https://checksums":
                digest = checksum or hashlib.sha256(self.payload).hexdigest()
                dest.write_text(f"{digest}  {self.info['asset']['name']}\n", encoding="utf-8")
                return
            if gate is not None:
                on_progress(5, 15)
                gate.wait(5)
                if updater._cancel.is_set():
                    raise updater.Cancelled()
            dest.write_bytes(self.payload)
            if on_progress:
                on_progress(len(self.payload), len(self.payload))
        return download

    def wait(self, *states):
        deadline = time.monotonic() + 5
        while updater.status()["state"] not in states:
            if time.monotonic() > deadline:
                self.fail(f"stuck in {updater.status()}")
            time.sleep(0.01)
        return updater.status()

    def test_a_verified_download_is_handed_off_and_remembered_for_the_next_launch(self):
        with patch.object(updater, "_download", self.fake_download()), patch.object(updater, "hand_off") as hand_off, \
                patch.object(updater, "__version__", "0.2.0"):
            updater.start(self.info)
            done = self.wait("restarting", "failed")
            self.assertEqual(done["state"], "restarting", done)
            installer = hand_off.call_args.args[0]
            self.assertEqual(installer.read_bytes(), self.payload)  # kept on disk for the installer, not a temp dir
            self.assertEqual(json.loads((self.folder / "pending.json").read_text())["to"], "v0.9.9")
            self.assertFalse(updater.previous_result()["ok"])  # still 0.2.0: the update did not take effect
        with patch.object(updater, "__version__", "0.9.9"):
            self.assertEqual(updater.previous_result(), {"ok": True, "from": "0.2.0", "to": "v0.9.9", "url": "https://example/v0.9.9"})
            updater.dismiss_previous()
            self.assertIsNone(updater.previous_result())

    def test_a_checksum_mismatch_deletes_the_download_and_installs_nothing(self):
        with patch.object(updater, "_download", self.fake_download(checksum="0" * 64)), patch.object(updater, "hand_off") as hand_off:
            updater.start(self.info)
            failed = self.wait("failed")
        self.assertIn("checksum", failed["error"])
        hand_off.assert_not_called()
        self.assertFalse((self.folder / self.info["asset"]["name"]).exists())
        self.assertIsNone(updater.previous_result())

    def test_a_failed_hand_off_keeps_this_version_running_and_says_why(self):
        with patch.object(updater, "_download", self.fake_download()), \
                patch.object(updater, "hand_off", side_effect=RuntimeError("Dikdik cannot replace itself in /Applications.")):
            updater.start(self.info)
            failed = self.wait("failed")
        self.assertIn("cannot replace itself", failed["error"])
        self.assertIsNone(updater.previous_result())

    def test_a_download_can_be_cancelled_and_progress_is_reported(self):
        gate = threading.Event()
        with patch.object(updater, "_download", self.fake_download(gate=gate)), patch.object(updater, "hand_off") as hand_off:
            updater.start(self.info)
            deadline = time.monotonic() + 5
            while updater.status().get("done") != 5 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual((updater.status()["done"], updater.status()["total"]), (5, 15))
            self.assertEqual(updater.start(self.info)["state"], "downloading")  # a second click does not start another
            updater.cancel()
            gate.set()
            stopped = self.wait("idle")
        self.assertTrue(stopped["cancelled"])
        hand_off.assert_not_called()


class HandOffTests(unittest.TestCase):
    def test_windows_installs_silently_after_dikdik_exits_then_reopens_it(self):
        argv = updater.windows_helper(Path(r"C:\Temp\dikdik update\Setup's.exe"), Path(r"C:\Users\a\Programs\Dikdik\Dikdik.exe"), 4242)
        script = argv[-1]
        self.assertEqual(argv[0], "powershell")
        self.assertIn("-WindowStyle", argv)
        self.assertLess(script.index("Wait-Process -Id 4242"), script.index("/VERYSILENT"))  # files are free first
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"):
            self.assertIn(flag, script)
        self.assertNotIn("'/S'", script)  # the NSIS flag; Dikdik's installer is Inno Setup
        self.assertIn("'C:\\Temp\\dikdik update\\Setup''s.exe'", script)  # PowerShell quoting
        self.assertTrue(script.rstrip().endswith("Start-Process -FilePath 'C:\\Users\\a\\Programs\\Dikdik\\Dikdik.exe'"))

    def test_macos_swaps_the_bundle_with_ditto_and_falls_back_to_the_disk_image(self):
        script = updater.macos_helper(PurePosixPath("/tmp/Dikdik-0.9.9.dmg"), PurePosixPath("/Users/a/Applications/Dikdik.app"), 77)[-1]
        self.assertTrue(script.startswith("while kill -0 77"))
        self.assertIn("ditto \"$m/Dikdik.app\" '/Users/a/Applications/Dikdik.app.new'", script)  # symlinks and signature intact
        self.assertIn("xattr -dr com.apple.quarantine '/Users/a/Applications/Dikdik.app'", script)
        self.assertIn("else open '/tmp/Dikdik-0.9.9.dmg'", script)

    def test_macos_bundle_is_found_from_the_running_executable(self):
        self.assertEqual(updater.macos_bundle("/Users/a/Apps/Dikdik.app/Contents/MacOS/Dikdik"), PurePosixPath("/Users/a/Apps/Dikdik.app"))
        self.assertEqual(updater.macos_bundle("/usr/local/bin/dikdik"), PurePosixPath("/Applications/Dikdik.app"))

    def test_linux_uses_pkexec_apt_and_relaunches_after_exit(self):
        calls = []
        with patch.object(updater, "install_kind", return_value="linux"), \
                patch("dikdik.updater.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
                patch("dikdik.updater.subprocess.run", side_effect=lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0})()), \
                patch.object(updater, "_detached") as detached, patch("dikdik.updater.sys.executable", "/opt/dikdik/Dikdik"):
            updater.hand_off(Path("/tmp/Dikdik-0.9.9-Linux-amd64.deb"))
        self.assertEqual(calls[0][:3], ["/usr/bin/pkexec", "/usr/bin/apt-get", "install"])
        self.assertIn("exec '/opt/dikdik/Dikdik'", detached.call_args.args[0][-1])

    def test_linux_without_pkexec_opens_the_package_instead(self):
        with patch.object(updater, "install_kind", return_value="linux"), \
                patch("dikdik.updater.shutil.which", side_effect=lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None), \
                patch("dikdik.updater.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "software installer"):
                updater.hand_off(Path("/tmp/x.deb"))
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", str(Path("/tmp/x.deb"))])

    def test_a_python_install_is_never_replaced(self):
        with patch.object(updater, "install_kind", return_value="python"):
            with self.assertRaisesRegex(RuntimeError, "pip"):
                updater.hand_off(Path("installer"))


class ChecksumTests(unittest.TestCase):
    def test_expected_hash_matches_by_filename(self):
        text = "abc123  Dikdik-0.3.0-Windows-x64-Setup.exe\n" + "0" * 64 + "  Dikdik-0.3.0-macOS-arm64.dmg\n" + "1" * 64 + " *Dikdik-0.3.0-Linux-amd64.deb\n"
        self.assertEqual(updater._expected_hash(text, "Dikdik-0.3.0-macOS-arm64.dmg"), ("0" * 64))
        self.assertEqual(updater._expected_hash(text, "Dikdik-0.3.0-Linux-amd64.deb"), ("1" * 64))  # binary-mode marker
        # 64-hex only; a short token is not a sha256
        self.assertIsNone(updater._expected_hash("abc123  Dikdik-0.3.0-Windows-x64-Setup.exe", "Dikdik-0.3.0-Windows-x64-Setup.exe"))


if __name__ == "__main__":
    unittest.main()
