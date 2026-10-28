"""Git accounts: status from the CLIs, tokens never shown, repository lists."""

import json
import subprocess
import unittest
from unittest.mock import patch

from dikdik import git_accounts


def done(out="", code=0):
    return subprocess.CompletedProcess([], code, out, "")


class GitAccountTests(unittest.TestCase):
    def test_status_reads_the_cli_and_hides_tokens(self):
        gh_out = ("github.com\n  ✓ Logged in to github.com account octo (keyring)\n  - Git operations protocol: ssh\n"
                  "  - Token: gho_abcdefghijklmnopqrstuvwxyz0123\n")

        def run(args, timeout=20):
            if args[0] == "git":
                return done({"user.name": "Ada", "user.email": "ada@example.com", "credential.helper": "manager"}[args[-1]] + "\n")
            return done(gh_out) if args[1:3] == ["auth", "status"] else done("", 1)

        with patch("dikdik.git_accounts.shutil.which", side_effect=lambda name: None if name == "glab" else name), \
                patch("dikdik.git_accounts._run", side_effect=run):
            status = git_accounts.status()
        self.assertEqual(status["github"], {"installed": True, "signed_in": True, "account": "octo", "protocol": "ssh",
                                            "cli": "gh", "install": "https://cli.github.com"})
        self.assertFalse(status["gitlab"]["installed"])
        self.assertEqual((status["git"]["name"], status["git"]["credential_helper"]), ("Ada", "manager"))
        self.assertNotIn("gho_", json.dumps(status))

    def test_repositories_newest_first_and_filtered(self):
        rows = [{"nameWithOwner": "octo/old", "description": "", "isPrivate": False, "updatedAt": "2024-01-01T00:00:00Z"},
                {"nameWithOwner": "octo/app", "description": "The web app", "isPrivate": True, "updatedAt": "2026-01-01T00:00:00Z"}]
        with patch("dikdik.git_accounts.shutil.which", return_value="gh"), patch("dikdik.git_accounts._run", return_value=done(json.dumps(rows))):
            repos = git_accounts.repositories("github")
            self.assertEqual([r["name"] for r in repos], ["octo/app", "octo/old"])
            self.assertEqual((repos[0]["url"], repos[0]["ssh_url"], repos[0]["private"]),
                             ("https://github.com/octo/app.git", "git@github.com:octo/app.git", True))
            self.assertEqual([r["name"] for r in git_accounts.repositories("github", "web")], ["octo/app"])
        with patch("dikdik.git_accounts.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "GitHub CLI"):
            git_accounts.repositories("github")
        with self.assertRaises(ValueError):
            git_accounts.repositories("bitbucket")

    def test_sign_in_needs_a_known_provider_and_its_cli(self):
        sign_ins = git_accounts.SignIns()
        with self.assertRaises(ValueError):
            sign_ins.start("bitbucket")
        with patch("dikdik.git_accounts.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "Install"):
            sign_ins.start("github")
        with self.assertRaises(KeyError):
            sign_ins.poll("github")


if __name__ == "__main__":
    unittest.main()
