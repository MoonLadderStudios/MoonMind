# Data Model: Resume from Last Failed Step

## Resume Capability

Represents whether a failed task may show failed-step Resume.

Fields:
- `canResumeFromFailedStep`: boolean; true only when the source execution is failed or otherwise explicitly eligible and checkpointed progress is restorable.
- `disabledReasons.canResumeFromFailedStep`: optional stable reason string for unavailable Resume.
- `resumeCheckpointRef`: optional compact ref to checkpoint evidence when available and authorized for detail display.

Validation rules:
- Must be false for non-`MoonMind.Run` workflows.
- Must be false for running, completed, canceled, or paused-lifecycle-only states unless the state is explicitly resume-eligible by policy.
- Must be false when the original task input snapshot is missing or degraded beyond authoritative resume use.
- Must not reuse lifecycle `canResume` semantics.

## Resume Source

Compact provenance carried by the resumed execution.

Fields:
- `kind`: constant `resume_from_failed_step`.
- `sourceWorkflowId`: non-empty source workflow identity.
- `sourceRunId`: non-empty source run identity.
- `sourceTaskInputSnapshotRef`: non-empty artifact/ref ID for the original task input snapshot.
- `sourcePlanRef`: optional source plan ref.
- `sourcePlanDigest`: optional source plan digest.
- `failedStepId`: non-empty logical step ID.
- `failedStepAttempt`: integer greater than or equal to 1.
- `resumeCheckpointRef`: non-empty resume checkpoint evidence ref.

Validation rules:
- `sourceWorkflowId` and `sourceRunId` are both required.
- `sourceTaskInputSnapshotRef` must match the source execution memo/evidence.
- If the source has plan evidence, the checkpoint and resumed execution must match by ref or digest.
- No edited task payload fields may accompany the Resume request.

## Resume Checkpoint

Durable evidence proving completed progress can be preserved.

Fields:
- `schemaVersion`: `v1`.
- `source.workflowId`: source workflow identity.
- `source.runId`: source run identity.
- `taskInputSnapshotRef`: original task input snapshot ref.
- `planRef`: optional plan ref.
- `planDigest`: optional plan digest.
- `failedStep.logicalStepId`: failed logical step ID.
- `failedStep.order`: failed step order.
- `failedStep.attempt`: failed attempt number.
- `failedStep.title`: failed step title.
- `preservedSteps[]`: ordered completed prior steps to materialize as reused.
- `preservedSteps[].logicalStepId`: source logical step ID.
- `preservedSteps[].order`: source step order.
- `preservedSteps[].status`: terminal source status, normally `succeeded` or `skipped` when allowed.
- `preservedSteps[].sourceAttempt`: source attempt number.
- `preservedSteps[].artifacts`: semantic output refs required by downstream steps.
- `preparedArtifactRefs[]`: task or step prepared input refs reused by the resumed execution.
- `resumeWorkspace`: workspace, branch, commit, or equivalent state before the failed step.

Validation rules:
- Must identify the same source workflow/run as the Resume request.
- Must identify exactly one failed step to retry.
- Must include every completed prior step that will be preserved.
- Each preserved step must include enough refs for downstream contracts.
- Workspace/branch/commit state is required when the task mutates working state.
- Validation failure must prevent new failed-step execution.

## Preserved Step

Step row materialized in the resumed execution without re-executing source work.

Fields:
- `logicalStepId`: logical step ID in the resumed execution.
- `status`: planned canonical value or metadata indicating preserved-from-source.
- `sourceWorkflowId`: source workflow identity.
- `sourceRunId`: source run identity.
- `sourceAttempt`: source step attempt.
- `artifacts`: reused semantic output refs.
- `summary`: operator-visible preserved/reused summary.

Validation rules:
- Must not increment execution attempts as newly run work.
- Must preserve enough provenance for task details and final verification.
- Must not be displayed as freshly executed by the resumed execution.

## Related Run

Operator-visible relationship between source and resumed executions.

Fields:
- `workflowId`: related execution workflow identity.
- `runId`: related execution run identity when known.
- `relationship`: `Resumed from failed step` for source-to-resumed display, and source/original failed run label for resumed-to-source display.
- `status`: related execution status.
- `createdAt`: related execution creation time.
- `href`: task detail route.

Validation rules:
- Source detail should show each resumed follow-up execution.
- Resumed detail should show the original failed source.
- Related-run computation must honor ownership/authorization boundaries.

## State Transitions

1. Source failed task becomes resume-eligible when original task snapshot, failed step identity, required refs, and checkpoint evidence are present and authorized.
2. Operator requests failed-step Resume.
3. System validates source state, source identity, snapshot, plan evidence, checkpoint, prepared refs, output refs, and workspace/branch state.
4. On validation failure, Resume fails explicitly and no new step executes.
5. On validation success, system creates a linked follow-up execution with `resumeSource` metadata and original task input snapshot.
6. Resumed execution materializes preserved prior steps, restores state, starts at the failed step, and then proceeds normally.
7. Source and resumed task details expose related runs and preserved-step provenance.
