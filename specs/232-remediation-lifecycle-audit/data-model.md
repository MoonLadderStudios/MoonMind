# Data Model: Remediation Lifecycle Audit

## Remediation Lifecycle Snapshot

Represents the compact lifecycle state exposed for a remediation run.

Fields:
- `remediationWorkflowId`: remediation execution workflow ID.
- `remediationRunId`: remediation execution run ID.
- `targetWorkflowId`: target execution workflow ID.
- `targetRunId`: pinned target run ID used as the diagnosis snapshot anchor.
- `phase`: one of `collecting_evidence`, `diagnosing`, `awaiting_approval`, `acting`, `verifying`, `resolved`, `escalated`, or `failed`.
- `resolution`: one of `not_applicable`, `diagnosis_only`, `no_action_needed`, `resolved_after_action`, `escalated`, `unsafe_to_act`, `lock_conflict`, `evidence_unavailable`, or `failed`.
- `evidenceDegraded`: boolean indicating whether expected evidence was unavailable or partial.
- `escalated`: boolean indicating whether the run requires operator or policy escalation.
- `updatedAt`: timestamp for the latest lifecycle update.

Validation:
- `phase` must be bounded to the allowed remediation phase values.
- Terminal phases must have a terminal `resolution`.
- Target identity must remain stable across lifecycle updates.

## Remediation Artifact Record

Represents one required remediation artifact linked to the remediation execution.

Fields:
- `artifactId`: immutable artifact identifier.
- `name`: bounded artifact path such as `reports/remediation_context.json`.
- `artifactType`: one of `remediation.context`, `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.verification`, or `remediation.summary`.
- `remediationWorkflowId`: producing remediation execution.
- `targetWorkflowId`: target execution, when applicable.
- `targetRunId`: pinned target run, when applicable.
- `metadata`: bounded display and classification fields.
- `redactionLevel`: artifact redaction classification.

Validation:
- Artifact metadata must not contain secrets, raw access grants, presigned URLs, storage keys, raw local paths, or unbounded log bodies.
- Artifact refs identify content; they are not access grants.

## Remediation Summary Block

Represents the compact block embedded in the run summary.

Fields:
- `targetWorkflowId`
- `targetRunId`
- `resultingTargetRunId`
- `mode`
- `authorityMode`
- `actionsAttempted[]`
- `resolution`
- `lockConflicts`
- `approvalCount`
- `evidenceDegraded`
- `escalated`
- `unavailableEvidenceClasses[]`
- `fallbacksUsed[]`

Validation:
- `actionsAttempted[]` contains bounded action kind/status summaries, not raw provider payloads.
- `unavailableEvidenceClasses[]` and `fallbacksUsed[]` use bounded symbolic values.

## Target-Side Linkage Summary

Represents compact inbound remediation metadata for target detail views.

Fields:
- `targetWorkflowId`
- `activeRemediationCount`
- `latestRemediationTitle`
- `latestRemediationStatus`
- `latestActionKind`
- `latestOutcome`
- `activeLockScope`
- `activeLockHolder`
- `lastUpdatedAt`

Validation:
- Summaries must be derived from durable remediation links or bounded execution projections.
- Consumers must not parse deep remediation artifact bodies to render the target summary.

## Remediation Audit Event

Represents compact queryable audit evidence for remediation action and approval decisions.

Fields:
- `eventId`
- `actorUser`
- `executionPrincipal`
- `remediationWorkflowId`
- `remediationRunId`
- `targetWorkflowId`
- `targetRunId`
- `actionKind`
- `riskTier`
- `approvalDecision`
- `eventType`
- `timestamp`
- `metadata`

Validation:
- `metadata` must be bounded and redacted.
- Audit events must not replace deep artifacts; they index important control-plane decisions.

## State Transitions

- A run starts without a remediation lifecycle snapshot until evidence collection begins.
- Evidence collection creates or updates phase `collecting_evidence`.
- Diagnosis updates phase `diagnosing`.
- Approval-gated work updates phase `awaiting_approval`.
- Action execution updates phase `acting`.
- Verification updates phase `verifying`.
- Terminal outcomes update phase to `resolved`, `escalated`, or `failed`.
- Cancellation or remediator failure attempts final summary publication and lock release before terminal state is recorded.
- Continue-As-New carries target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor into the continued run.
