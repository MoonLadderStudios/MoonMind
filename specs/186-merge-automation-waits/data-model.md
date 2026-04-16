# Data Model: Merge Automation Waits

## MergeAutomationStartInput

Compact start payload for `MoonMind.MergeAutomation`.

Fields:
- `workflowType`: exactly `MoonMind.MergeAutomation`.
- `parentWorkflowId`: parent `MoonMind.Run` workflow id.
- `parentRunId`: optional parent Temporal run id.
- `publishContextRef`: artifact-backed publish context reference.
- `pullRequest`: compact PR identity with repository, number, URL, head SHA, optional head/base refs.
- `mergeAutomationConfig`: readiness policy, resolver policy, timeouts, and optional Jira gate configuration.
- `resolverTemplate`: compact child `MoonMind.Run` resolver template.
- `jiraIssueKey`: optional linked Jira issue key.
- `blockers`: current sanitized blockers when continuing as new.
- `cycleCount`: readiness evaluation cycle count.
- `resolverHistory`: resolver child run references already launched.
- `expireAt`: optional deadline timestamp.

Validation:
- `parentWorkflowId`, `publishContextRef`, PR URL, PR number, and PR head SHA are required.
- `fallbackPollSeconds` is normalized to a safe positive bounded value.
- Unsupported workflow types fail validation.

## ReadinessEvidence

Deterministic readiness evidence for the current PR head SHA.

Fields:
- `status`: `waiting`, `open`, `blocked`, or `expired`.
- `headSha`: head SHA the evidence applies to.
- `ready`: whether resolver launch is allowed.
- `blockers`: sanitized blocker list.

Validation:
- Evidence for a different head SHA becomes a stale-revision blocker.
- Unknown provider blocker details are sanitized and preserved as retryable external blockers.

## ReadinessBlocker

Operator-safe reason resolver launch is blocked.

Fields:
- `kind`: machine-readable blocker kind.
- `summary`: sanitized human-readable summary.
- `retryable`: whether waiting and re-evaluation can resolve the blocker.
- `source`: optional provider or policy source.

Validation:
- Secret-like assignments are redacted.
- Summary is bounded for workflow history and UI projection.

## ResolverRunRef

Compact reference to a resolver child run.

Fields:
- `workflowId`: resolver `MoonMind.Run` workflow id.
- `runId`: optional Temporal run id.
- `created`: whether the resolver run was newly created.

## State Transitions

- `initializing` -> `awaiting_external` after input validation and first visibility update.
- `awaiting_external` -> `awaiting_external` after retryable blockers and signal/poll wait.
- `awaiting_external` -> `executing` when readiness opens and resolver creation starts.
- `executing` -> `completed` after resolver launch request is recorded.
- `awaiting_external` -> `failed` for terminal blockers or provider failure.
- `awaiting_external` -> `completed` with output status `expired` when the expire-at deadline passes.
- Any active state -> `canceled` when Temporal cancellation is observed.
