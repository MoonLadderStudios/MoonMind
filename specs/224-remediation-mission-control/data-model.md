# Data Model: Remediation Mission Control Surfaces

## Remediation Link Summary

Compact API/read-model item for one remediation relationship.

Fields:
- `remediationWorkflowId`: logical workflow ID of the remediation task.
- `remediationRunId`: current or pinned run ID of the remediation task.
- `targetWorkflowId`: logical workflow ID of the target execution.
- `targetRunId`: pinned target run ID.
- `mode`: remediation mode such as `snapshot`, `live_follow`, or `snapshot_then_follow`.
- `authorityMode`: remediation authority mode such as `observe_only`, `approval_gated`, or `admin_auto`.
- `status`: compact remediation phase or task status.
- `activeLockScope`: nullable current lock scope.
- `activeLockHolder`: nullable current lock holder.
- `latestActionSummary`: nullable compact latest action summary.
- `resolution`: nullable final or current remediation resolution.
- `contextArtifactRef`: nullable artifact ID for `reports/remediation_context.json`.
- `createdAt`: creation timestamp.
- `updatedAt`: latest link/update timestamp.

Validation:
- Workflow and run IDs are identifiers only; they are not presigned URLs or storage keys.
- A read response may omit optional fields, but it must preserve enough identity to link both directions.
- Lists are bounded for task-detail rendering.

## Remediation Evidence Item

Operator-facing metadata for one linked remediation evidence artifact.

Fields:
- `artifactId`: existing artifact identifier.
- `artifactType`: one of `remediation.context`, `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.verification`, `remediation.summary`, or an unknown remediation-prefixed type.
- `label`: human-readable label derived from artifact metadata or known remediation type.
- `previewUrl` / `downloadUrl`: existing artifact presentation routes when authorized.
- `createdAt`: artifact creation timestamp when available.
- `degradedReason`: optional reason when the expected artifact is missing or unavailable.

Validation:
- Evidence items never include storage backend keys, local filesystem paths, presigned URLs, raw log bodies, or artifact byte content.
- Unknown remediation artifact types render as evidence artifacts with safe generic labels.

## Remediation Approval State

Current operator handoff state for approval-gated remediation.

Fields:
- `requestId`: stable action request identifier or artifact ID.
- `actionKind`: typed action kind.
- `riskTier`: action risk tier.
- `preconditions`: bounded text or structured summary.
- `blastRadius`: bounded text or structured summary.
- `decision`: `pending`, `approved`, `rejected`, `expired`, or `not_required`.
- `decisionActor`: nullable user/principal that decided.
- `decisionAt`: nullable decision timestamp.
- `canDecide`: whether the current operator may approve/reject.
- `auditRef`: nullable artifact or audit event reference.

Validation:
- Side-effecting decisions require a trusted server route.
- Read-only users must receive `canDecide = false` and no enabled decision action.
- Approval metadata is bounded and does not include raw credentials or raw command output.

## Remediation Create Draft

Mission Control state expanded into the canonical create route.

Fields:
- `targetWorkflowId`: target execution workflow ID from the current detail page.
- `targetRunId`: pinned current run ID when known.
- `selectedSteps`: selected logical step IDs, attempts, or task run IDs.
- `authorityMode`: `observe_only`, `approval_gated`, or `admin_auto` when supported.
- `mode`: `snapshot`, `live_follow`, or `snapshot_then_follow`.
- `actionPolicyRef`: selected action policy reference.
- `evidencePolicy`: bounded evidence classes and tail-line hints.

Validation:
- The submitted shape must normalize into `initialParameters.task.remediation`.
- The target workflow ID is always taken from trusted page/API state, not free-form prompt text.
- Evidence preview is descriptive and ref-based; it does not fetch raw artifact bodies during draft creation.
