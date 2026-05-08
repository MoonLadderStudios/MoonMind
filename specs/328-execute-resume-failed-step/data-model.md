# Data Model: Execute Resume From the Failed Step Only

## Resumed Execution

Represents the new execution created when a user chooses failed-step Resume.

Fields:

- `workflowId`: new resumed execution workflow identity.
- `runId`: new resumed execution run identity when available.
- `resumeSource`: compact source provenance and checkpoint metadata.
- `parameters`: original source task parameters minus stale task-run identity fields.

Validation rules:

- Must be created only after checkpoint validation succeeds.
- Must reuse the original task input snapshot.
- Must not include edited task authoring fields supplied by the Resume request.
- Must not be created when checkpoint validation or restoration fails.

## Resume Source

Compact provenance carried by the resumed execution.

Fields:

- `kind`: constant `resume_from_failed_step`.
- `sourceWorkflowId`: failed source workflow ID.
- `sourceRunId`: failed source run ID.
- `sourceTaskInputSnapshotRef`: original source task input snapshot ref.
- `sourcePlanRef`: source plan ref when available.
- `sourcePlanDigest`: source plan digest when available.
- `failedStepId`: logical ID of the failed step to retry.
- `failedStepAttempt`: source failed-step attempt number.
- `resumeCheckpointRef`: durable checkpoint evidence ref.
- `preservedSteps[]`: completed prior steps to materialize as preserved progress.

Validation rules:

- Source workflow and run ID must match the checkpoint source.
- Snapshot identity must match the source execution memo.
- Plan identity or digest must match when source metadata exists.

## Resume Checkpoint

Durable evidence used to restore the resumed execution before new work begins.

Fields:

- `source`: source workflow ID and run ID.
- `taskInputSnapshotRef`: original task input snapshot ref.
- `planRef` or `planDigest`: plan identity evidence.
- `failedStep`: logical ID, order, attempt, and optional title of the failed step.
- `preservedSteps[]`: completed prior source steps that can be reused.
- `preparedArtifactRefs[]`: prepared input refs available for reuse.
- `resumeWorkspace`: compact workspace, branch, commit, or equivalent restoration evidence.

Validation rules:

- Must include plan identity or digest.
- Must include workspace restoration evidence.
- Must keep large or binary content behind refs.
- Must fail validation when source, snapshot, plan, or failed-step identity is inconsistent.

## Preserved Step

A source-run completed step represented as reused progress in the resumed run.

Fields:

- `logicalStepId`: source logical step ID.
- `order`: source step order.
- `status`: terminal source status eligible for preservation.
- `sourceAttempt`: source attempt number.
- `artifacts`: compact output refs needed by downstream contracts.
- `stateCheckpointRef`: state checkpoint ref for the preserved step boundary.
- `preservedFrom`: operator-visible provenance on the resumed step row.

Validation rules:

- Must include at least one semantic artifact ref.
- Must include state checkpoint evidence.
- Must carry source workflow ID, source run ID, logical step ID, and attempt provenance.
- Must not be newly executed by the resumed run.

## Retried Failed Step

The failed source step executed as the first new step in the resumed run.

Validation rules:

- Must not execute until checkpoint validation and workspace restoration have succeeded.
- Must receive preserved outputs needed to match continuous-run contracts.
- Must produce fresh resumed-run ledger rows, artifacts, and checkpoints.

## State Transitions

1. Failed source execution has valid checkpoint evidence.
2. User submits failed-step Resume without editable task input.
3. Service validates checkpoint source, snapshot, plan, failed-step identity, preserved refs, and workspace evidence.
4. Resumed execution is created with `resumeSource`.
5. `MoonMind.Run` restores workspace or equivalent runtime state before the failed step.
6. Completed prior steps are materialized as preserved rows with provenance.
7. Failed step is the first newly executed step.
8. Later steps execute normally and produce fresh resumed-run evidence.
9. Invalid validation or restoration fails explicitly before new step execution.
