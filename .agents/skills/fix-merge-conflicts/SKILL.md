---
name: fix-merge-conflicts
description: Sync the branch with the latest `origin/main`, merge `origin/main`, resolve conflicts end-to-end, then commit and push the current branch.
---

# Fix Merge Conflicts

Run this as an end-to-end sync and conflict resolution workflow:
1. Fetch latest `main` from `origin`.
2. Merge `origin/main` into the current branch.
3. Resolve any conflicts.
4. Validate no conflict markers remain.
5. Commit and push the current branch.

## Default Prompt

```text
Fetch the latest main branch from origin, merge origin/main into the current branch, resolve all merge conflicts, verify no conflict markers remain, then commit and push.
```

## Workflow

0. Ensure git identity is available locally before merge/commit:
- Resolve required identity from env (repository-local > env-provided) and write it into local git config if missing.
```bash
git_local_name="$(git config --local --get user.name || true)"
git_local_email="$(git config --local --get user.email || true)"
git_env_name="${GIT_AUTHOR_NAME:-${GIT_COMMITTER_NAME:-${MOONMIND_GIT_USER_NAME:-MoonMind}}}"
git_env_email="${GIT_AUTHOR_EMAIL:-${GIT_COMMITTER_EMAIL:-${MOONMIND_GIT_USER_EMAIL:-noreply@moonmind.local}}}"

if [ -z "$git_local_name" ] || [ -z "$git_local_email" ]; then
  if [ -z "$git_env_name" ] || [ -z "$git_env_email" ]; then
    echo "git user.name and user.email are required for merge/commit; set them in environment or local config."
    exit 1
  fi
  git config user.name "$git_env_name"
  git config user.email "$git_env_email"
fi
```

1. Sync remote refs for `main`.
- Run `git fetch origin main --prune`.
- Confirm branch state with `git status`.

2. Merge latest `main` into the current branch.
- Run `git merge origin/main`.
- If merge completes cleanly, continue to step 4.
- If git reports conflicts, continue to step 3.

3. Resolve conflicts in each unmerged file.
- List conflicted files with `git diff --name-only --diff-filter=U`.
- Remove `<<<<<<<`, `=======`, and `>>>>>>>` blocks.
- Keep the correct merged content.
- Preserve project conventions and existing architecture.
- Stage resolved files with `git add <file>` or `git add -A`.
- Complete the merge commit with `git commit` (or `git commit -m "Merge origin/main and resolve conflicts"`).

4. Validate resolution completeness.
- Confirm `git diff --name-only --diff-filter=U` returns nothing.
- Confirm no conflict markers remain with:
  - `rg '^(<<<<<<<|=======|>>>>>>>)'`

5. Run quick verification.
- Run targeted checks or tests that are reasonable for the changed files.
- If checks cannot run locally, record that clearly.

6. Commit and push.
- If the merge was a fast-forward, no merge commit is created. Commit any other local changes before pushing.
- Push current branch: `git push`

## Output

Provide:
- Resolved file list.
- Whether `git merge origin/main` was clean, fast-forward, or conflicting.
- Verification performed (or what was skipped).
- Commit hash and pushed branch.
