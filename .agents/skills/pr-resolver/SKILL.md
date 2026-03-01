---
name: pr-resolver
description: Master orchestrator to resolve a PR by diagnosing state and delegating to specialized skills.
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. You diagnose the PR state using a snapshot script and resolve issues by reading and executing the instructions of specialized sub-skills (`fix-merge-conflicts`, `fix-ci`, etc.).

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 3)
- inputs.failFastIfCiRunning (default true)

## Workflow
1. Run `python3 .agents/skills/pr-resolver/bin/pr_resolve_snapshot.py` to generate `artifacts/pr_resolver_snapshot.json`.
2. Inspect the snapshot output.
3. Apply fixes in this strict priority order:
   - **Merge Conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) or `mergeStateStatus` is exactly `DIRTY`, you MUST read `.agents/skills/fix-merge-conflicts/SKILL.md`. Follow its procedure exactly to resolve the conflict before attempting CI fixes or waiting for CI.
   - **Review / Feedback Comments:** If any of the following indicate unresolved review feedback, you MUST read `.agents/skills/fix-comments/SKILL.md` and follow its procedure before merge:
     - `reviewDecision` indicates changes requested.
     - `commentsSummary.hasActionableComments` is true.
     - `commentsFetch.succeeded` is false (comment signal unavailable).
    Proceed with comment fixes even if CI is still running.
    Actionability is intentionally broad: bot and human comments are both included by default. Only empty comments and explicitly resolved/outdated review threads are treated as non-actionable.
   - **CI Failures:** If `ci.hasFailures` is true, you MUST read `.agents/skills/fix-ci/SKILL.md` (or similar available skill) and follow its procedure to fix the tests/build.
   - **CI Signal Integrity:** If `ci.signalQuality` is not `"ok"` (for example missing required checks or missing non-security checks on head commit), treat this as blocking CI and do not merge until fixed.
   - **CI Still Running:** If `ci.isRunning` is true, do not merge yet. Exit blocked and wait for CI completion.
   - **Merge:** If all green, `mergeable` is clean, `mergeStateStatus` is `"CLEAN"`, and NO CI is running, execute `gh pr merge --<mergeMethod>`.
   - **Blocked:** If CI is running but comments are still actionable or mergeability is conflicting, exit and state why it is blocked.
4. After applying ANY fix (conflict, review comments, CI), you MUST loop back to Step 1 and re-run the snapshot. Stop after `maxIterations`.
5. For the final merge decision, you MUST run `python3 .agents/skills/pr-resolver/bin/pr_resolve_finalize.py --merge-method <merge|squash|rebase>` and follow its decision. Do NOT call `gh pr merge` directly.
6. Write `artifacts/pr_resolver_result.json` summarizing the actions taken and the final merge outcome.

## Lightweight Finalize Pass

When a prior `pr-resolver` run already fixed conflicts/comments/CI and is only waiting on CI, use:

```bash
python3 .agents/skills/pr-resolver/bin/pr_resolve_finalize.py --merge-method <merge|squash|rebase>
```

This performs only:
- snapshot refresh
- comment/mergeability/CI gate evaluation
- either direct merge (`gh pr merge --<mergeMethod>`) or blocked exit

Use this command for check-back runs to avoid rerunning the full fix workflow.

## Constraints
- Do NOT try to invent your own conflict resolution or CI fixing workflow. Always load and follow the specialized sub-skill instructions.
- This skill is allowed to commit/push and merge (task.publish.mode MUST be none).
