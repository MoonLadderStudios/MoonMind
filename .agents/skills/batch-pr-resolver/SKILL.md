---
name: batch-pr-resolver
description: Discover open PRs in a repository, optionally limited to selected PR numbers, and enqueue one `pr-resolver` task for each.
metadata:
  sideEffect:
    kind: enqueue_children
    owner: agent
    outcomeArtifact: artifacts/batch_pr_resolver_result.json
    terminalContractId: batch_pr_resolver_fanout.v1
    terminalSchemaVersion: moonmind.batch-pr-resolver-result.v1
  required-capabilities:
    - gh
---

# Batch PR Resolver Skill

## Purpose

Create one queue task per open pull request so each PR branch can be resolved by `pr-resolver` on its existing branch. Fork PRs are skipped.

This parent batch skill does not publish repository changes itself. It records
child workflow queueing evidence in `artifacts/batch_pr_resolver_result.json`;
each queued existing-PR coordinator delegates repository publication to its `pr-resolver` children and owns their durable external waits.
The coordinator's execution-local publish mode `none` does not override the
children's `auto` publishing contract. Preserve any explicit user restriction
on child publishing; do not infer one from the coordinator's local mode.

## Inputs (skill args)

- `repo` (string, required): Target repository in `owner/repo` form.
- `state` (string, optional): PR state filter for discovery. Default is `open`. Using other states prints a warning.
- `pullRequests` (string, optional): Only queue these PRs, as numbers or inclusive ranges such as `4724-4746, 4765`. The helper inherits this field from the parent task context when its CLI override is omitted. When omitted everywhere, every open PR is queued.
- `maxAttempts` (number, optional): Queue job `maxAttempts` for each created task. Default `3`.
- `priority` (number, optional): Queue job priority. Default `0`.
- `mergeMethod` (string, optional): Merge method passed to `pr-resolver`. Default `squash`.
- `maxIterations` (number, optional): `pr-resolver` loop cap. Default `5`.
- `childInstructions` (string, optional): Additional requirements appended to every queued resolver's instructions. The helper inherits this field from the parent task context when its CLI override is omitted.
- `runtimeMode` (string, optional): Runtime to stamp onto each queued `pr-resolver` task. When omitted, the helper falls back to inherited task context or deployment defaults.
- `runtimeModel` (string, optional): Explicit model override to stamp onto each queued `pr-resolver` task.
- `runtimeEffort` (string, optional): Explicit effort override to stamp onto each queued `pr-resolver` task.
- `runtimeProviderProfile` (string, optional): Explicit provider-profile override to stamp onto each queued `pr-resolver` task.

## Workflow

The helper requires Python 3 and `httpx`, plus `gh` for discovery. Keep the
portable `_shared/workflow_execution_client.py` beside the skill directories;
resolved snapshots include it automatically. No MoonMind or API-service
installation is required. Resolve helpers exclusively from the run's immutable
active bundle:

```bash
BATCH_PR_RESOLVER_SKILL_DIR="${BATCH_PR_RESOLVER_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/batch-pr-resolver}}"
test -n "$BATCH_PR_RESOLVER_SKILL_DIR" && test -f "$BATCH_PR_RESOLVER_SKILL_DIR/SKILL.md"
```

Inside MoonMind, `MOONMIND_ACTIVE_SKILLS_DIR` is always set; a checked-in
`.agents/skills` directory must never shadow the selected snapshot. When no
skill-specific override is present, the helper resolves to
`${MOONMIND_ACTIVE_SKILLS_DIR:-.agents/skills}/batch-pr-resolver/bin/batch_pr_resolver.py`.
Outside
MoonMind, set `BATCH_PR_RESOLVER_SKILL_DIR` to the directory containing this
`SKILL.md` (no MoonMind-only environment variables required). A missing
selected helper is a materialization/packaging error: stop as blocked instead
of substituting stale repository code.

1. Run the helper script:

```bash
python3 "$BATCH_PR_RESOLVER_SKILL_DIR/bin/batch_pr_resolver.py" \
  --repo <owner/repo> \
  --state <open|merged|closed> \
  --max-attempts 3 \
  --priority 0 \
  --merge-method squash \
  --max-iterations 5 \
  --runtime-mode <runtime_mode> \
  --runtime-model <model> \
  --runtime-effort <effort> \
  --runtime-provider-profile <profile_id>
```

2. Map inputs to flags:
   - `repo` -> `--repo`
   - `state` -> `--state`
   - `pullRequests` -> `--pull-requests`
   - `maxAttempts` -> `--max-attempts`
   - `priority` -> `--priority`
   - `mergeMethod` -> `--merge-method`
   - `maxIterations` -> `--max-iterations`
   - `childInstructions` -> `--child-instructions`
   - `runtimeMode` -> `--runtime-mode`
   - `runtimeModel` -> `--runtime-model`
   - `runtimeEffort` -> `--runtime-effort`
   - `runtimeProviderProfile` -> `--runtime-provider-profile`

   Always forward the parent task's explicit runtime selection fields when they are present so the queued `pr-resolver` tasks reuse the same runtime, model, effort, and provider profile instead of falling back to the deployment default runtime.

   Carry user constraints into every child through `childInstructions`; pass
   `--child-instructions` explicitly when no task context is materialized.
   The adopting PR coordinator carries these instructions through its durable
   gate into each repair and final resolver pass.

   When the request names specific PRs or a PR range (for example "Select PRs
   from #4724 to #4746"), pass that selection with `--pull-requests` so the
   helper filters discovery before it submits anything. Never wrap, shim, or
   monkeypatch the helper to narrow its selection; a selection the flag cannot
   express is a blocker to report, not a reason to queue every open PR.

3. For each selected open PR in the target repo (every open PR when no selection is given):
   - Skip PRs identified as cross-repository (`isCrossRepository=true`) or whose head is not on `owner/repo`.
   - Build a canonical queue task with:
     - `type: "task"`
     - `payload.idempotencyKey`: stable per parent batch run and PR, hash-backed and capped to the execution persistence limit, so rerunning the same batch task does not create duplicate resolver workflows.
     - `payload.repository`: target repo
     - `payload.task.taskTemplate.slug`: `pr-review-resolve`
     - `payload.task.taskTemplate.inputs`: `{ repository: repo, pull_request: pr_number, review_provider: "none", finish_with_pr_resolver: true, merge_method: mergeMethod, max_iterations: maxIterations }`
     - inherited runtime, model, effort, and Provider Profile fields.
   - Trusted preset expansion resolves the existing PR branch and publication scope. The coordinator publishes nothing itself; its ordinary `pr-resolver` children own fixes and merge. CI/provider waits return to that enclosing durable gate so queued self-hosted Tactics checks do not consume an agent slot. Preserve all Tactics CI on self-hosted runners.
   - Submit via the internal Temporal execution API (`POST /api/executions`),
     require the canonical `workflowId` in the response, and verify that ID via
     `GET /api/executions/{workflowId}` before counting it as queued;
     `MOONMIND_URL` must point at the MoonMind API from the managed session.
     When MoonMind supplies `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE`
     (preferred) or `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN`, read and forward
     the execution-scoped bearer and mark both calls as fan-out v1. A declared
     token file that is missing or empty is a hard failure; do not fall back to
     the ambient value.
4. Write one summary artifact at `batch_pr_resolver_result.json` under the managed session artifact spool path when available, otherwise under the configured `--artifacts-dir`. The helper binds it to `MOONMIND_STEP_EXECUTION_ID` and records `running` before discovery, then `queued`, `no_op`, `partial_failure`, or `failed`. MoonMind validates this terminal evidence before accepting completion; a clean harness exit with no result is incomplete.
5. On any submission or verification error, write `skill_outcome.json` with a
   `failed` or `partial` status so the managed runtime cannot report the batch
   as successful.
6. Print a short count summary to stdout (`queued`, `skipped`, `errors`).

## Security constraints

- Reject missing `repo` unless it can be inferred from `git remote origin` fallback.
- Use `state=open` by default to avoid accidental non-open PR dispatch.
- Skip fork PRs by default: PRs identified as cross-repository
  (`isCrossRepository=true`) or whose head is not on `owner/repo` are recorded
  as `fork-pr` skips. There is no fork-inclusion option; queued `pr-resolver`
  jobs cannot reliably check out fork-only head refs.
- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue fallback is intentionally unsupported.
