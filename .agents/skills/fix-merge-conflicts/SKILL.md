---
name: fix-merge-conflicts
description: Sync the branch with the latest PR base branch from `origin`, merge the PR base ref, resolve conflicts end-to-end, then commit and push the current branch.
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
  required-capabilities:
    - git
---

# Fix Merge Conflicts

Run this as an end-to-end sync and conflict resolution workflow:
1. Fetch latest PR base branch from `origin`.
2. Merge the PR base ref into the current branch.
3. Resolve any conflicts.
4. Validate no conflict markers remain.
5. Commit and push the current branch.

## Inputs

- `inputs.base` (required): the PR base branch name (for example `main` or
  `release/2.x`). The merge target is always `origin/<base>`.
- If the base branch is missing or empty, stop as blocked with reason
  `base_unavailable`. Never silently substitute a default branch for a PR
  that targets another base branch.

## Default Prompt

```text
Fetch the latest PR base branch from origin, merge the PR base ref into the current branch, resolve all merge conflicts, verify no conflict markers remain, then commit and push.
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

1. Sync remote refs for the PR base branch.
- Resolve the base branch name from `inputs.base` (required). If it is
  missing or empty, stop as blocked with reason `base_unavailable`.
- Run `git fetch origin <base> --prune`.
- Confirm branch state with `git status`.

2. Merge the latest PR base ref into the current branch.
- Run `git merge origin/<base>`.
- If merge completes cleanly, continue to step 4.
- If git reports conflicts, continue to step 3.

3. Resolve conflicts in each unmerged file.
- List conflicted files with `git diff --name-only --diff-filter=U`.
- Remove `<<<<<<<`, `=======`, and `>>>>>>>` blocks.
- Keep the correct merged content.
- Preserve project conventions and existing architecture.
- Stage resolved files with `git add <file>` or `git add -A`.
- Complete the merge commit with `git commit` (or `git commit -m "Merge origin/<base> and resolve conflicts"`).

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
- After push, record local `HEAD` with `git rev-parse HEAD` and verify the exact same SHA is visible on the remote branch with `git ls-remote origin refs/heads/<current-branch>` or an equivalent trusted GitHub path.
- If there was nothing to commit, still verify the current local `HEAD` exactly matches the remote branch head.
- On successful push, write canonical evidence:
  ```bash
  python3 "${MOONMIND_ACTIVE_SKILLS_DIR:-.agents/skills}/_shared/publish_evidence.py" write-pushed \
    --skill-id fix-merge-conflicts \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
- On verified no-op, write canonical no-op evidence:
  ```bash
  python3 "${MOONMIND_ACTIVE_SKILLS_DIR:-.agents/skills}/_shared/publish_evidence.py" write-no-op \
    --skill-id fix-merge-conflicts \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
- If push or remote verification is unavailable, write blocked evidence, then stop as blocked with reason `publish_unavailable`:
  ```bash
  python3 "${MOONMIND_ACTIVE_SKILLS_DIR:-.agents/skills}/_shared/publish_evidence.py" write-blocked \
    --skill-id fix-merge-conflicts \
    --repo "$REPO" \
    --branch "$BRANCH" \
    --reason publish_unavailable
  ```
  Do not report success.

## Output

Provide:
- Resolved file list.
- Whether the merge of the PR base ref was clean, fast-forward, or conflicting.
- Verification performed (or what was skipped).
- Commit hash or verified no-op `HEAD` hash, plus pushed/verified branch.
