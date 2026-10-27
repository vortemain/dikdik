"""Remote Git from Dikdik: fetch, pull, push, branches and PRs with the computer's own sign-in."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import gitops
from dikdik.gitops import GitError


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class GitRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.remote = base / "remote.git"
        git(base, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        self.root = base / "work"
        git(base, "clone", "--quiet", self.remote.as_uri(), str(self.root))
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "first")
        git(self.root, "push", "--quiet", "-u", "origin", "main")
        self.other = base / "other"
        git(base, "clone", "--quiet", self.remote.as_uri(), str(self.other))

    def test_remote_info_reports_branch_and_distance(self):
        info = gitops.remote_info(self.root)
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["upstream"], "origin/main")
        self.assertEqual((info["ahead"], info["behind"]), (0, 0))
        self.assertIn("origin", info["remotes"])

    def test_push_needs_its_own_approval_then_reaches_the_remote(self):
        (self.root / "app.py").write_text("x = 2\n", encoding="utf-8")
        gitops.commit(self.root, "second", ["app.py"], ("T", "t@t"))
        self.assertEqual(gitops.remote_info(self.root)["ahead"], 1)
        for refused in (None, False, 1, "yes"):
            with self.assertRaisesRegex(GitError, "explicit approval"):
                gitops.push(self.root, refused)
        self.assertNotIn("second", git(self.remote, "log", "--format=%s", "main"))
        self.assertEqual(gitops.push(self.root, True)["ahead"], 0)
        self.assertIn("second", git(self.remote, "log", "--format=%s", "main"))

    def test_fetch_and_fast_forward_pull(self):
        (self.other / "new.py").write_text("y = 1\n", encoding="utf-8")
        git(self.other, "add", "-A")
        git(self.other, "commit", "--quiet", "-m", "from elsewhere")
        git(self.other, "push", "--quiet")
        self.assertEqual(gitops.fetch(self.root)["behind"], 1)
        self.assertEqual(gitops.pull(self.root)["behind"], 0)
        self.assertTrue((self.root / "new.py").is_file())

    def test_pull_refuses_to_merge_diverged_work(self):
        (self.other / "a.py").write_text("1\n", encoding="utf-8")
        git(self.other, "add", "-A"); git(self.other, "commit", "--quiet", "-m", "theirs"); git(self.other, "push", "--quiet")
        (self.root / "b.py").write_text("2\n", encoding="utf-8")
        gitops.commit(self.root, "mine", ["b.py"], ("T", "t@t"))
        with self.assertRaisesRegex(GitError, "both changed"):
            gitops.pull(self.root)

    def test_new_branch_pushes_with_an_upstream(self):
        self.assertEqual(gitops.switch_branch(self.root, "feature/login", create=True)["branch"], "feature/login")
        self.assertIsNone(gitops.remote_info(self.root)["upstream"])
        self.assertEqual(gitops.push(self.root, True)["upstream"], "origin/feature/login")
        for bad in ("-x", "a..b", "x.lock", "", "a b"):
            with self.assertRaises(GitError):
                gitops.switch_branch(self.root, bad, create=True)

    def test_pull_request_through_gh(self):
        with self.assertRaisesRegex(GitError, "explicit approval"):
            gitops.create_pr(self.root, {})
        with patch("dikdik.gitops.shutil.which", side_effect=lambda name: None if name == "gh" else "git"):
            with self.assertRaisesRegex(GitError, "GitHub CLI"):
                gitops.create_pr(self.root, {"approve": True})
        done = subprocess.CompletedProcess([], 0, "https://github.com/o/r/pull/7\n", "")
        real_run = subprocess.run

        def fake_run(args, **kwargs):
            return done if args and str(args[0]).endswith("gh") and args[1:3] == ["pr", "create"] else real_run(args, **kwargs)

        import shutil
        real_which = shutil.which
        with patch("dikdik.gitops.shutil.which", side_effect=lambda name, *a, **k: "C:/gh" if name == "gh" else real_which(name, *a, **k)), \
                patch("dikdik.gitops.subprocess.run", side_effect=fake_run) as run:
            result = gitops.create_pr(self.root, {"approve": True, "title": "Fix it", "body": "Details", "draft": True})
        self.assertEqual(result["url"], "https://github.com/o/r/pull/7")
        sent = next(call.args[0] for call in run.call_args_list if call.args[0][1:3] == ["pr", "create"])
        self.assertEqual(sent[3:], ["--head", "main", "--title", "Fix it", "--body", "Details", "--draft"])

    def test_remote_urls_never_show_credentials(self):
        self.assertEqual(gitops._clean_url("https://user:secret@github.com/o/r.git"), "https://github.com/o/r.git")
        self.assertEqual(gitops._clean_url("git@github.com:o/r.git"), "git@github.com:o/r.git")


if __name__ == "__main__":
    unittest.main()
