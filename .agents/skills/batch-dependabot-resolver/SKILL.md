---
name: batch-dependabot-resolver
description: Discover open Dependabot version-bump PRs and enqueue one `pr-resolver` workflow for each.
metadata:
  sideEffect:
    kind: enqueue_children
    owner: agent
    outcomeArtifact: artifacts/batch_dependabot_resolver_result.json
    terminalContractId: batch_dependabot_resolver_fanout.v1
    terminalSchemaVersion: moonmind.batch-dependabot-resolver-result.v1
  required-capabilities:
    - gh
inputSchema:
  type: object
  properties:
    repo:
      type: string
      title: Repository
      description: >-
        Target repository in owner/repo form; inferred from task context when omitted.
      x-moonmind-context-default: repository
    state:
      type: string
      title: Pull request state
      enum: [open, closed, merged, all]
      default: open
    mergeMethod:
      type: string
      title: Merge method
      enum: [squash, merge, rebase]
      default: squash
    maxIterations:
      type: integer
      title: Resolver iteration limit
      minimum: 1
      default: 5
    maxAttempts:
      type: integer
      title: Queue attempt limit
      minimum: 1
      default: 3
    priority:
      type: integer
      title: Queue priority
      default: 0
    packageManagers:
      type: array
      title: Package managers
      items:
        type: string
      default: []
    titleRegex:
      type: string
      title: Version-bump title regex
      default: '^(?:Bump|[Cc]hore\(deps\): bump) .+ from \S+ to \S+(?: in /.+)?$'
    includeSecurityUpdates:
      type: boolean
      title: Include security updates
      default: true
    maxPrs:
      type: integer
      title: Maximum PRs per run
      minimum: 1
    dryRun:
      type: boolean
      title: Dry run
      default: false
    runtimeMode:
      type: string
      title: Child runtime override
    runtimeModel:
      type: string
      title: Child model override
    runtimeEffort:
      type: string
      title: Child effort override
    runtimeProviderProfile:
      type: string
      title: Child provider profile override
uiSchema:
  titleRegex:
    widget: textarea
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

This parent batch skill does not publish repository changes itself. It records
child workflow queueing evidence in `artifacts/batch_dependabot_resolver_result.json`;
each queued `pr-resolver` child owns its repository publishing outcome.

## Inputs (skill args)

- `repo` (string, required): Target repository in `owner/repo` form. Falls back to
  parent task context, `git remote origin`, then env, like `batch-pr-resolver`.
- `state` (string, optional): PR state filter for discovery. Default `open`. Other
  states print a warning.
- `mergeMethod` (string, optional): Merge method passed to `pr-resolver`. Default `squash`.
- `maxIterations` (number, optional): `pr-resolver` loop cap. Default `5`.
- `maxAttempts` (number, optional): Queue job `maxAttempts` for each created task. Default `3`.
- `priority` (number, optional): Queue job priority. Default `0`.
- `packageManagers` (list, optional): Allowlist of package managers, e.g. `pip`, `npm`,
  `github-actions`. Matched against the Dependabot branch ecosystem segment with alias
  normalization (`npm`↔`npm_and_yarn`, `github-actions`↔`github_actions`). Omit to allow all.
- `titleRegex` (string, optional): Override for the version-bump title matcher.
  Default `^(?:Bump|[Cc]hore\(deps\): bump) .+ from \S+ to \S+(?: in /.+)?$`
  (matches both Dependabot's legacy `Bump anthropic from 0.105.2 to 0.107.1`
  titles and conventional-commit titles such as `Chore(deps): bump anthropic from
  0.105.2 to 0.107.1`, including subdirectory suffixes like `in /frontend`).
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
   `task.publish.mode = auto`, inherited runtime) and a stable idempotency key:
   `batch-dependabot-resolver:{repo}:pr:{number}:head:{headSha}`. Submit via
   `POST /api/executions`, require the canonical `workflowId`, and verify it via
   `GET /api/executions/{workflowId}` before counting the child as queued
   (`MOONMIND_URL` must point at the MoonMind API).

5. Write a summary artifact `batch_dependabot_resolver_result.json` (under the managed
   session artifact spool when available, otherwise the configured `--artifacts-dir`) listing
   discovered PRs, matched count, queued / would-queue resolver workflows, skipped PRs with
   reasons, runtime-inheritance mode, and submission errors. A deliberate zero-match run also
   writes `skill_outcome.json` with `status: "no_op"`. When the default title contract rejects
   a Dependabot title that still has the shape of a single version bump (`bump ... from ... to
   ...`), the helper records title-contract drift and fails instead of silently reporting a
   successful no-op.

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
            "maxIterations": 5,
            "packageManagers": ["pip", "npm", "github-actions"],
            "dryRun": false
          },
          "publish": { "mode": "auto" }
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
