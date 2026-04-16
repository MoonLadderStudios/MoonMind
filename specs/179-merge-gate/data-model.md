# Data Model: Merge Gate

## MergeAutomationRequest

Represents task-level intent to run merge automation after successful pull request publication.

Fields:

- `enabled`: boolean; defaults to false unless explicitly requested by task or policy.
- `policy`: merge automation readiness policy.
- `mergeMethod`: optional merge method forwarded to pr-resolver when supported.
- `jiraIssueKey`: optional linked Jira issue key such as `MM-341`.

Validation:

- Disabled requests must not start a merge gate.
- Enabled requests require a published pull request URL or equivalent PR identity before gate creation.
- Unsupported policy values fail fast rather than silently falling back.

## PullRequestRef

Compact identity for the pull request being gated.

Fields:

- `repo`: repository in `owner/name` form.
- `number`: pull request number.
- `url`: pull request URL.
- `headSha`: current tracked revision.
- `headBranch`: optional PR source branch.
- `baseBranch`: optional PR target branch.

Validation:

- `repo`, `number`, `url`, and `headSha` are required for gate creation.
- A readiness result for a different `headSha` is stale and cannot open the gate.

## MergeGateState

Durable state for one merge gate workflow.

Fields:

- `parentWorkflowId`: workflow ID of the implementation `MoonMind.Run`.
- `parentRunId`: optional Temporal run ID of the implementation workflow.
- `pullRequest`: `PullRequestRef`.
- `jiraIssueKey`: optional linked Jira issue key.
- `policy`: readiness policy.
- `status`: one of `waiting`, `blocked`, `open`, `resolver_launched`, `completed`, `failed`, `canceled`.
- `blockers`: list of current `ReadinessBlocker` values.
- `resolverRun`: optional `ResolverRunRef`.
- `lastEvaluatedAt`: timestamp of last readiness evaluation.

Validation:

- `resolverRun` may be set only once for a given gate and pull request revision.
- Terminal statuses cannot transition back to waiting.
- `open` transitions to `resolver_launched` only after resolver creation succeeds.

State transitions:

```text
waiting -> waiting
waiting -> blocked
waiting -> open
open -> resolver_launched
resolver_launched -> completed
waiting -> failed
blocked -> failed
waiting -> canceled
```

## ReadinessEvidence

Compact activity result describing current external readiness for one PR revision.

Fields:

- `headSha`: revision evaluated.
- `checksComplete`: boolean.
- `checksPassing`: boolean.
- `automatedReviewComplete`: boolean.
- `jiraStatusAllowed`: boolean or null when Jira is not configured for the policy.
- `pullRequestOpen`: boolean.
- `policyAllowed`: boolean.
- `blockers`: list of `ReadinessBlocker`.

Validation:

- Evidence for a mismatched `headSha` must be treated as stale.
- Any blocker prevents resolver launch.
- Provider-specific raw bodies, comments, check logs, and credentials are not stored in evidence.

## ReadinessBlocker

Operator-visible reason that a gate remains waiting or blocked.

Fields:

- `kind`: bounded value such as `checks_running`, `checks_failed`, `automated_review_pending`, `jira_status_pending`, `pull_request_closed`, `stale_revision`, `policy_denied`, `external_state_unavailable`.
- `summary`: sanitized human-readable explanation.
- `retryable`: boolean.
- `source`: optional bounded source label such as `github`, `jira`, or `policy`.

Validation:

- Summaries must not include raw provider secrets, auth headers, full check logs, or raw comments.
- Non-retryable blockers prevent resolver launch until a new gate or explicit operator action.

## ResolverRunRef

Reference to the follow-up `MoonMind.Run` created by the merge gate.

Fields:

- `workflowId`: resolver workflow ID.
- `runId`: optional Temporal run ID when available.
- `createdAt`: timestamp.
- `prResolverInputs`: compact summary of repo, PR number or URL, merge method, and policy refs.

Validation:

- Created resolver runs must use pr-resolver and publish mode `none`.
- A gate cannot replace an existing resolver ref for the same PR revision.
