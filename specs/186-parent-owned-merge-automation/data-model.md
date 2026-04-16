# Data Model: Parent-Owned Merge Automation

## MergeAutomationRequest

Represents parent-run intent to await merge automation after successful pull request publication.

Fields:
- `enabled`: boolean flag. False or absent preserves current publish behavior.
- `strategy`: selected strategy. For MM-350, the supported value is parent-owned child workflow merge automation.
- `resolver`: compact resolver policy, including skill name and merge method when configured.
- `gate`: readiness policy, including GitHub readiness rules and optional Jira status rules.
- `timeouts`: fallback polling and expiration limits.

Validation:
- Request is effective only when `enabled` is true and parent `publishMode` is `pr`.
- Unsupported strategy values fail fast through normal validation.
- Worker-bound parent input keeps `publishMode` top-level.

## PublishContext

Durable evidence that the parent published a pull request and can start merge automation.

Fields:
- `repository`
- `prNumber`
- `prUrl`
- `baseRef`
- `headRef`
- `headSha`
- `publishedAt`
- `jiraIssueKey`
- `artifactRef`

Validation:
- `repository`, `prNumber`, `prUrl`, `headSha`, and `publishedAt` are required before child workflow start.
- `jiraIssueKey` is optional.
- Provider response bodies, logs, comments, and full check data are excluded; large data must be referenced through artifacts.

## MergeAutomationChildStart

Compact parent-to-child workflow request.

Fields:
- `parentWorkflowId`
- `parentRunId`
- `publishContextRef`
- `publishContextSummary`
- `mergeAutomationConfig`
- `resolverTemplate`

Validation:
- Child workflow id is deterministic for one parent publish context.
- Payload contains compact metadata and refs only.
- Resolver template must keep resolver publish mode as `none`.

## MergeAutomationOutcome

Terminal child result consumed by the parent.

Fields:
- `status`: one of `merged`, `already_merged`, `blocked`, `failed`, `expired`, `canceled`.
- `prNumber`
- `prUrl`
- `cycles`
- `resolverChildWorkflowIds`
- `lastHeadSha`
- `blockers`
- `summary`

Validation:
- Parent success is allowed only for `merged` and `already_merged`.
- `blocked`, `failed`, and `expired` fail the parent with an operator-readable reason.
- `canceled` cancels or stops the parent according to existing cancellation semantics.

## ParentMergeAutomationState

Compact parent metadata while merge automation is active.

Fields:
- `childWorkflowId`
- `publishContextRef`
- `status`
- `latestHeadSha`
- `currentBlockers`
- `cycles`
- `lastUpdatedAt`

State transitions:
- `not_started` -> `starting` after publish succeeds and configuration is effective.
- `starting` -> `awaiting_child` after child workflow identity is recorded.
- `awaiting_child` -> `succeeded` when child returns `merged` or `already_merged`.
- `awaiting_child` -> `failed` when child returns `blocked`, `failed`, or `expired`.
- `awaiting_child` -> `canceled` when child cancellation is observed.

Relationship:
- Parent `MoonMind.Run` remains the only dependency target. The child workflow is subordinate state, not a new top-level task dependency.
