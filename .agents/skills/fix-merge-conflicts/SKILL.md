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
inputSchema:
  type: object
  required:
    - base
  properties:
    base:
      type: string
      title: Base branch
      description: >-
        PR base branch name (for example `main` or `release/2.x`). The merge
        target is always `origin/<base>`. Declared here so schema-driven
        callers (including the Create page catalog entry, which has no other
        base field) render a base input and the runtime supplies
        `inputs.base`; standalone invocations without a base stop as
        `base_unavailable` instead of substituting a default branch.
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
Fetch the latest PR base branch from origin for `inputs.base`, merge `origin/<base>` into the current branch, resolve all merge conflicts, verify no conflict markers remain, then commit and push. If `inputs.base` is missing or empty, stop as blocked with reason `base_unavailable`.
```

## Workflow

0. Ensure git identity is available locally before merge/commit:
- Use only the repository's existing authorized identity configuration (repository-local config or environment-provided identity for this run).
- Resolve the effective identity first so normal inherited configuration counts: `git config --get user.name` and `git config --get user.email` already cover repository-local, global, and system scopes (verify origin with `git config --show-origin --get user.name` when in doubt). Only when the effective values are missing, fall back to environment-provided identity for this run.
- If no usable `user.name`/`user.email` is configured, stop as blocked with reason `git_identity_unavailable`; do not invent an author/email to bypass the block.
```bash
git_effective_name="$(git config --get user.name || true)"
git_effective_email="$(git config --get user.email || true)"
git_env_name="${GIT_AUTHOR_NAME:-${GIT_COMMITTER_NAME:-${MOONMIND_GIT_USER_NAME:-}}}"
git_env_email="${GIT_AUTHOR_EMAIL:-${GIT_COMMITTER_EMAIL:-${MOONMIND_GIT_USER_EMAIL:-}}}"

if [ -z "$git_effective_name" ] || [ -z "$git_effective_email" ]; then
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

2. Isolate pre-existing staged changes before merging.
- A `git merge` can leave unrelated staged entries in the index, and the bare
  `git commit` that completes a conflicting merge commits the entire index —
  so unrelated work staged before this run would be included and pushed.
  Snapshot and remove unrelated index entries before merging, then restore
  them after the merge commit; block without publishing if they cannot be
  isolated or restored.
```bash
git diff --cached --name-only > /tmp/fix-merge-conflicts-staged-before.txt
if [ -s /tmp/fix-merge-conflicts-staged-before.txt ]; then
  git stash push --staged -m "fix-merge-conflicts: isolate pre-existing staged changes"
fi
git diff --cached --quiet || { echo "index is not clean; refusing to merge."; exit 1; }
```
- After the merge commit in step 4, restore the isolated entries:
```bash
if git stash list | grep -q "fix-merge-conflicts: isolate pre-existing staged changes"; then
  git stash pop || { echo "staged changes cannot be restored cleanly; do not push."; exit 1; }
fi
```
- If the stash pop conflicts with the merge result, stop as blocked with
  reason `staged_changes_not_restorable` without pushing: never force-restore
  over the merge commit and never push a branch whose unrelated entries are
  unaccounted for.

3. Merge the latest PR base ref into the current branch.
- Run `git merge origin/<base>`.
- If merge completes cleanly, continue to step 5.
- If git reports conflicts, continue to step 4.

4. Resolve conflicts in each unmerged file.
- List conflicted files with `git diff --name-only --diff-filter=U`.
- Remove `<<<<<<<`, `=======`, and `>>>>>>>` blocks.
- Keep the correct merged content.
- Preserve project conventions and existing architecture.
- Stage only resolved files with `git add <file>`. Never use `git add -A` or stage unrelated changes.
- Validate the merged content semantically for the touched behavior, not only by absence of marker strings.
- Complete the merge commit with `git commit` (or `git commit -m "Merge origin/<base> and resolve conflicts"`).

4. Validate resolution completeness.
- Confirm `git diff --name-only --diff-filter=U` returns nothing.
- Confirm no conflict markers remain with:
  - `rg '^(<<<<<<<|=======|>>>>>>>)'`

5. Run quick verification.
- Run targeted checks or tests that are reasonable for the changed files.
- If checks cannot run locally, record that clearly.

6. Commit and push.
- If the merge was a fast-forward, no merge commit is created. Commit only intentional task changes (the merge result and conflict resolutions); preserve pre-existing unrelated staged, unstaged, and untracked work without committing it. The staged entries isolated in step 2 stay out of the merge commit and are restored only after it exists.
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
