# Data Model: Merge Automation Visibility

## MergeAutomationVisibility

Compact operator-facing projection.

- `enabled`: boolean indicating merge automation was requested or active.
- `status`: current or terminal merge automation status.
- `prNumber`: pull request number when known.
- `prUrl`: pull request URL when known.
- `latestHeadSha`: latest tracked PR head SHA.
- `cycles`: number of resolver attempts or gate cycles.
- `childWorkflowId`: parent-owned merge automation child workflow id.
- `resolverChildWorkflowIds`: resolver child workflow ids.
- `blockers`: bounded blocker summaries.
- `artifactRefs`: artifact ids for summary, gate snapshots, and resolver attempts.

Validation rules:

- Text fields are bounded and sanitized before exposure.
- Missing optional fields are omitted rather than guessed.
- `enabled` is true only when merge automation was requested, started, or returned a result.

## MergeAutomationArtifactSet

Durable JSON artifacts written by `MoonMind.MergeAutomation`.

- `reports/merge_automation_summary.json`: latest compact summary.
- `artifacts/merge_automation/gate_snapshots/<cycle>.json`: readiness evaluation state.
- `artifacts/merge_automation/resolver_attempts/<attempt>.json`: resolver launch/result state.

State transitions:

- Waiting writes or updates summary and gate snapshots.
- Executing writes resolver attempt artifacts.
- Terminal states update the summary artifact reference in the returned payload.
