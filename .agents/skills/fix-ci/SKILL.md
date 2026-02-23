---
name: fix-ci
description: Fix continuous integration (CI) test or build failures for the current PR branch. Fetch CI failure logs, map them to local commands, reproduce the failures, fix the code, verify locally, and commit and push.
---

# Fix CI

Run this workflow to diagnose and fix CI build and test failures on the active branch.

## Inputs
- Optional: specific failing check name or local reproduction command.

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

5. Commit and push.
- Ensure the working tree is clean except for the fixed files.
- Stage the resolved files with `git add <file>`.
- Commit the changes using a descriptive message (e.g. `fix(ci): <short failure summary>`).
- Push to the current branch with `git push`.

## Output

Provide:
- The failing check name and a brief summary of the error.
- The local reproduction command used.
- Files changed to fix the issue.
- Confirmation that the local verification command passed.
- Commit hash and pushed branch.
