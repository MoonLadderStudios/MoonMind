---
name: batch-dependabot-resolver
description: Discover open Dependabot version-bump PRs and enqueue one `pr-resolver` workflow for each.
---

# Batch Dependabot Resolver Skill

## Purpose

Discover open **Dependabot** dependency-version PRs in a target GitHub repository
and enqueue one `pr-resolver` workflow per matching PR. The skill is designed to
run on a daily or weekly recurring schedule through MoonMind's Temporal-backed
recurring schedule system (`/api/recurring-tasks`).

This is intentionally a narrower discovery/filter layer on top of the same
`pr-resolver` child mechanism used by `batch-pr-resolver`. It encodes
Dependabot-specific assumptions and safer defaults rather than overloading the
general-purpose batch resolver with filters.

## Inputs (skill args)

- `repo` (string, required): Target repository in `owner/repo` form. Falls back
  to parent task context, `git remote origin`, or environment when omitted.
- `state` (string, optional): PR state filter for discovery. Default `open`.
  Using other states prints a warning.
- `mergeMethod` (string, optional): Merge method passed to `pr-resolver`. Default `squash`.
- `maxIterations` (number, optional): `pr-resolver` loop cap. Default `3`.
- `maxAttempts` (number, optional): Queue job `maxAttempts` for each created task. Default `3`.
- `priority` (number, optional): Queue job priority. Default `0`.
- `packageManagers` (string, optional): Comma-separated allowlist of Dependabot
  package managers (e.g. `pip,npm,github-actions`). When omitted, all are allowed.
  Aliases are normalized to Dependabot branch ecosystem segments (`npm` →
  `npm_and_yarn`, `github-actions` → `github_actions`).
- `titleRegex` (string, optional): Override for the version-bump title regex.
  Default `^Bump .+ from \S+ to \S+$`.
- `includeSecurityUpdates` (boolean, optional): Default `true`. When `false`,
  PRs labelled `security` are skipped.
- `maxPrs` (number, optional): Safety cap on the number of resolver workflows queued.
- `dryRun` (boolean, optional): Default `false`. When `true`, discover and match
  PRs but do not submit resolver workflows.
- `runtimeMode` / `runtimeModel` / `runtimeEffort` / `runtimeProviderProfile`
  (string, optional): Runtime selection stamped onto each queued `pr-resolver`
  task. Inherited from the parent task context like `batch-pr-resolver`.

## Workflow

1. Run the helper script:

```bash
python3 .agents/skills/batch-dependabot-resolver/bin/batch_dependabot_resolver.py \
  --repo <owner/repo> \
  --state open \
  --merge-method squash \
  --max-iterations 3 \
  --max-attempts 3 \
  --priority 0 \
  --package-managers pip,npm,github-actions \
  --title-regex '^Bump .+ from \S+ to \S+$' \
  --max-prs 25 \
  --runtime-mode <runtime_mode> \
  --runtime-model <model> \
  --runtime-effort <effort> \
  --runtime-provider-profile <profile_id>
```

   Use `--dry-run` to test the recurring schedule without creating resolver
   workflows, and `--exclude-security-updates` to skip security-labelled PRs.

2. Map inputs to flags:
   - `repo` -> `--repo`
   - `state` -> `--state`
   - `mergeMethod` -> `--merge-method`
   - `maxIterations` -> `--max-iterations`
   - `maxAttempts` -> `--max-attempts`
   - `priority` -> `--priority`
   - `packageManagers` -> `--package-managers`
   - `titleRegex` -> `--title-regex`
   - `includeSecurityUpdates=false` -> `--exclude-security-updates`
   - `maxPrs` -> `--max-prs`
   - `dryRun` -> `--dry-run`
   - `runtimeMode` -> `--runtime-mode`
   - `runtimeModel` -> `--runtime-model`
   - `runtimeEffort` -> `--runtime-effort`
   - `runtimeProviderProfile` -> `--runtime-provider-profile`

   Always forward the parent task's explicit runtime selection fields when they
   are present so the queued `pr-resolver` tasks reuse the same runtime, model,
   effort, and provider profile instead of falling back to the deployment default.

3. Discovery (`gh pr list`) requests enough metadata to identify Dependabot PRs:
   `number, title, author, headRefName, headRefOid, headRepository,
   headRepositoryOwner, isCrossRepository, labels`.

4. A PR is matched only when **all** of the following hold:
   - the PR is open;
   - the PR is not a fork / cross-repository PR (`isCrossRepository=true` or a
     non-`owner/repo` head is skipped, matching `batch-pr-resolver`);
   - the author login is `dependabot[bot]`;
   - the head branch starts with `dependabot/`;
   - the title matches the version-bump regex;
   - the package manager (if an allowlist is provided) is allowed; and
   - the PR is not a security update when `includeSecurityUpdates=false`.

5. For each matching PR, build a canonical queue task with the **same payload
   shape as `batch-pr-resolver`**:
   - `type: "task"`
   - `payload.idempotencyKey`: a stable cross-run key,
     `batch-dependabot-resolver:{repo}:pr:{number}:head:{headSha}` (hashed
     fallback for unusually long repository names). The same Dependabot PR at the
     same head commit only ever gets one resolver; a rebase (new head SHA) allows
     a new resolver.
   - `payload.repository`: target repo
   - `payload.task.git.startingBranch`: PR head branch
   - `payload.task.publish.mode`: `none`
   - `payload.task.skill.name`: `pr-resolver`
   - `payload.task.inputs`: `{ repo, pr, branch, mergeMethod, maxIterations }`
   - Submit via the internal Temporal execution API (`POST /api/executions`);
     `MOONMIND_URL` must point at the MoonMind API from the managed session.

6. Write one summary artifact at `batch_dependabot_resolver_result.json` under the
   managed session artifact spool path when available, otherwise under the
   configured `--artifacts-dir`. The summary lists discovered PRs, matched PRs,
   queued resolver workflows (or planned submissions in `--dry-run`), skipped PRs
   with reasons, and submission errors.

7. Print a short count summary to stdout (`discovered`, `matched`, `queued`,
   `skipped`, `errors`, `dryRun`).

## Recurring schedule

To run the skill daily or weekly, create a Temporal-backed recurring schedule via
`POST /api/recurring-tasks`. MoonMind delegates cron/time handling to Temporal
Schedules; the schedule target starts a `MoonMind.Run` workflow whose
`initialParameters.task` invokes this skill. See
[docs/Temporal/WorkflowSchedulingGuide.md](../../../docs/Temporal/WorkflowSchedulingGuide.md)
for the full schedule contract.

### Daily example (`02:00` every day)

```json
{
  "name": "Daily Dependabot resolver",
  "description": "Discover open Dependabot PRs and enqueue one pr-resolver per PR.",
  "enabled": true,
  "cron": "0 2 * * *",
  "timezone": "America/Los_Angeles",
  "scopeType": "personal",
  "target": {
    "workflowType": "MoonMind.Run",
    "initialParameters": {
      "repository": "MoonLadderStudios/MoonMind",
      "task": {
        "title": "batch-dependabot-resolver",
        "skill": {
          "name": "batch-dependabot-resolver",
          "version": "1.0"
        },
        "inputs": {
          "repo": "MoonLadderStudios/MoonMind",
          "mergeMethod": "squash",
          "maxIterations": 3,
          "packageManagers": "pip,npm,github-actions",
          "maxPrs": 25,
          "dryRun": false
        },
        "publish": { "mode": "none" }
      }
    }
  },
  "policy": {
    "overlap": "skip",
    "catchup": "last",
    "jitterSeconds": 30
  }
}
```

### Weekly example (`02:00` every Monday)

Use the same payload with a weekly cron expression:

```json
{
  "name": "Weekly Dependabot resolver",
  "cron": "0 2 * * 1",
  "timezone": "America/Los_Angeles",
  "scopeType": "personal",
  "target": {
    "workflowType": "MoonMind.Run",
    "initialParameters": {
      "repository": "MoonLadderStudios/MoonMind",
      "task": {
        "title": "batch-dependabot-resolver",
        "skill": { "name": "batch-dependabot-resolver", "version": "1.0" },
        "inputs": {
          "repo": "MoonLadderStudios/MoonMind",
          "mergeMethod": "squash",
          "maxIterations": 3,
          "dryRun": false
        },
        "publish": { "mode": "none" }
      }
    }
  }
}
```

Set `"dryRun": true` in `inputs` to test a new schedule without creating any
`pr-resolver` workflows.

## Safety constraints

- Reject missing `repo` unless it can be inferred from task context, the
  `git remote origin` fallback, or environment.
- Use `state=open` by default to avoid accidental non-open PR dispatch.
- Skip fork / cross-repository PRs (cannot reliably check out fork-only head refs).
- Match only `dependabot[bot]`-authored version-bump PRs on `dependabot/` branches.
- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue
  fallback is intentionally unsupported.
