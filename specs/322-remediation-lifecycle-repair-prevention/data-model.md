# Data Model: Observable Remediation Repair and Prevention Lifecycle

## Remediation Lifecycle State

Represents the current remediation-specific phase for one remediation run.

Fields:
- `remediationWorkflowId`: logical remediation execution ID.
- `targetWorkflowId`: logical target execution ID.
- `targetRunId`: pinned target run used as diagnosis snapshot anchor.
- `phase`: one of `collecting_evidence`, `diagnosing`, `awaiting_approval`, `acting`, `verifying`, `resolved`, `escalated`, `failed`.
- `mode`: remediation evidence/follow mode, such as `snapshot_then_follow`.
- `authorityMode`: active remediation authority mode.
- `evidenceDegraded`: whether required evidence was partial, unavailable, unsupported, or denied.
- `unavailableEvidenceClasses`: bounded list of unavailable evidence classes.
- `fallbacksUsed`: bounded list of fallback evidence/finalization paths.

Validation:
- Unknown phases normalize to `failed` or fail closed at the lifecycle decision boundary.
- The top-level task execution state remains unchanged by this subordinate phase.
- Secret-like values and raw paths are excluded from all summary fields.

## Repair Decision

Records the immediate-repair candidate and outcome for the target.

Fields:
- `candidateActionKind`: typed action considered, when one exists.
- `decision`: `attempted`, `skipped`, `denied`, `unsafe`, `approval_required`, or `escalated`.
- `reason`: bounded reason code.
- `freshTargetHealthRef`: optional ref or compact summary proving current target health was read before action.
- `authorityDecisionRef`: optional action authority decision ref.
- `guardDecisionRef`: optional mutation guard decision ref.
- `actionRequestRef`: optional `remediation.action_request` artifact ref.
- `actionResultRef`: optional `remediation.action_result` artifact ref.
- `verificationRef`: optional `remediation.verification` artifact ref.
- `repairOutcome`: `repaired`, `still_failed`, `not_attempted`, `unsafe`, `approval_required`, or `escalated`.

Validation:
- `attempted` requires authority, guard, action request, action result, and verification evidence.
- `approval_required` requires approval context or policy reason.
- `unsafe`, `denied`, and `escalated` require no side-effecting action refs unless an action was already requested before the decision.
- Repair outcome is target-health oriented and does not reuse raw action execution status as the final outcome.

## Prevention Outcome

Records recurrence-prevention analysis after repair decision.

Fields:
- `status`: `reviewable_change_created`, `findings_reported`, `no_reviewable_fix`, or `policy_blocked`.
- `rootCauseCategory`: bounded category string.
- `summary`: short redacted prevention summary.
- `branch`: optional branch name for a reviewable change.
- `commit`: optional commit ID for a reviewable change.
- `pullRequestUrl`: optional reviewable PR URL.
- `findingsRef`: optional artifact ref for findings-only output.
- `blockedReason`: optional reason when policy prevents a reviewable change.

Validation:
- `reviewable_change_created` requires a PR URL or equivalent reviewable change ref.
- `findings_reported` requires `findingsRef` or a bounded summary.
- `no_reviewable_fix` requires a reason.
- `policy_blocked` requires `blockedReason`.

## Remediation Decision Log

Append-only bounded decision evidence for one remediation run.

Fields:
- `schemaVersion`: `v1`.
- `entries`: ordered decision entries.
- Entry fields: `timestamp`, `phase`, `decisionType`, `decision`, `reason`, `actor`, `actionKind`, `artifactRefs`, `targetWorkflowId`, `targetRunId`, `metadata`.

Validation:
- Entries are compact and redacted.
- Artifact refs are refs only, never raw artifact bodies.
- Decision log includes repair candidate, attempted/skipped/denied/escalated reason, action/verification refs when present, recurrence category, prevention refs, and no-change reasons.

## Remediation Final Summary

Terminal compact summary published as `reports/remediation_summary.json`.

Fields:
- Existing summary fields from `build_remediation_summary_block()`.
- `repair`: embedded Repair Decision summary.
- `prevention`: embedded Prevention Outcome summary.
- `decisionLogRef`: decision log artifact ref.
- `finalAuditRef`: optional final audit artifact ref.
- `lockRelease`: `attempted`, `released`, `not_held`, or `failed`.
- `resultingTargetRunId`: optional run ID when remediation action changes the target run.

Validation:
- Terminal summaries are published for resolved, escalated, failed, and canceled remediation outcomes where publication is possible.
- Cancellation records any already-requested action but does not introduce new target mutation.
- Continue-As-New state preserves target identity, context ref, lock identity, action ledger, approval state, retry budget state, and live-follow cursor.

## State Transitions

Allowed lifecycle progression:

```text
collecting_evidence -> diagnosing
diagnosing -> awaiting_approval | acting | resolved | escalated
awaiting_approval -> acting | escalated
acting -> verifying | escalated
verifying -> resolved | diagnosing | escalated
resolved -> terminal
escalated -> terminal
failed -> terminal
```

Cancellation may interrupt any non-terminal phase and must attempt final summary/audit publication plus lock release without initiating new target mutation.
