# Data Model: Expose Distinct Full Retry Recovery Actions

Traceability: MM-632, FR-001 through FR-013.

## Failed Execution

Represents the source execution that exposes recovery choices after failure or another terminal state.

Fields:

- `workflowId`: Stable source execution identity.
- `runId`: Latest source run identity.
- `state`: Current normalized workflow state.
- `workflowType`: Must be a task workflow type eligible for task recovery actions.
- `taskInputSnapshotRef`: Reference to the authoritative original task input snapshot.
- `resumeCheckpointRef`: Optional durable evidence reference for failed-step Resume.
- `stepLedgerRef` or step ledger projection: Durable source of failed-step and completed-step evidence when Resume is available.

Validation rules:

- Edit task and Rerun require an authoritative task input snapshot.
- Resume requires a task input snapshot plus valid failed-step checkpoint evidence.
- Recovery actions must not mutate the failed execution's state, snapshot, step ledger, artifacts, or checkpoints.

## Recovery Action Capability

The user-visible availability state for one recovery action.

Fields:

- `canEditForRerun`: True only when editable full retry is available.
- `canRerun`: True only when exact full rerun is available.
- `canResumeFromFailedStep`: True only when failed-step Resume is available.
- `disabledReasons`: Optional machine-readable reason per unavailable action.

Validation rules:

- Capability fields are independent; one recovery action must not imply another.
- Disabled reasons must distinguish unsupported workflow type, disabled feature flag, ineligible state, missing original task input snapshot, and missing or invalid Resume evidence.
- UI must render each action according to its own capability field.

## Edited Full Retry

A new from-beginning execution created after the user edits the original task input.

Fields:

- `sourceWorkflowId`: Failed source execution identity.
- `sourceRunId`: Failed source run identity.
- `newWorkflowId`: New execution identity, which may differ from the source.
- `newTaskInputSnapshotRef`: Authoritative snapshot for the edited retry.
- `sourceKind`: `edit` or equivalent edited-full-retry provenance.

Validation rules:

- Must permit normal task input edits subject to normal validation.
- Must start from the beginning.
- Must not import completed progress or preserved steps from the source execution.
- Must preserve source execution immutability.

## Exact Full Rerun

A new from-beginning execution using the original task input unchanged.

Fields:

- `sourceWorkflowId`: Failed source execution identity.
- `sourceRunId`: Failed source run identity.
- `originalTaskInputSnapshotRef`: Snapshot reused for exact rerun.
- `rerunSource`: Compact provenance linking the new execution to the source.

Validation rules:

- Must reject or ignore edited task/input mutation fields.
- Must start from the beginning.
- Must not import Resume checkpoint state, completed progress, preserved steps, or partial work.
- Must preserve source execution immutability.

## Resume Evidence

Durable evidence required before failed-step Resume can preserve completed work.

Fields:

- `resumeCheckpointRef`: Reference to checkpoint evidence.
- `failedStepId`: Failed step selected for retry.
- `preservedSteps`: Completed prior steps that may be reused only by Resume.
- `sourceWorkflowId`: Source execution identity pinned by the checkpoint.
- `sourceRunId`: Source run identity pinned by the checkpoint.
- `taskInputSnapshotRef`: Original input snapshot associated with the checkpoint.

Validation rules:

- Resume evidence must be present, authorized, current for the source execution, and consistent with the source step graph.
- Resume must not accept edited task input fields.
- Resume-only evidence must not be imported by Edit task or exact Rerun.

## State Transitions

```text
Failed Execution
├── Edit task -> Edited Full Retry -> New from-beginning execution with edited snapshot
├── Rerun -> Exact Full Rerun -> New from-beginning execution with original snapshot unchanged
└── Resume -> Failed-step Resume -> Linked execution preserving prior completed progress
```

Invalid transitions:

- Exact Rerun with edited task/input mutation fields.
- Resume with edited task/input mutation fields.
- Edit task or Rerun importing `resumeCheckpointRef`, `resumeSource`, or preserved completed steps.
- Any recovery action mutating the failed source execution.
