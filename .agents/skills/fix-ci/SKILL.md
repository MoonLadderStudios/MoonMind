---
name: fix-ci
description: Fix continuous integration (CI) test or build failures for the current PR branch. Fetch CI failure logs, map them to local commands, reproduce the failures, fix the code, verify locally, and commit and push.
---

# Fix CI

Run this workflow to diagnose and fix CI build and test failures on the active branch.

## Inputs
- Optional: specific failing check name or local reproduction command.
- Optional: `maxIterations` for post-push CI remediation loops. Default: `3`.

If no inputs are provided, investigate the failing CI checks for the current branch PR.

## Workflow

1. Identify the failing check.
- If not provided, run `gh pr view --json statusCheckRollup` or `gh run list --branch <current-branch> --json` to find failing checks.
- Fetch the failing logs using `gh run view <run-id> --log` or similar if necessary.

2. Reproduce the failure locally.
- Map the failing check to a local script or command (e.g. `./tools/test_unit.sh`, `pytest`, `npm test`, `poetry run ruff check .`, etc.).
- Run the local command and ensure it fails in the same way as CI.

3. Fix the underlying issue.
- Analyze the error output.
- Apply surgical code changes to fix the build error or test failure.
- Ensure the fix doesn't break other existing functionality.

4. Verify the fix locally.
- Re-run the local reproduction command until it passes successfully.
- If the command still fails, repeat step 3.
- If the required local toolchain or service is unavailable, do not treat
  weaker checks such as `git diff --check` as sufficient verification. Record
  the missing prerequisite and default to post-push CI verification unless the
  task explicitly forbids pushing; otherwise stop as blocked.

5. Commit and push.
- Ensure the working tree is clean except for the fixed files.
- Stage the resolved files with `git add <file>`.
- Commit the changes using a descriptive message (e.g. `fix(ci): <short failure summary>`).
- Push to the current branch with `git push`.

6. Verify the pushed head in CI.
- Record the exact local `HEAD` SHA after the push.
- Confirm the PR branch on GitHub points at that same SHA. If it does not,
  stop as blocked; do not report success.
- Wait for required PR checks on that SHA to finish. Poll with bounded backoff.
- If checks pass, finish successfully.
- If checks fail, fetch the new failing logs and repeat steps 2-6, up to
  `maxIterations`.
- If checks remain queued/running beyond the wait cap, GitHub is unavailable,
  or the task explicitly forbids pushing, stop as blocked with the current SHA,
  check state, and next action. Do not report success while CI is running,
  degraded, unknown, or failing.

## Output

Provide:
- The failing check name and a brief summary of the error.
- The local reproduction command used.
- Files changed to fix the issue.
- Confirmation that local verification passed, or a clear blocked reason if it
  could not run.
- Confirmation that the pushed PR-head CI passed for the exact fixed commit.
- Commit hash and pushed branch.
