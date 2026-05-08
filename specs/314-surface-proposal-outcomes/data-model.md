# Data Model: Surface Proposal Outcomes

## Proposal Outcome Summary

Operator-visible summary for one run's proposal stage.

Fields:
- `requested`: whether proposal generation was requested for the run.
- `generatedCount`: number of generated proposal candidates.
- `submittedCount`: number of candidates accepted for proposal submission.
- `deliveredCount`: number of proposals delivered to external tracker issues.
- `validationErrors`: redacted validation errors for skipped malformed candidates.
- `deliveryFailures`: redacted provider-specific delivery failures.
- `externalLinks`: compact list of delivered GitHub/Jira issue links.
- `dedupUpdates`: compact list of proposals attached to or updating existing external issues.

Validation rules:
- Counts must be non-negative integers.
- Error values must be redacted before artifact publication or UI exposure.
- Links must identify provider and external key without embedding credentials.
- Summary payloads must remain compact and must not embed full task snapshots or external issue bodies.

## Proposal Delivery Outcome

Run-scoped representation of one delivered or attempted proposal.

Fields:
- `proposalId`
- `provider`
- `externalKey`
- `externalUrl`
- `deliveryStatus`
- `lastSyncedAt`
- `created`
- `duplicateSource`
- `taskSnapshotRef`
- `taskSummary`
- `promotionResult`
- `errors`

Validation rules:
- `provider`, `externalKey`, and `externalUrl` are present only when known.
- `deliveryStatus` uses bounded proposal-delivery states such as `pending`, `delivered`, `failed`, `updated`, or `deduped`.
- `duplicateSource` is present only when delivery reused or updated an existing issue.
- `errors` are redacted and compact.

## Compact Proposal Task Summary

Small task-facing summary shown to operators without exposing the full task payload.

Fields:
- `runtime`
- `repository`
- `publishMode`
- `priority`
- `maxAttempts`
- `skillContext`
- `presetProvenance`

Validation rules:
- Fields may be omitted when the stored proposal lacks the source value.
- The summary must never be used as the executable task contract.
- Full stored proposal snapshots remain referenced by `taskSnapshotRef`.

## Promotion Result

Visibility record for an externally approved proposal that has been promoted.

Fields:
- `promotedExecutionId`
- `promotedExecutionUrl`
- `providerEventId`
- `resultingExternalState`
- `promotedAt`

Validation rules:
- Promotion links are shown only after a promotion result is known.
- Duplicate provider approvals must not create duplicate promoted executions.

## State Transitions

Proposal-stage run state:
- `scheduled|planning|executing -> proposals -> finalizing|completed|failed|canceled`

Proposal delivery outcome:
- `pending -> delivered`
- `pending -> failed`
- `delivered -> updated`
- `delivered -> promoted`
- `delivered -> dismissed|deferred|request_revision`

Dedup outcome:
- `new candidate -> new external issue`
- `new candidate -> existing external issue update/link`
