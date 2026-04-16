# Data Model: PR Resolver Child Re-Gating

## ResolverChildRun

Represents one child `MoonMind.Run` started by `MoonMind.MergeAutomation` for a pr-resolver attempt.

Fields:
- `workflowId`: Deterministic child workflow id for the resolver attempt.
- `workflowType`: Must be `MoonMind.Run`.
- `initialParameters.publishMode`: Must be exactly `none`.
- `initialParameters.task.tool.type`: Must be `skill`.
- `initialParameters.task.tool.name`: Must be `pr-resolver`.
- `initialParameters.task.tool.version`: Must be `1.0`.
- `inputs.repo`: Repository containing the PR.
- `inputs.pr`: Pull request number.
- `inputs.mergeMethod`: Requested merge method when configured.

Validation rules:
- `publishMode` values other than `none` are invalid for resolver children.
- Missing or different resolver tool identity is invalid for this story.
- The child workflow id must be cycle/head-SHA scoped so repeated attempts do not collide.

## MergeAutomationDisposition

Compact machine-readable resolver outcome consumed by `MoonMind.MergeAutomation`.

Allowed values:
- `merged`: Resolver completed the merge.
- `already_merged`: Resolver found the PR already merged.
- `reenter_gate`: Resolver changed or observed state requiring gate evaluation again.
- `manual_review`: Resolver determined operator review is required.
- `failed`: Resolver failed without a retryable gate wait.

Validation rules:
- Missing disposition is a deterministic non-success outcome.
- Unsupported disposition is a deterministic non-success outcome.
- `merged` and `already_merged` are success outcomes.
- `reenter_gate` is non-terminal and returns to gate evaluation.
- `manual_review` and `failed` are non-success terminal outcomes.

## GateFreshnessState

Represents the readiness state that determines whether resolver launch or merge completion is valid for the current PR head.

Fields:
- `headSha`: Current PR head SHA.
- `cycle`: Current merge automation gate/resolver cycle.
- `blockers`: Logical blocker categories and labels.
- `readyToLaunchResolver`: Whether the gate is open for the tracked head.

State transitions:
- `awaiting_external` to resolver child launch when gate readiness is fresh for the current head.
- resolver `reenter_gate` to `awaiting_external` with an incremented cycle and updated head SHA when available.
- resolver `merged` or `already_merged` to completed.
- resolver `manual_review`, `failed`, missing disposition, or unsupported disposition to failed/non-success result.
