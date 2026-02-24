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
   - **Merge Conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) **or** `mergeStateStatus` is not `"CLEAN"`, you MUST read `.agents/skills/fix-merge-conflicts/SKILL.md`. Follow its procedure exactly to resolve the conflict.
   - **CI Failures:** If `ci.hasFailures` is true, you MUST read `.agents/skills/fix-ci/SKILL.md` (or similar available skill) and follow its procedure to fix the tests/build.
   - **Review Comments:** If `reviewDecision` indicates changes requested, read `.agents/skills/fix-comments/SKILL.md` and follow its procedure.
   - **Merge:** If all green, `mergeable` is clean, `mergeStateStatus` is `"CLEAN"`, and NO CI is running, execute `gh pr merge --<mergeMethod>`.
   - **Blocked:** If CI is running but no failures and `mergeable` is clean with `mergeStateStatus` `"CLEAN"`, exit and state the PR is blocked waiting for CI.
4. After applying ANY fix (conflict, CI, or review), you MUST loop back to Step 1 and re-run the snapshot. Stop after `maxIterations`.
5. Write `artifacts/pr_resolver_result.json` summarizing the actions taken and the final merge outcome.

## Constraints
- Do NOT try to invent your own conflict resolution or CI fixing workflow. Always load and follow the specialized sub-skill instructions.
- This skill is allowed to commit/push and merge (task.publish.mode MUST be none).
