# Data Model: Merge Outcome Propagation

## MergeAutomationTerminalStatus

Represents the terminal status returned by `MoonMind.MergeAutomation` to the parent `MoonMind.Run`.

Allowed values:
- `merged`: Pull request was merged by automation.
- `already_merged`: Pull request was already merged before or during automation finalization.
- `blocked`: Merge automation reached a terminal blocker that requires operator action.
- `failed`: Merge automation failed while evaluating readiness, running resolver work, or finalizing.
- `expired`: Merge automation exceeded its allowed waiting window.
- `canceled`: Merge automation was canceled.

Validation rules:
- `merged` and `already_merged` are the only parent-success statuses.
- `blocked`, `failed`, and `expired` are parent-failure statuses.
- `canceled` is a parent-cancellation status.
- Missing or unsupported status values produce deterministic non-success outcomes with operator-readable reasons.

## ParentCompletionOutcome

Represents the terminal outcome emitted by the original parent `MoonMind.Run`.

Fields:
- `status`: Parent terminal status, one of success, failed, or canceled.
- `summary`: Operator-readable reason for non-success or cancellation outcomes.
- `mergeAutomationStatus`: Compact child status copied into parent publish context.
- `mergeAutomationResult`: Compact child result for operator-visible summaries and verification.

State transitions:
- Awaiting merge automation to success when child status is `merged` or `already_merged`.
- Awaiting merge automation to failed when child status is `blocked`, `failed`, `expired`, missing, or unsupported.
- Awaiting merge automation to canceled when child status is `canceled` or parent cancellation interrupts the active child.

## DependencySatisfactionSignal

Represents the downstream dependency signal derived from the parent workflow terminal outcome.

Rules:
- Dependencies are satisfied only by parent terminal success.
- Parent failure produces a failed dependency resolution.
- Parent cancellation produces a canceled dependency resolution.
- Merge automation and resolver child workflow ids are not replacement dependency targets.

## CancellationPropagationState

Represents active child workflow cancellation relationships.

Fields:
- `parentWorkflowId`: Original `MoonMind.Run` workflow id.
- `mergeAutomationWorkflowId`: Active merge automation child id when present.
- `resolverChildWorkflowId`: Active resolver child id when present.
- `cleanupSummary`: Truthful best-effort cancellation or cleanup description.

Rules:
- Parent cancellation requests cancellation of active merge automation child work.
- Merge automation cancellation requests cancellation of active resolver child work.
- Cleanup summaries do not claim completion when child cancellation confirmation is unavailable.
