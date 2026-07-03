---
name: pr-resolver
description: Master orchestrator to resolve a PR by diagnosing state and delegating to specialized skills.
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
  required-skills: "fix-comments fix-ci fix-merge-conflicts"
  required-capabilities:
    - git
    - gh
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. Use bounded retries to keep finalize resilient, and delegate actual fixes to specialized skills (`fix-merge-conflicts`, `fix-comments`, `fix-ci`) when blockers are actionable.

The task is not complete until the target PR is merged or is proven already merged. A local fix, local commit, passing local test run, unresolved review reply, or unpushed branch is not a successful PR resolution.

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 3, full remediation cap per cycle)
- inputs.finalizeMaxRetries (default 60)
- inputs.finalizeBackoffSeconds (default 30)
- inputs.finalizeMaxSleepSeconds (default 120)
- inputs.finalizeMaxElapsedSeconds (default 7200)
- `ci_running` finalize waits use at least 60 seconds before the next check,
  even when `finalizeBackoffSeconds` is lower.

## Primary Command (mandatory first action)
Run the orchestration entrypoint before making manual changes. **You MUST provide the `--pr` argument** (using either the PR number or the branch name) to ensure the script targets the correct PR, even if you are on a different branch or detached HEAD:

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_orchestrate.py \
  --pr <pr_number_or_branch> \
  --merge-method <merge|squash|rebase> \
  --fix-max-iterations <maxIterations> \
  --finalize-max-retries <finalizeMaxRetries> \
  --base-sleep-seconds <finalizeBackoffSeconds> \
  --max-sleep-seconds <finalizeMaxSleepSeconds> \
  --max-elapsed-seconds <finalizeMaxElapsedSeconds>
```

This writes:
- `var/pr_resolver/result.json` (terminal orchestration summary)
- `var/pr_resolver/attempts/*.json` (per-attempt finalize/full artifacts)

If this command does not run, does not write `var/pr_resolver/result.json`, or exits before producing a parseable result, stop as blocked with the command output and do not report success.

## Foreground Execution Contract (no backgrounding)
This skill runs inside a one-shot managed agent run. When your process exits, the
run is over — there is no event loop that will "notify you on completion" and no
scheduled wakeup that will resume you later. Those are interactive-harness concepts
that do not exist here.

Therefore:
- You MUST run the orchestration command in the **foreground** and block on it until
  it returns. Do NOT launch it with `&`, `nohup`, a background job, a detached
  process, or any "run in background and notify me" mechanism.
- The orchestration command already polls long-running states (especially
  `ci_running`) internally up to its elapsed budget (`--max-elapsed-seconds`,
  default 7200s) and finalizes the merge when CI passes. Let it run to completion;
  do not exit while it is still polling.
- Do NOT end your turn expecting a callback, notification, or fallback wakeup to
  finish the merge for you. If you exit while CI is still running, the merge is
  abandoned and the PR is left unresolved even though your local work succeeded.
- A run that stops at `ci_running` (or any other non-terminal state) because you
  backgrounded the orchestrator or returned early is **not** a successful PR
  resolution.

## Terminal Success Contract
Allowed successful terminal states:
- `var/pr_resolver/result.json` has `status=merged`, `merge_outcome=merged`, and `mergeAutomationDisposition=merged`.
- The PR is independently confirmed as already merged after a snapshot/finalize race, with `mergeAutomationDisposition=already_merged`.

## Merge Automation Result Contract
Every terminal `var/pr_resolver/result.json` MUST include `mergeAutomationDisposition`:
- `merged`: the resolver merged the PR.
- `already_merged`: the PR was independently confirmed as already merged.
- `reenter_gate`: the resolver pushed changes and merge automation must wait for readiness on the new head.
- `manual_review`: the resolver stopped on a blocker, exhausted attempts, or needs human follow-up.
- `failed`: the resolver hit a hard execution failure.

Everything else is blocked, failed, or still in-progress. In particular, never finish with `task complete` or a success summary when:
- the PR remains open,
- the branch is ahead of origin,
- a push failed,
- GitHub auth is unavailable,
- `gh pr merge` failed,
- review comments remain actionable,
- CI is running, degraded, or failing,
- mergeability is unknown, dirty, or blocked.

After any local commit-producing remediation, verify the exact current `HEAD` is visible on the remote PR branch before continuing. If `git push`, `gh`, or any GitHub connector path cannot publish the commit, stop as blocked with reason `publish_unavailable`; do not proceed to finalize and do not report success.

Never print raw environment variables while diagnosing GitHub auth or publish failures. Use targeted checks such as `test -n "$GITHUB_TOKEN"` or trusted-tool health calls; do not run `printenv`, `env`, `set`, or equivalent commands that can expose secrets.

When a delegated remediation step cannot publish, overwrite `var/pr_resolver/result.json` before stopping so parent workflows do not report stale gate state. Use `status=blocked`, `merge_outcome=blocked`, `mergeAutomationDisposition=manual_review`, `reason=publish_unavailable`, `final_reason=publish_unavailable`, and `next_step=manual_review`.

## Main Loop
Repeat this state machine until a terminal success or manual blocker:

1. Run the Primary Command.
2. Read `var/pr_resolver/result.json`.
3. If `status=merged` and `merge_outcome=merged`, confirm the PR is merged and finish.
4. If `status=blocked` or `status=attempts_exhausted`, inspect `next_step`.
5. If the same blocker repeats after its specialized skill ran and no remote PR branch change is visible, stop as blocked and report the artifact details.
6. Execute the matching specialized skill exactly once for that blocker, then return to step 1.
7. If the blocker is transient CI or mergeability state, wait only within the configured retry/backoff caps, then return to step 1.

## Retry Policy
- Full-remediation escalation reasons:
  - `actionable_comments`
  - `ci_failures`
  - `merge_conflicts`
- Finalize-only retry reasons:
  - `ci_running`
  - `comments_unavailable`
  - `ci_signal_degraded`
  - `merge_not_ready` (limited grace retries)
- `ci_running` should be allowed to consume the long finalize elapsed budget so
  queued or running checks can reach a terminal state; if they then become
  `ci_failures`, continue into `run_fix_ci_skill` instead of stopping early.
- `ci_running` should not poll faster than once per minute.
- If retries transition from transient CI states into actionable `ci_failures`, continue into `run_fix_ci_skill` instead of stopping at manual review.
- If `merge_not_ready` resolves into an actionable blocker such as `merge_conflicts`, continue into the matching remediation skill instead of stopping at manual review.
- Non-retryable stop reasons:
  - `comment_policy_not_enforced`
  - any unknown blocker

## Manual Remediation Loop (only when needed)
When orchestration returns `status=blocked` or `status=attempts_exhausted`, inspect `next_step` in `var/pr_resolver/result.json`:

- `run_fix_merge_conflicts_skill`: read `.agents/skills/fix-merge-conflicts/SKILL.md` and execute it.
- `run_fix_comments_skill`: read `.agents/skills/fix-comments/SKILL.md` and execute it.
- `run_fix_ci_skill`: read `.agents/skills/fix-ci/SKILL.md` and execute it.
- `wait_for_ci_and_retry_finalize` / `retry_finalize_after_backoff`: do not mutate; wait and re-run orchestration.
- `manual_review`: stop and report the blocker with artifact details.

After applying any fix, run the orchestration command again. Do not summarize the task as complete before the rerun returns the Terminal Success Contract.

## Lightweight Commands
- Finalize-only gate checker:

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_finalize.py --pr <pr_number_or_branch> --merge-method <merge|squash|rebase>
```

- Full gate classifier (no merge, deterministic state classification):

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_full.py --pr <pr_number_or_branch> --merge-method <merge|squash|rebase> --max-iterations <maxIterations>
```

## Constraints
- Keep `pr_resolve_finalize.py` as a gate checker; do not add remediation mutations there.
- Do NOT invent custom conflict/CI/comment workflows; always execute the specialized skill instructions.
- Respect retry caps; if retries are exhausted, return `attempts_exhausted` and stop.
- This skill owns publishing under `task.publish.mode = "auto"` and may commit, push, or merge only as required to resolve the target PR. Before reporting success, write `artifacts/publish_result.json` with auto publish evidence proving the verified outcome.
- A failed push, missing GitHub auth, or missing remote branch update is an unresolved PR blocker, even if all code changes are committed locally.
- Never background the orchestration command or rely on notifications/scheduled wakeups to finish the merge; run it in the foreground and wait for it to return (see Foreground Execution Contract).
