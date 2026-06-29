---
name: batch-dependabot-resolver
description: Discover open Dependabot version-bump PRs and enqueue one `pr-resolver` workflow for each.
metadata:
  required-capabilities:
    - gh
---

# Batch Dependabot Resolver Skill

## Purpose

Discover open Dependabot dependency-version PRs in a target repository and enqueue
one `pr-resolver` workflow per matching PR, so Dependabot bumps can be resolved
automatically on a daily or weekly recurring schedule. This is a narrower,
safer-by-default discovery/filter layer over `batch-pr-resolver`: it reuses the
same child `pr-resolver` payload shape, fork/cross-repo safety, runtime
inheritance, and `/api/executions` submission path, and adds Dependabot-specific
matching, a cross-run-stable idempotency key, a dry-run mode, an optional `maxPrs`
cap, and a Dependabot-specific summary artifact.

## Inputs (skill args)

- `repo` (string, required): Target repository in `owner/repo` form. Falls back to
  parent task context, `git remote origin`, then env, like `batch-pr-resolver`.
- `state` (string, optional): PR state filter for discovery. Default `open`. Other
  states print a warning.
- `mergeMethod` (string, optional): Merge method passed to `pr-resolver`. Default `squash`.
- `maxIterations` (number, optional): `pr-resolver` loop cap. Default `3`.
- `maxAttempts` (number, optional): Queue job `maxAttempts` for each created task. Default `3`.
- `priority` (number, optional): Queue job priority. Default `0`.
- `packageManagers` (list, optional): Allowlist of package managers, e.g. `pip`, `npm`,
  `github-actions`. Matched against the Dependabot branch ecosystem segment with alias
  normalization (`npm`↔`npm_and_yarn`, `github-actions`↔`github_actions`). Omit to allow all.
- `titleRegex` (string, optional): Override for the version-bump title matcher.
  Default `^Bump .+ from \S+ to \S+(?: in /.+)?$` (matches e.g. `Bump anthropic from 0.105.2 to 0.107.1` and subdirectory suffixes like `in /frontend`).
- `includeSecurityUpdates` (boolean, optional): Default `true`. When `false`, PRs labeled
  `security` are skipped.
- `maxPrs` (number, optional): Safety cap on the number of resolver workflows queued per run.
- `dryRun` (boolean, optional): Default `false`. When `true`, compute and report the resolver
  workflows that would be submitted without creating any.
- `runtimeMode` / `runtimeModel` / `runtimeEffort` / `runtimeProviderProfile` (string, optional):
  Inherited like `batch-pr-resolver` when omitted.

## Workflow

1. Run the helper script:

```bash
python3 .agents/skills/batch-dependabot-resolver/bin/batch_dependabot_resolver.py \
  --repo <owner/repo> \
  --merge-method squash \
  --max-iterations 3 \
  --package-managers pip,npm,github-actions \
  --max-prs 25 \
  --runtime-mode <runtime_mode> \
  --runtime-model <model> \
  --runtime-effort <effort> \
  --runtime-provider-profile <profile_id>
```

   Add `--dry-run` to validate a schedule without creating resolver workflows.
   Always forward the parent task's explicit runtime selection fields when present so the
   queued `pr-resolver` tasks reuse the same runtime/model/effort/provider profile.

2. The skill discovers open PRs via
   `gh pr list --json number,title,author,headRefName,headRefOid,headRepository,headRepositoryOwner,isCrossRepository,labels`.

3. A PR is **matched** only when ALL of:
   - it is open and not a fork/cross-repository PR (same head-locality check as `batch-pr-resolver`),
   - the author is `dependabot[bot]` (also accepts `app/dependabot`),
   - the head branch starts with `dependabot/`,
   - the title matches `titleRegex`,
   - it passes the optional `packageManagers` allowlist,
   - (when `includeSecurityUpdates=false`) it is not a `security`-labeled PR,
   - it has a head SHA available for the idempotency key.

   Every non-matching PR is recorded as skipped with a reason: `fork-pr`,
   `not-dependabot-author`, `non-dependabot-branch`, `title-mismatch`,
   `package-manager-filtered`, `security-update-excluded`, `missing-head-sha`, or `max-prs-cap`.

4. For each matched PR, submit a `pr-resolver` task with the canonical `batch-pr-resolver`
   payload (`repository`, `task.inputs = { repo, pr, branch, mergeMethod, maxIterations }`,
   `task.git.startingBranch/branch`, `task.skill.name = pr-resolver`,
   `task.publish.mode = none`, inherited runtime) and a stable idempotency key:
   `batch-dependabot-resolver:{repo}:pr:{number}:head:{headSha}`. Submit via
   `POST /api/executions` (`MOONMIND_URL` must point at the MoonMind API).

5. Write a summary artifact `batch_dependabot_resolver_result.json` (under the managed
   session artifact spool when available, otherwise the configured `--artifacts-dir`) listing
   discovered PRs, matched count, queued / would-queue resolver workflows, skipped PRs with
   reasons, and submission errors. A deliberate zero-match run also writes
   `skill_outcome.json` with `status: "no_op"`. A run with submission errors writes
   `skill_outcome.json` with `status: "failed"` and returns non-zero.

6. Print a short summary to stdout (`matched`, `queued`, `would_queue`, `skipped`, `errors`).

## Idempotency

The idempotency key is `batch-dependabot-resolver:{repo}:pr:{number}:head:{headSha}` (capped at
128 chars with a sha256 fallback). The same Dependabot PR at the same commit only ever gets one
resolver across separate scheduled runs; if Dependabot rebases or updates the PR (new head SHA),
a fresh resolver is allowed.

## Recurring schedule (daily / weekly)

Target this skill from a `queue_task` recurring schedule rather than `pr-resolver` directly.
Example `POST /api/recurring-workflows` body:

```json
{
  "name": "Daily Dependabot resolver (MoonLadderStudios/MoonMind)",
  "description": "Resolve open Dependabot version-bump PRs every morning.",
  "enabled": true,
  "scheduleType": "cron",
  "cron": "0 7 * * *",
  "timezone": "UTC",
  "scopeType": "personal",
  "target": {
    "kind": "queue_task",
    "job": {
      "type": "task",
      "priority": 0,
      "maxAttempts": 3,
      "payload": {
        "repository": "MoonLadderStudios/MoonMind",
        "requiredCapabilities": ["gh"],
        "task": {
          "title": "batch-dependabot-resolver",
          "instructions": "Discover open Dependabot version-bump PRs and enqueue one pr-resolver workflow for each.",
          "skill": { "name": "batch-dependabot-resolver", "version": "1.0" },
          "inputs": {
            "repo": "MoonLadderStudios/MoonMind",
            "mergeMethod": "squash",
            "maxIterations": 3,
            "packageManagers": ["pip", "npm", "github-actions"],
            "dryRun": false
          },
          "publish": { "mode": "none" }
        }
      }
    }
  }
}
```

Use `"cron": "0 7 * * *"` for daily 07:00 UTC or `"cron": "0 7 * * 1"` for weekly (Mondays).
MoonMind delegates cron/time handling to Temporal Schedules. Set `"dryRun": true` in `inputs`
to validate a schedule without creating resolver workflows.

## Safety constraints

- Reject missing `repo` unless it can be inferred from task context / `git remote origin` / env.
- Use `state=open` by default to avoid dispatching against non-open PRs.
- Skip fork and cross-repository PRs (cannot reliably check out fork head refs in queued jobs).
- Match conservatively: author AND branch AND title must all indicate a Dependabot version bump.
- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue fallback is unsupported.
- Never queue a PR without a head SHA (no stable idempotency key) — skip it instead.
