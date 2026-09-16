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

0. Resolve helpers exclusively from the run's immutable active bundle.
- Establish the portable paths before running any helper:
```bash
FIX_MERGE_CONFLICTS_SKILL_DIR="${FIX_MERGE_CONFLICTS_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/fix-merge-conflicts}}"
ACTIVE_SKILLS_DIR="${MOONMIND_ACTIVE_SKILLS_DIR:-$(dirname "$FIX_MERGE_CONFLICTS_SKILL_DIR")}"
test -n "$FIX_MERGE_CONFLICTS_SKILL_DIR" && test -f "$FIX_MERGE_CONFLICTS_SKILL_DIR/SKILL.md"
```
- Inside MoonMind, `MOONMIND_ACTIVE_SKILLS_DIR` is always set and the shared
  helper below resolves from it; a checked-in `.agents/skills` directory must
  never shadow the selected snapshot. Outside MoonMind, set
  `FIX_MERGE_CONFLICTS_SKILL_DIR` to the directory containing this `SKILL.md`
  (no MoonMind-only environment variables required). A missing selected
  helper is a materialization/packaging error: stop as blocked instead of
  substituting stale repository code.

1. Resolve the merge target from authoritative sources before any mutation:
- `inputs.base` (required) is the only task-supplied input: the PR base
  branch name (for example `main` or `release/2.x`). The merge target is
  always `origin/<base>`. If the base is missing or empty, stop as blocked
  with reason `base_unavailable`; never silently substitute a default branch
  for a PR that targets another base branch. This non-merging skill takes no
  finish mode.
- Confirm the exact repository, head branch, and base branch against the
  authoritative PR itself (`gh pr view --json number,headRefName,baseRefName,headRepository`)
  before mutating. If `inputs.base` disagrees with the PR's base, or the
  base ref changed since task start, stop as blocked instead of merging a
  changed/wrong base.
- Preserve source authority: if the PR head lives in a fork or another
  combination the authorized boundary cannot safely mutate, stop before
  mutation as blocked with reason `unsupported_source` instead of pushing
  across authority boundaries.
- Git identity is required only when Git is about to create a commit. Do
  not block fetching or attempting the merge on missing identity: read only
  the already-configured identity (`git config --get user.name`
  and `git config --get user.email`, honoring repository-local configuration
  first) just before committing, and revalidate both values before each
  write. Do not invent, default, or export a fallback author/email and do
  not write identity from unvalidated inputs or environment defaults.
- If identity is missing when a commit is actually needed, stop as blocked
  without committing or pushing: report the missing identity and the
  supported setup (`git config user.name "<name>"` /
  `git config user.email "<email>"`) so the operator can provide authorized
  configuration. A clean, fast-forward, or already-up-to-date merge that
  creates no commit needs no identity and must not be blocked by this check.

2. Sync remote refs for the PR base branch.
- Run `git fetch origin <base> --prune`.
- Confirm branch state with `git status`. Preserve pre-existing staged,
  unstaged, and untracked work, local branch state, and configured Git
  identity; take no mutation that would discard or absorb unrelated changes.

3. Merge the latest PR base ref into the current branch.
- When the working tree is not clean, run
  `git merge --autostash origin/<base>` so pre-existing staged/unstaged
  work is stashed and restored automatically. If the merge still refuses,
  or `--autostash` is unsupported, perform the merge in an isolated
  worktree with bounded restoration instead of stalling.
- Otherwise run `git merge origin/<base>`.
- If merge completes cleanly, continue to step 4.
- If git reports conflicts, continue to step 3.

4. Resolve conflicts in each unmerged file.
- List conflicted files with `git diff --name-only --diff-filter=U`.
- Remove `<<<<<<<`, `=======`, and `>>>>>>>` blocks.
- Keep the correct merged content.
- Preserve project conventions and existing architecture.
- Resolve conflicts semantically: the merged content must preserve the
  intended behavior of both sides, not merely contain no marker strings.
- Stage only intentional task files with explicit paths
  (`git add <intentional-file> [...]`). Never use `git add -A`, `git add .`,
  or any unconditional/broad staging, and never stage or commit unrelated
  pre-existing staged, unstaged, or untracked files.
- Complete the merge commit with `git commit` (or `git commit -m "Merge origin/<base> and resolve conflicts"`).

5. Validate resolution completeness.
- Confirm `git diff --name-only --diff-filter=U` returns nothing.
- Confirm no conflict markers remain with:
  - `rg '^(<<<<<<<|=======|>>>>>>>)'`
- Validate the resulting behavior, not just the absence of markers: review
  the semantic merge result and run the targeted checks from step 6. A
  marker-free tree with broken or regressed behavior is not resolved.

6. Run quick verification.
- Run targeted checks or tests that are reasonable for the changed files.
- If checks cannot run locally, record that clearly.

7. Commit and push only intentional task changes.
- If the merge was a fast-forward, no merge commit is created. Do not commit
  unrelated local changes: leave pre-existing staged, unstaged, and untracked
  files unmodified and uncommitted in clean-merge, conflict, fast-forward,
  and no-op paths, and do not alter local branch state beyond the task's own
  commits.
- Push current branch: `git push`
- After push, record local `HEAD` with `git rev-parse HEAD` and verify the exact same SHA is visible on the remote branch with `git ls-remote origin refs/heads/<current-branch>` or an equivalent trusted GitHub path.
- If there was nothing to commit, still verify the current local `HEAD` exactly matches the remote branch head.
- Before repeating a push/merge whose outcome is uncertain (timeout,
  dropped connection, ambiguous tool output), reconcile first: re-read the
  remote branch head and PR state and continue from the reconciled state
  instead of pushing or merging again blindly.
- Auxiliary output failures (for example evidence-writer errors) never erase
  a verified primary effect and never cause another push/merge on their own.
- On successful push, write canonical evidence:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-pushed \
    --skill-id fix-merge-conflicts \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
- On verified no-op, write canonical no-op evidence:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-no-op \
    --skill-id fix-merge-conflicts \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
- If push or remote verification is unavailable, write blocked evidence, then stop as blocked with reason `publish_unavailable`:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-blocked \
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
