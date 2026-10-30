"""Self-updater version logic, asset selection, checksum parsing and apply flow."""
import unittest
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


class CheckTests(unittest.TestCase):
    def _release(self, tag, **fields):
        release = {"tag_name": tag, "name": f"Dikdik {tag}", "body": "release notes",
                   "html_url": "https://example/dikdik/" + tag, "assets": [
                        {"name": updater.platform_asset_name(tag), "browser_download_url": "https://asset", "size": 2},
                        {"name": "SHA256SUMS.txt", "browser_download_url": "https://checksums"}]}
        release.update(fields)
        return release

    def test_check_reports_available_when_newer_with_asset(self):
        with patch.object(updater, "__version__", "0.2.0"), patch.object(updater, "fetch_releases", return_value=[self._release("v0.9.9")]):
            info = updater.check()
        self.assertTrue(info["available"])
        self.assertEqual(info["latest"], "v0.9.9")
        self.assertEqual(info["current"], "0.2.0")
        self.assertEqual(info["asset"]["name"], updater.platform_asset_name("v0.9.9"))
        self.assertEqual(info["checksums_url"], "https://checksums")

    def test_check_not_available_for_same_version(self):
        tag = "v" + updater.__version__
        with patch.object(updater, "fetch_releases", return_value=[self._release(tag)]):
            self.assertFalse(updater.check()["available"])

    def test_latest_release_selects_newest_published_prerelease_for_platform(self):
        # GitHub hides flagged prereleases from /releases/latest; 0.x publishes
        # every build that way, so the updater must pick from the release list.
        newest = self._release("v0.3.1", prerelease=True, published_at="2026-10-30T23:10:00Z")
        drafts = [self._release("v0.4.0", draft=True), self._release("v0.3.2", draft=True, prerelease=True)]
        missing_installer = self._release("v0.3.2"); missing_installer["assets"] = [{"name": "SHA256SUMS.txt"}]
        ordered = [newest, missing_installer, *drafts, self._release("v0.2.0", prerelease=True, published_at="2026-10-29T16:28:05Z")]
        self.assertIs(updater.latest_release(ordered), newest)
        # A draft with a higher number is invisible: selection falls to v0.2.0.
        older = self._release("v0.2.0", prerelease=True)
        self.assertIs(updater.latest_release([drafts[0], older]), older)
        with self.assertRaisesRegex(RuntimeError, "No published Dikdik release"):
            updater.latest_release([missing_installer, *drafts])


class ApplyTests(unittest.TestCase):
    def test_apply_downloads_verifies_installs_and_schedules_relaunch(self):
        release = {"tag_name": "v0.9.9", "name": "n", "body": "", "html_url": "u", "assets": [
            {"name": updater.platform_asset_name("v0.9.9"), "browser_download_url": "u", "size": 1},
            {"name": "SHA256SUMS.txt", "browser_download_url": "c"}]}

        def fake_download(url, dest, **_kwargs):
            dest.write_bytes(b"installer-bytes")

        with patch.object(updater, "__version__", "0.2.0"), \
                patch.object(updater, "fetch_releases", return_value=[release]), \
                patch.object(updater, "_download", side_effect=fake_download), \
                patch.object(updater, "install") as install, \
                patch.object(updater, "_schedule_relaunch") as relaunch:
            result = updater.apply()
        self.assertTrue(result["ok"])
        self.assertTrue(result["restarting"])
        self.assertEqual(result["latest"], "v0.9.9")
        install.assert_called_once()
        relaunch.assert_called_once()


class ChecksumTests(unittest.TestCase):
    def test_expected_hash_matches_by_filename(self):
        text = "abc123  Dikdik-0.3.0-Windows-x64-Setup.exe\n" + "0" * 64 + "  Dikdik-0.3.0-macOS-arm64.dmg\n"
        self.assertEqual(updater._expected_hash(text, "Dikdik-0.3.0-macOS-arm64.dmg"), ("0" * 64))
        # 64-hex only; a short token is not a sha256
        self.assertIsNone(updater._expected_hash("abc123  Dikdik-0.3.0-Windows-x64-Setup.exe", "Dikdik-0.3.0-Windows-x64-Setup.exe"))


if __name__ == "__main__":
    unittest.main()