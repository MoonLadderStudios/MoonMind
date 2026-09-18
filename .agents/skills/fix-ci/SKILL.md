---
name: fix-ci
description: Fix continuous integration (CI) test or build failures for the current PR branch. Inspect CI logs, use targeted reproductions for diagnosis, fix the code, and prefer CI for broader verification of the pushed revision while honoring repository and publication policy.
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

2. Choose a targeted reproduction and the repository-preferred verification path.
- Discover and honor the repository's testing guidance and entrypoints from
  `AGENTS.md`, `CONTRIBUTING.md`, docs, and manifests (`tools/`,
  `package.json`, `pyproject.toml`, `Makefile`, CI workflow files). Do not
  assume a fixed command or use an entire suite when a test path or node ID
  can reproduce the failure.
- Prefer the smallest representative local reproduction when its toolchain
  and services are available. Targeted integration or browser tests count
  when they exercise the failing boundary. Observe the expected failure
  before fixing it; an existing representative CI failure can supply the
  red phase. Missing dependencies are not a reproduced regression.
- Prefer GitHub Actions CI for verification beyond targeted tests under
  MoonMind's testing policy, even when local or managed compute is available.
  Do not require a broad local or managed suite before pushing a coherent
  fix or using CI. Broader local runs remain appropriate when they materially
  help diagnosis, cover an environment CI cannot exercise, or provide a
  practical fallback when CI is unavailable or publication is prohibited.
- For targeted reproduction, check the authorized managed path when local
  prerequisites are missing. An absent local Docker executable does not
  establish that the managed runner is unavailable. Respect the repository's
  managed-runner and isolation rules rather than using Docker sockets,
  nested Docker, or a host-only wrapper inside a managed workflow.
- If a representative targeted reproduction cannot run, record the specific
  limitation and use the observed CI failure and authorized CI path instead.
  Local or managed execution is not a fallback chain that must be exhausted
  before CI. Never push solely to test when publication is prohibited.
  Continue safe authorized work and report any remaining verification gap.

3. Fix the underlying issue.
- Analyze the error output.
- Apply surgical code changes to fix the build error or test failure.
- Ensure the fix doesn't break other existing functionality.

4. Verify targeted behavior and reserve broader verification for CI.
- Re-run the available targeted reproduction and relevant inexpensive static
  checks. If the targeted check still fails, repeat step 3.
- Use step 6 for broader regression, integration, browser, and cross-cutting
  verification. Do not rerun whole CI suites locally just because their
  prerequisites are installed, or make them a pre-push gate.
- If targeted verification is unavailable, record what was not run and why,
  then continue through the authorized CI path. Successful CI can provide
  verification without a duplicate local run, but only for behavior that
  the selected jobs actually exercise. Preserve any required environment-
  specific verification outside CI.
- Do not substitute weaker checks such as `git diff --check` for required
  behavioral verification, weaken assertions, or bypass failing required
  checks. If no representative authorized verification path is available,
  report the concrete blocker without inventing a pass.

5. Commit and push.
- Ensure the working tree is clean except for the fixed files.
- Stage the resolved files with `git add <file>`.
- Commit the changes using a descriptive message (e.g. `fix(ci): <short failure summary>`).
- Push to the current branch with `git push` when publication is authorized.

6. Verify the pushed head in CI.
- Record the exact local `HEAD` SHA after the push.
- Confirm the PR branch on GitHub points at that same SHA. If it does not,
  stop as blocked; do not report success. CI results count only for that
  exact pushed head SHA, never for an earlier or assumed revision.
- Confirm the workflow actually started for that revision. Follow the
  repository's triggers: open or update the PR, or use an authorized manual
  dispatch when needed. A feature-branch push alone may not trigger CI.
  Opening or updating a PR is not a claim that verification has passed.
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
- The targeted local or managed reproduction commands and observed results,
  or what was not run and why. An unexecuted local check is not itself a
  blocker when representative CI verification passed.
- Files changed to fix the issue.
- CI run links, the tested revision, and the actual required-check outcomes.
  Distinguish pending, failed, unavailable, and passing verification.
- Confirmation of passing pushed PR-head CI only when observed for the exact
  fixed commit, plus any verification gaps outside CI.
- Commit hash and pushed branch.
