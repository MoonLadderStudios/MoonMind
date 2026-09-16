---
name: fix-ci
description: Fix continuous integration (CI) test or build failures for the current PR branch. Fetch CI failure logs, map them to local commands, reproduce the failures, fix the code, verify locally, and commit and push.
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
  required-capabilities:
    - git
    - gh
---

# Fix CI

Run this workflow to diagnose and fix CI build and test failures on the active branch.

## Inputs
- Optional: specific failing check name or local reproduction command.
- Optional: `maxIterations` for post-push CI remediation loops. Default: `3`.

If no inputs are provided, investigate the failing CI checks for the current branch PR.

## Workflow

0. Resolve helpers exclusively from the run's immutable active bundle.

  ```bash
  FIX_CI_SKILL_DIR="${FIX_CI_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/fix-ci}}"
  ACTIVE_SKILLS_DIR="${MOONMIND_ACTIVE_SKILLS_DIR:-$(dirname "$FIX_CI_SKILL_DIR")}"
  test -n "$FIX_CI_SKILL_DIR" && test -f "$FIX_CI_SKILL_DIR/SKILL.md"
  ```

  Inside MoonMind, `MOONMIND_ACTIVE_SKILLS_DIR` is always set and the shared
  helper below resolves from it; a checked-in `.agents/skills` directory must
  never shadow the selected snapshot. Outside MoonMind, set `FIX_CI_SKILL_DIR`
  to the directory containing this `SKILL.md` (no MoonMind-only environment
  variables required). A missing selected helper is a
  materialization/packaging error: stop as blocked instead of substituting
  stale repository code.

1. Identify the failing check.
- If not provided, run `gh pr view --json statusCheckRollup` or `gh run list --branch <current-branch> --json` to find failing checks.
- Fetch the failing logs using `gh run view <run-id> --log` or similar if necessary.

2. Prefer local reproduction when representative, without making it an
absolute prerequisite.
- Discover the repository's test entrypoints from authoritative repo guidance
  (`AGENTS.md`, `CONTRIBUTING.md`, docs) and manifests (`tools/`,
  `package.json`, `pyproject.toml`, `Makefile`, CI workflow files). Do not
  assume a fixed command; map the failing check to the repo's own local
  script or command (e.g. `./tools/test_unit.sh`, `pytest`, `npm test`,
  `poetry run ruff check .`, etc.).
- Discover the authorized managed/CI substrate alongside local options. An
  absent local Docker executable is not proof the shared managed backend is
  unavailable; check the authorized managed path before declaring local
  reproduction impossible.
- When a representative local command exists and its toolchain/service is
  available, run it and ensure it fails in the same way as CI before fixing.
- When no representative local path exists or its prerequisite is
  unavailable, record the missing prerequisite and continue through the
  authorized managed/CI path instead of blocking on local
  reproduction. When the local prerequisite is unavailable but the
  authorized managed substrate is available, execute the discovered
  managed runner first for the touched scope; use pushed-head CI (step 6)
  only when the managed substrate is also unavailable.
  Never push solely to test when the workflow forbids
  publication; in that case stop as blocked.

3. Fix the underlying issue.
- Analyze the error output.
- Apply surgical code changes to fix the build error or test failure.
- Ensure the fix doesn't break other existing functionality.

4. Verify the fix through the authorized substrate.
- Re-run the representative local reproduction command until it passes
  successfully.
- If the command still fails, repeat step 3.
- If the required local toolchain or service is unavailable, do not treat
  weaker checks such as `git diff --check` as sufficient verification. Record
  the missing prerequisite and default to the managed runner first, then
  exact pushed-head CI verification (step 6), unless the task explicitly
  forbids pushing; otherwise stop as blocked. An absent local Docker
  executable alone never proves the shared managed backend is unavailable.

5. Commit and push.
- Ensure the working tree is clean except for the fixed files.
- Stage the resolved files with `git add <file>`.
- Commit the changes using a descriptive message (e.g. `fix(ci): <short failure summary>`).
- Push to the current branch with `git push`.

6. Verify the pushed head in CI.
- Record the exact local `HEAD` SHA after the push.
- Confirm the PR branch on GitHub points at that same SHA. If it does not,
  stop as blocked; do not report success. CI results count only for that
  exact pushed head SHA, never for an earlier or assumed revision.
- Never push solely to test when the workflow forbids publication. Missing
  required validation is never replaced by `git diff --check`.
- Write `artifacts/publish_result.json` through the shared helper after every
  pushed or verified no-op outcome:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-pushed \
    --skill-id fix-ci \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
  If no commit was needed, use:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-no-op \
    --skill-id fix-ci \
    --repo "$REPO" \
    --branch "$BRANCH"
  ```
- Wait for required PR checks on that SHA to finish. Poll with bounded backoff.
- If checks pass, finish successfully.
- If checks fail, fetch the new failing logs and repeat steps 2-6, up to
  `maxIterations`.
- If checks remain queued/running beyond the wait cap, GitHub is unavailable,
  or the task explicitly forbids pushing, write blocked evidence and stop with
  the current SHA, check state, and next action:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" write-blocked \
    --skill-id fix-ci \
    --repo "$REPO" \
    --branch "$BRANCH" \
    --reason publish_unavailable
  ```
  Do not report success while CI is running, degraded, unknown, or failing.
- Before repeating a push after an uncertain outcome (timeout, dropped
  connection, ambiguous tool output), reconcile first: re-read the remote
  branch head and PR state and continue from the reconciled state instead of
  pushing again blindly. Auxiliary output failures never erase a verified
  primary effect and never cause another push on their own.
- Required check failures remain failures: a clean local run, a queued
  check, or an unrelated green run never overrides a failing required check
  on the exact pushed head.

## Output

Provide:
- The failing check name and a brief summary of the error.
- The local reproduction command used.
- Files changed to fix the issue.
- Confirmation that local verification passed, or a clear blocked reason if it
  could not run.
- Confirmation that the pushed PR-head CI passed for the exact fixed commit.
- Commit hash and pushed branch.
