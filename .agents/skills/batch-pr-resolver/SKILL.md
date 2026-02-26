---
name: batch-pr-resolver
description: Discover open PRs in a repository and enqueue one `pr-resolver` task for each.
---

# Batch PR Resolver Skill

## Purpose

Create one queue task per open pull request so each PR branch can be resolved by `pr-resolver` on its existing branch. Fork PRs are skipped.

## Inputs (skill args)

- `repo` (string, required): Target repository in `owner/repo` form.
- `state` (string, optional): PR state filter for discovery. Default is `open`. Using other states prints a warning.
- `includeForks` (boolean, optional): Reserved for compatibility. Currently rejected because queued `pr-resolver` jobs cannot reliably check out fork-only head refs.
- `skipExistingOnly` (boolean, optional): Legacy alias kept for compatibility. If set, skip PRs from fork repositories. **Note:** this name is counter-intuitive; `true` means forks are skipped.
- `maxAttempts` (number, optional): Queue job `maxAttempts` for each created task. Default `3`.
- `priority` (number, optional): Queue job priority. Default `0`.
- `publishMode` (string, optional): Child `pr-resolver` task publish mode (`none|branch|pr`). Default `branch`.
- `mergeMethod` (string, optional): Merge method passed to `pr-resolver`. Default `squash`.
- `maxIterations` (number, optional): `pr-resolver` loop cap. Default `3`.

## Workflow

1. Run the helper script:

```bash
python3 .agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py \
  --repo <owner/repo> \
  --state <open|merged|closed> \
  --skip-existing-only \
  --max-attempts 3 \
  --priority 0 \
  --publish-mode branch \
  --merge-method squash \
  --max-iterations 3
```

2. Map inputs to flags:
   - `repo` -> `--repo`
   - `state` -> `--state`
   - `skipExistingOnly` -> `--skip-existing-only`
   - `includeForks` -> `--include-forks` (currently rejected at runtime)
   - `maxAttempts` -> `--max-attempts`
   - `priority` -> `--priority`
   - `publishMode` -> `--publish-mode`
   - `mergeMethod` -> `--merge-method`
   - `maxIterations` -> `--max-iterations`

3. For each open PR in the target repo:
   - Skip PRs identified as cross-repository (`isCrossRepository=true`) or whose head is not on `owner/repo`.
   - Build a canonical queue task with:
     - `type: "task"`
     - `payload.repository`: target repo
     - `payload.task.git.startingBranch`: PR head branch
     - `payload.task.publish.mode`: `publishMode` input (default `branch`)
     - `payload.task.skill.id`: `pr-resolver`
     - `payload.task.skill.args`: `{ repo, pr, branch, mergeMethod, maxIterations }`
   - Submit via internal queue service (`AgentQueueService`).
4. Write one summary artifact at `artifacts/batch_pr_resolver_result.json`.
5. Print a short count summary to stdout (`queued`, `skipped`, `errors`).

## Safety constraints

- Reject missing `repo` unless it can be inferred from `git remote origin` fallback.
- Use `state=open` by default to avoid accidental non-open PR dispatch.
- `--include-forks` is rejected to avoid unreliable fork-branch checkout behavior in queued jobs.
- Skip fork PRs by default.
