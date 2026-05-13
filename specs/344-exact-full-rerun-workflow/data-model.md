# Data Model: Exact Full Rerun Workflow

## Failed Execution

Represents the source execution selected for exact full rerun.

Fields:

- `workflowId`: Stable workflow identifier for the source execution.
- `runId`: Concrete run identifier for the failed source run.
- `state`: Must be an eligible terminal state for Rerun.
- `taskInputSnapshotRef`: Reference to the authoritative original task input snapshot.
- `inputArtifactRef`: Existing input artifact reference, when present.
- `planArtifactRef`: Existing plan artifact reference, when present.
- `resumeCheckpointRef`: Existing failed-step resume checkpoint reference, if any; must not carry into exact rerun.
- `completedProgress`: Any completed stages or preserved outputs on the source; must not carry into exact rerun.

Validation rules:

- Exact rerun is unavailable when `taskInputSnapshotRef` is missing or unauthorized.
- Exact rerun provenance must use the source `workflowId` and `runId`.
- Existing resume/checkpoint state is source evidence only and is not exact rerun input.

## Original Task Input Snapshot

Represents the authoritative authored task input for recovery actions.

Fields:

- Objective/task instructions.
- Objective-scoped attachment refs.
- Step text, order, identity, and step-scoped attachment refs.
- Runtime and publish selections.
- Repository and single authored branch selection.
- Preset application metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Dependency declarations.

Validation rules:

- Exact full rerun reuses this snapshot unchanged.
- Snapshot reconstruction must not depend on current live preset catalog state.
- Attachment-aware executions without a reconstructible snapshot are degraded/blocked.

## Exact Full Rerun Execution

Represents the new execution created by the direct Rerun action.

Fields:

- `workflowId`: New execution workflow identifier.
- `runId`: New execution run identifier once available.
- `taskInputSnapshotRef`: The unchanged source snapshot reference or a verified equivalent artifact whose content and lineage are unchanged.
- `recovery.kind`: `exact_full_rerun`.
- `recovery.sourceWorkflowId`: Source failed execution workflow identifier.
- `recovery.sourceRunId`: Source failed execution run identifier.
- `rerunSource`: Existing source linkage metadata where applicable.

Validation rules:

- Must start from the beginning.
- Must not include `resume`, `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, `completedSteps`, or equivalent progress-import fields.
- Must not accept editable task mutation fields on the exact direct action path.

## State Transitions

```text
Failed Execution with authoritative snapshot
  └── user chooses Rerun
      └── validate eligibility and source identity
          └── create Exact Full Rerun Execution
              ├── recovery.kind = exact_full_rerun
              ├── source workflow/run IDs pinned
              ├── original task input snapshot reused unchanged
              └── full execution starts from beginning
```

Blocked transition:

```text
Failed Execution without authoritative snapshot
  └── user chooses Rerun
      └── explicit unavailable/degraded outcome
```
