# Data Model: Editable Full Retry Workflow

Traceability: MM-644, `spec.md` FR-001 through FR-012.

## Source Execution

Represents the failed `MoonMind.Run` execution selected for Edit task.

Fields and evidence:
- `workflowId`: stable source workflow identifier.
- `runId`: pinned source run identifier used for provenance.
- `state`: must be an eligible terminal state for edit-for-rerun; MM-644 primarily targets failed executions.
- `taskInputSnapshot`: authoritative original task input snapshot descriptor.
- `inputArtifactRef` / `planArtifactRef`: existing source evidence refs.
- `artifactRefs`: source-linked artifacts, including snapshot and input artifacts.
- `memo` / `searchAttributes`: compact execution metadata and recovery/snapshot refs.

Validation rules:
- Source workflow type must be `MoonMind.Run`.
- Edit task is available only when task editing is enabled and an authoritative task input snapshot is available and readable for the current user.
- Source evidence must remain unchanged when an edited full retry is created.

## Authoritative Task Input Snapshot

Represents the complete authored task input used to hydrate edit-for-rerun mode.

Fields:
- `snapshotVersion`: schema version.
- `source.kind`: `create`, `edit`, `rerun`, or `unknown` for existing descriptor compatibility.
- `source.sourceWorkflowId`: source execution workflow ID when snapshot belongs to a retry.
- `source.sourceRunId`: source execution run ID when snapshot belongs to a retry.
- `draft.repository`: repository selected in the authored task.
- `draft.targetRuntime`: runtime selected in the authored task.
- `draft.requiredCapabilities`: authored capability requirements.
- `draft.task`: task payload used to hydrate the Create page.
- `draft.authoredTaskInput`: normalized authored task input snapshot.
- `attachmentRefs`: objective-scoped and step-scoped artifact refs.
- `excluded.schedule`: explanation for creation-only schedule fields.

Validation rules:
- Authoritative reconstruction requires a non-empty artifact ref.
- Attachment refs must retain target binding.
- If the snapshot cannot reconstruct the authored task, edit-for-rerun must be blocked or marked unavailable.

## Edited Full Retry Execution

Represents the new execution created from an edited retry submission.

Fields:
- `workflowId`: new execution ID distinct from the source terminal execution.
- `runId`: new run ID.
- `parameters`: edited task/repository/runtime/publish payload after normal authoring validation.
- `inputRef`: optional new input artifact ref for large or artifact-backed edited input.
- `taskInputSnapshot`: new authoritative snapshot of edited authoring state.
- `rerunSource` or equivalent provenance: source workflow and run IDs.
- `recovery.kind`: desired canonical value `edited_full_retry` when task-level provenance is represented.

Validation rules:
- Must start from the beginning.
- Must not carry `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, `completedSteps`, task `resume`, or prior task `recovery` from the source execution.
- Must receive its own snapshot when edited input is accepted.
- Must preserve MM-644 traceability in feature artifacts and final evidence.

## Recovery Provenance

Represents audit linkage between the edited full retry and the source execution.

Fields:
- `kind`: `edited_full_retry` for changed edited retries.
- `sourceWorkflowId`: source execution workflow ID.
- `sourceRunId`: source execution run ID.
- `requestedBy`: optional actor identifier.
- `requestedAt`: optional request timestamp.

Validation rules:
- `sourceWorkflowId` and `sourceRunId` must be non-empty.
- Edited full retry must not include failed-step Resume refs.
- Exact full rerun and edited full retry must remain distinguishable in persisted or response-visible evidence.

## State Transitions

```text
Failed source execution
  └─ user chooses Edit task when canEditForRerun is true
      └─ Create page opens edit-for-rerun from authoritative snapshot
          └─ user edits supported authoring fields
              └─ normal authoring validation passes
                  └─ RequestRerun accepted as edited full retry
                      ├─ new execution starts from beginning
                      ├─ new authoritative snapshot is written
                      ├─ edited_full_retry provenance pins source workflow/run
                      └─ source execution evidence remains immutable
```
