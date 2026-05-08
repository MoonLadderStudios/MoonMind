# Data Model: Show Attachment and Recovery Diagnostics By Target

## Task Target

Represents the owner of attachment-related metadata or diagnostics.

- `targetKind`: `objective` or `step`.
- `stepId`: stable logical step identifier when `targetKind` is `step`.
- `stepTitle`: operator-facing step title when available.
- `attachmentCount`: number of attachment metadata items associated with this target.
- `hasDiagnostics`: whether any diagnostic evidence exists for this target.

Validation rules:

- Objective targets must not include `stepId`.
- Step targets must include a stable step identifier or documented degraded reason.
- Empty targets are valid and distinct from missing diagnostics.

## Target Attachment Metadata

Represents safe, bounded metadata for one input attachment.

- `artifactRef` or `artifactId`: compact artifact reference.
- `filename`: display filename when available.
- `contentType`: safe content type label when available.
- `sizeBytes`: byte size when available.
- `target`: owning Task Target.
- `previewAvailable`: whether normal artifact preview/download rules can apply.

Validation rules:

- Metadata must not include raw binary content.
- Metadata must not bypass artifact authorization or redaction.
- Target ownership must come from the task contract or prepared context evidence, not filename inference.

## Target Diagnostic Reference

Represents a compact ref or summary for target-owned evidence.

- `target`: owning Task Target.
- `refKind`: `attachment_manifest`, `generated_context`, `runtime_diagnostics`, `step_artifact`, or `resume_checkpoint`.
- `artifactRef`: compact artifact reference when available.
- `path`: workspace-local path only when safe and already exposed by runtime diagnostics.
- `degradedReason`: bounded reason when the ref should exist but is unavailable.

Validation rules:

- Large bodies remain in artifacts; task detail receives refs and bounded metadata.
- Missing refs must be represented as degraded evidence only when a target had expected evidence.

## Attachment Failure Diagnostic

Represents a bounded attachment-related failure.

- `target`: affected Task Target.
- `phase`: `upload`, `validation`, `materialization`, or `context_generation`.
- `message`: bounded operator-facing message.
- `evidenceRef`: optional artifact or diagnostics ref.

Validation rules:

- Every failure must include exactly one affected target and exactly one bounded phase.
- Unknown raw event names must be normalized or exposed as degraded evidence, not silently omitted.

## Recovery Provenance

Represents failed-step Resume evidence shown on task detail.

- `sourceWorkflowId`: source execution workflow ID.
- `sourceRunId`: source execution run ID.
- `checkpointRef`: resume checkpoint ref when available.
- `preservedSteps`: completed prior steps reused from the source execution.
- `failedResumePhase`: optional phase when a Resume attempt failed: `checkpoint_validation`, `workspace_restoration`, `preserved_output_injection`, or `failed_step_execution`.

Validation rules:

- Preserved steps must retain source workflow ID, run ID, logical step ID, and attempt provenance.
- Failed Resume phase labels must be bounded and operator-facing.

## State Transitions

- Target diagnostics can be absent when no target-aware evidence exists.
- Target diagnostics can be populated when snapshot, prepared context, artifact, or ledger evidence is available.
- Target diagnostics can be degraded when evidence was expected but missing, unauthorized, or failed before generation.
- Recovery provenance can be unavailable, available, or degraded depending on Resume evidence.
