---
name: pr-resolver
description: Master orchestrator to resolve a PR by diagnosing state and delegating to specialized skills.
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. Use bounded retries to keep finalize resilient, and delegate actual fixes to specialized skills (`fix-merge-conflicts`, `fix-comments`, `fix-ci`) when blockers are actionable.

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 3, full remediation cap per cycle)
- inputs.finalizeMaxRetries (default 2)
- inputs.finalizeBackoffSeconds (default 15)
- inputs.finalizeMaxSleepSeconds (default 60)
- inputs.finalizeMaxElapsedSeconds (default 900)

## Primary Command (use first)
Run the orchestration entrypoint:

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_orchestrate.py \
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

After applying any fix, run the orchestration command again.

## Lightweight Commands
- Finalize-only gate checker:

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_finalize.py --merge-method <merge|squash|rebase>
```

- Full gate classifier (no merge, deterministic state classification):

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_full.py --merge-method <merge|squash|rebase> --max-iterations <maxIterations>
```

## Constraints
- Keep `pr_resolve_finalize.py` as a gate checker; do not add remediation mutations there.
- Do NOT invent custom conflict/CI/comment workflows; always execute the specialized skill instructions.
- Respect retry caps; if retries are exhausted, return `attempts_exhausted` and stop.
- This skill is allowed to commit/push and merge (task.publish.mode MUST be none).
