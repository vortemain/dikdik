---
name: git-workflows
description: Manage focused changes, review diffs and resolve conflicts while preserving user work.
category: engineering
version: "1.0"
triggers: "git, worktree, merge conflict, pull request, branch"
---

# Git Workflows

1. Inspect status, current branch and repository instructions before changing history or files. Identify pre-existing edits and preserve them throughout the task.

2. Keep the diff focused and review staged/untracked/generated files. Do not include credentials, unrelated edits or build output in a commit.

3. Resolve conflicts using both sides' intended behavior and current callers, then run affected checks. A conflict marker disappearing does not establish a correct merge.

4. Use the requested branch and review workflow. Pushes, history rewrites and destructive cleanup require the relevant authorization; a local task does not grant publication rights.
