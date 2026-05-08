# Data Model: Publish Remediation Audit Evidence

Traceability: MM-623, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-028.

## Remediation Evidence Set

Represents the complete operator-facing evidence trail for one remediation run.

Fields:
- `remediationWorkflowId`: logical remediation execution identity.
- `remediationRunId`: concrete remediation run identity.
- `targetWorkflowId`: logical target execution identity.
- `targetRunId`: pinned target run used for the evidence snapshot.
- `artifacts`: typed references to applicable remediation evidence artifacts.
- `nonApplicableArtifacts`: bounded reasons for artifacts that do not apply to the selected path.
- `evidenceDegraded`: boolean indicating incomplete evidence.
- `degradedReasons`: bounded reason codes or messages when evidence is incomplete.

Relationships:
- Belongs to one remediation run.
- References one target execution and pinned target run.
- Contains many artifact references.
- Links to one remediation summary and zero or more audit events.

Validation rules:
- Artifact references must be identifiers, not URLs or local paths.
- Metadata must not contain secrets, auth headers, cookies, raw storage keys, presigned URLs, or absolute local paths.
- Missing non-applicable artifacts must have bounded reasons.

## Remediation Artifact Record

Represents one durable artifact in the remediation evidence trail.

Fields:
- `artifactId`: durable artifact reference.
- `artifactType`: one of `remediation.context`, `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.audit_event`, `remediation.target_annotation`, `remediation.verification`, or `remediation.summary`.
- `name`: presentation label or path-like artifact name.
- `contentType`: artifact content type.
- `redactionLevel`: presentation safety classification.
- `metadata`: bounded safe metadata.
- `targetWorkflowId`: target identity when applicable.
- `targetRunId`: target run identity when applicable.

Relationships:
- Belongs to one remediation evidence set.
- May be referenced by decision log entries, repair decisions, prevention outcomes, or the final summary.

Validation rules:
- `artifactType` must be remediation-specific for this feature.
- Metadata must be safe for control-plane display.
- Raw artifact access must not be implied by artifact references.

## Decision Log Entry

Represents one bounded remediation decision.

Fields:
- `timestamp`: event time.
- `phase`: bounded remediation phase.
- `decisionType`: category such as repair candidate, prevention, approval, cancellation, or verification.
- `decision`: attempted, skipped, denied, escalated, or equivalent bounded outcome.
- `reason`: bounded human-readable rationale.
- `actor`: service, operator, or remediation principal.
- `actionKind`: action kind when applicable.
- `targetWorkflowId`: target execution identity.
- `targetRunId`: target run identity.
- `artifactRefs`: references to related action, verification, prevention, or no-PR evidence.
- `metadata`: bounded safe metadata.

Relationships:
- Belongs to a remediation decision log artifact.
- May reference action request, action result, verification, prevention findings, or summary artifacts.

Validation rules:
- At least one entry is required when publishing a decision log.
- Unsafe metadata keys and values must be removed or redacted.
- Attempted repair decisions require action and verification references.

## Remediation Summary

Represents the stable run-level summary used for operator review and final reporting.

Fields:
- `targetWorkflowId`
- `targetRunId`
- `resultingTargetRunId`
- `phase`
- `mode`
- `authorityMode`
- `actionsAttempted`
- `resolution`
- `lockConflicts`
- `approvalCount`
- `evidenceDegraded`
- `escalated`
- `unavailableEvidenceClasses`
- `fallbacksUsed`
- `repair`
- `prevention`
- `decisionLogRef`
- `finalAuditRef`
- `lockRelease`

Relationships:
- Belongs to one remediation evidence set.
- References the decision log artifact.
- References repair and prevention evidence when applicable.

Validation rules:
- Target workflow and pinned run identity are required.
- Resolution and phase must be bounded values.
- Repair/prevention sections must use bounded status values and safe references.

## Queryable Remediation Audit Event

Represents compact control-plane evidence for a side-effecting remediation action decision.

Fields:
- `eventId`: deterministic or durable event identifier.
- `eventType`: remediation action or related bounded event type.
- `actorUser`: operator actor when applicable.
- `executionPrincipal`: remediation execution principal.
- `remediationWorkflowId`
- `remediationRunId`
- `targetWorkflowId`
- `targetRunId`
- `actionKind`
- `riskTier`
- `approvalDecision`
- `timestamp`
- `metadata`: bounded query/display metadata.

Relationships:
- Belongs to one remediation run.
- References one target execution and target run.
- May correspond to one decision log entry and action artifact set.

Validation rules:
- Must be queryable by remediation identity and target identity.
- Must not contain artifact bodies, logs, secrets, raw storage keys, presigned URLs, or absolute local paths.
- Must be idempotent for retried publication of the same side-effecting decision.

## Target-Side Remediation Annotation

Represents supplemental evidence on the mutated target side.

Fields:
- `targetWorkflowId`
- `targetRunId`
- `remediationWorkflowId`
- `remediationRunId`
- `actionKind`
- `decision`
- `artifactRefs`
- `timestamp`
- `metadata`

Relationships:
- Belongs to the target execution evidence surface.
- References the remediation evidence set.
- Does not replace subsystem-native control, continuity, diagnostic, or summary artifacts.

Validation rules:
- Must be supplemental and non-destructive.
- Must preserve target-native evidence.
- Must use safe refs and bounded metadata only.

## State Transitions

1. `collecting_evidence`: context artifacts are created or evidence degradation is recorded.
2. `diagnosing`: remediation plan and candidate decisions are recorded.
3. `acting`: action request/result and audit events are published for side-effecting actions.
4. `verifying`: verification artifacts and target-side annotations are linked when applicable.
5. `resolved`, `escalated`, or `failed`: summary and decision log artifacts are finalized with bounded outcome state.
