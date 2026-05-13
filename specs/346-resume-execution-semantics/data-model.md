# Data Model: Resume Execution Semantics

## Failed Source Run

- `workflowId`: stable logical workflow ID of the failed source execution.
- `runId`: exact Temporal/source run ID being resumed.
- `taskInputSnapshotRef`: artifact ref for the immutable original task input snapshot.
- `planRef`: optional artifact ref for the source execution plan.
- `planDigest`: optional digest binding Resume to the same plan identity.
- `failedStepId`: logical step ID of the failed step.
- `failedStepAttempt`: source attempt number of the failed step.
- `resumeCheckpointRef`: artifact ref for the source resume checkpoint.

Validation rules:
- `workflowId`, `runId`, `taskInputSnapshotRef`, `failedStepId`, and `resumeCheckpointRef` are required and non-empty.
- At least one plan identity field, `planRef` or `planDigest`, must be present when checkpoint validation depends on plan matching.
- Source workflow and run IDs must match the checkpoint source exactly.

## Resume Checkpoint

- `schemaVersion`: checkpoint contract version, currently `v1`.
- `source`: source workflow/run identity.
- `taskInputSnapshotRef`: immutable authored input snapshot ref.
- `planRef` / `planDigest`: plan identity.
- `failedStep`: logical failed step identity, order, attempt, and optional title.
- `preservedSteps`: ordered completed steps eligible for preservation.
- `preparedArtifactRefs`: compact prepared input refs available for safe reuse.
- `resumeWorkspace`: workspace, branch, commit, checkpoint ref, or equivalent recoverable pre-failed-step state.

Validation rules:
- Must remain compact and ref-based; no large inline checkpoint payloads.
- Must include workspace or equivalent recoverable state evidence.
- Invalid, unauthorized, stale, corrupted, or inconsistent checkpoints block Resume before failed-step execution.

## Preserved Step

- `logicalStepId`: source logical step ID.
- `order`: original step order.
- `status`: completed source status such as `succeeded` or `skipped`.
- `sourceAttempt`: source attempt number.
- `artifacts`: semantic output refs needed by downstream steps.
- `stateCheckpointRef`: workspace/state checkpoint ref associated with the preserved step.
- `preservedFrom`: resumed-run provenance with source workflow ID, source run ID, logical step ID, and attempt.

Validation rules:
- A preserved step must have source provenance.
- A preserved step must not increment a new resumed-run attempt.
- A preserved step without required output refs or state checkpoint evidence is not eligible for preservation.

## Preserved Output

- `outputPrimary`: primary semantic output ref when available.
- `outputSummary`: summary output ref when available.
- `outputDetails`, `outputReport`, or other bounded artifact refs supported by existing step artifact shape.

Validation rules:
- Preserved outputs are refs, not inline large payloads.
- Failed and downstream steps must observe preserved outputs through the same step input contract they would receive in a continuous run.

## Resumed Run

- `resumeSource`: compact execution metadata derived from the checkpoint.
- `task.recovery`: canonical `resume_from_failed_step` recovery provenance.
- `task.resume`: failed-step resume ref with source identity, failed step, checkpoint, snapshot, and plan identity.
- Step ledger rows: preserved rows for prior completed steps, fresh rows for retried and later steps.

State transitions:
1. Created from a failed source run after checkpoint validation.
2. Initializes with preserved prior step rows.
3. Restores workspace state before failed-step execution.
4. Retries the failed step as the first new attempt.
5. Continues downstream normally after failed-step success.
6. Records fresh resumed-run evidence for retried and later steps.
7. Fails explicitly before failed-step execution if restoration is incomplete or inconsistent.
