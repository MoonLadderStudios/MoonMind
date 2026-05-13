# Data Model: Operator Observability Diagnostics

## Execution Target Diagnostics

Represents compact operator-visible diagnostics on an execution detail response.

Fields:
- `targets`: ordered list of target diagnostic entries.
- `recovery`: optional Resume provenance and failed Resume phase summary.
- `degradedReason`: optional bounded reason explaining incomplete target diagnostics.

Validation rules:
- Target entries must preserve objective versus step target meaning.
- Unknown attachment failure phases must degrade to a bounded operator-visible value rather than leaking raw provider text as control flow.
- Diagnostics must contain refs and metadata only, not binary payloads.

## Target Diagnostic Entry

Represents one objective or step target.

Fields:
- `targetKind`: `objective` or `step`.
- `stepId`: required for step targets, absent or null for objective targets.
- `label`: operator-visible target label.
- `attachments`: compact attachment metadata and artifact refs.
- `refs`: manifest, generated context, or other evidence refs.
- `failures`: bounded attachment failure diagnostics for the target.

Validation rules:
- Objective attachments must not be relabeled as step attachments.
- Step attachments must stay bound to their declared step.
- Empty targets may be shown when needed to distinguish missing attachments from unavailable diagnostics.

## Attachment Metadata

Represents one attachment reference visible to operators.

Fields:
- `artifactRef`: artifact or input ref.
- `filename`: optional display name.
- `contentType`: optional media type.
- `sizeBytes`: optional size.
- `previewAvailable`: optional preview capability hint.

Validation rules:
- Metadata must be bounded and must not inline image bytes or other binary payloads.
- Preview availability must not imply authorization bypass; normal artifact access policy applies.

## Diagnostic Ref

Represents target-related evidence.

Fields:
- `refKind`: evidence kind such as `attachment_manifest` or `generated_context`.
- `artifactRef`: optional artifact reference.
- `path`: optional path-like reference when no artifact ref is available.

Validation rules:
- At least one of `artifactRef` or `path` must be present.
- Refs should remain compact and inspectable.

## Attachment Failure

Represents a bounded failure for one target.

Fields:
- `phase`: `upload`, `validation`, `materialization`, `context_generation`, or `degraded`.
- `message`: sanitized operator-visible message.
- `evidenceRef`: optional diagnostic artifact reference.

Validation rules:
- Unknown raw phases map to `degraded`.
- Messages must be sanitized and must not expose credentials.

## Recovery Provenance

Represents Resume-related evidence shown in task detail.

Fields:
- `resumed`: whether this execution is a resumed execution or has Resume evidence.
- `sourceWorkflowId`: optional source workflow ID.
- `sourceRunId`: optional source run ID.
- `checkpointRef`: optional resume checkpoint ref.
- `preservedSteps`: prior steps reused from the source run.
- `failedResumePhase`: optional phase for failed Resume attempts.

Validation rules:
- `failedResumePhase` must be one of `checkpoint_validation`, `workspace_restoration`, `preserved_output_injection`, or `failed_step_execution`.
- Preserved steps must retain source workflow/run provenance when available.

## Preserved Step

Represents one prior completed step reused by Resume.

Fields:
- `logicalStepId`: stable step identifier.
- `title`: optional display title.
- `sourceAttempt`: optional source attempt number.
- `sourceWorkflowId`: optional source workflow ID.
- `sourceRunId`: optional source run ID.

Validation rules:
- A preserved step must identify a logical step.
- Preserved steps must not be displayed as newly executed work.
