# Data Model: Gate Resume on Durable Checkpoint Evidence

Traceability: MM-633, `spec.md` FR-001 through FR-013.

## Resume Eligibility Result

Represents backend-computed availability for failed-step Resume.

Fields:
- `available`: boolean. True only when every required evidence category is present and valid.
- `checkpointRef`: durable checkpoint artifact ref when evidence exists.
- `failedStepId`: logical failed step identity from the source ledger or validated checkpoint.
- `sourceRunId`: pinned source run id.
- `disabledReason`: bounded reason when unavailable, such as `original_task_input_snapshot_missing`, `resume_checkpoint_missing`, `failed_step_identity_missing`, `completed_step_refs_missing`, `workspace_checkpoint_missing`, `plan_identity_missing`, `checkpoint_unauthorized`, `checkpoint_corrupted`, or `checkpoint_inconsistent`.

Validation rules:
- Must be computed by backend code, not UI inference.
- `available=true` requires all Resume Evidence Bundle rules below to pass.
- Disabled reasons must not expose secrets or raw checkpoint payloads.

## Resume Evidence Bundle

Logical evidence set required before Resume can be offered or accepted.

Fields:
- `sourceWorkflowId`: source execution workflow id.
- `sourceRunId`: source execution run id.
- `taskInputSnapshotRef`: authoritative original task input snapshot ref.
- `failedStep`: failed-step ledger identity.
- `preservedSteps`: completed prior step refs and provenance.
- `preparedArtifactRefs`: prepared input refs safe to reuse.
- `workspaceCheckpoint`: workspace, branch, commit, or equivalent state checkpoint ref.
- `planRef` or `planDigest`: plan identity proving step graph compatibility.
- `resumeCheckpointRef`: durable artifact ref for the compact evidence bundle.

Validation rules:
- `sourceWorkflowId` and `sourceRunId` must match the failed source execution.
- `taskInputSnapshotRef` must match source execution memo/canonical snapshot state.
- `failedStep` must identify the last failed step in the source step ledger.
- Every completed step before the failed step that will be preserved must have recoverable output refs and state checkpoint evidence.
- `workspaceCheckpoint` cannot be empty for a valid Resume offer.
- At least one plan identity value must be present and must match the source execution plan identity.
- Large or binary state must be represented by refs, never inline bodies.

## Resume Checkpoint Artifact

Artifact-backed compact payload that can be hydrated before Resume execution.

State transitions:
- `candidate`: checkpoint write is being assembled from source execution evidence.
- `valid`: all evidence categories are present, compact, authorized, and internally consistent.
- `invalid`: evidence is missing, stale, unauthorized, corrupted, or inconsistent and cannot be used for Resume.
- `consumed`: a valid Resume request used this checkpoint to create a linked follow-up execution.

Validation rules:
- Checkpoint writes must be idempotent for the same source workflow/run and failed-step identity.
- Repeated writes for unchanged evidence must resolve to the same checkpoint ref or an equivalent deterministic replacement.
- Hydration failures must produce unavailable Resume status before step execution.

## Preserved Step Evidence

Represents one completed prior step that may be materialized as preserved progress.

Fields:
- `logicalStepId`
- `order`
- `status`
- `sourceAttempt`
- `artifacts`
- `stateCheckpointRef` or equivalent state evidence
- `preservedFrom` provenance when materialized in the resumed run

Validation rules:
- Must include at least one semantic output ref.
- Must not embed large output content inline.
- Must point back to the pinned source workflow/run and source attempt.
- Must not be materialized if the source ledger row is not completed or does not precede the failed step.

## Relationships

- Failed Source Execution has one Original Task Input Snapshot.
- Failed Source Execution may have one valid Resume Eligibility Result at a time.
- Resume Eligibility Result references one Resume Checkpoint Artifact.
- Resume Checkpoint Artifact contains one failed step and zero or more Preserved Step Evidence entries.
- Resumed Execution references the pinned Failed Source Execution and Resume Checkpoint Artifact.
