# Data Model: Preserve Attachment Bindings in Snapshots and Reruns

## Task Input Snapshot

Represents the saved source of truth for reconstructing edit and rerun drafts.

Relevant fields:
- `snapshotVersion`
- `source.kind`
- `draft.task`
- `draft.task.inputAttachments`
- `draft.task.steps[n].inputAttachments`
- `draft.task.steps[n].id`
- `draft.task.steps[n].instructions`
- `draft.repository`
- `draft.targetRuntime`
- `attachmentRefs`

Validation:
- Objective-scoped refs must remain under `draft.task.inputAttachments`.
- Step-scoped refs must remain under the corresponding `draft.task.steps[n].inputAttachments`.
- Step identity/order must be preserved well enough to bind step-scoped refs to the intended step.
- Missing binding data for a task that has attachment refs is a reconstruction failure.

## Attachment Ref

Represents an already uploaded input attachment selected for a task target.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`
- `targetKind`
- optional `stepId`
- optional `stepOrdinal`

Validation:
- `artifactId`, `filename`, and `contentType` must be non-empty when present in persisted refs.
- `targetKind` is `objective` or `step`.
- Step refs must identify a step through snapshot position and, when available, stable step id.
- Attachment metadata may aid diagnostics but cannot override target binding stored in the task snapshot.

## Temporal Submission Draft

Frontend reconstruction model used to prefill Create-page edit and rerun state.

Relevant fields:
- `runtime`
- `providerProfile`
- `model`
- `effort`
- `repository`
- `startingBranch`
- `targetBranch`
- `publishMode`
- `taskInstructions`
- `steps`
- `inputAttachments`
- `steps[n].inputAttachments`
- `appliedTemplates`

Validation:
- Persisted attachment refs are represented separately from new local files.
- Unchanged persisted refs are included in edit/rerun submissions unless explicitly removed by the user.
- New local files must still go through the existing upload flow before submission.

## Attachment Preview State

Represents user-visible attachment rows on task detail, edit, and rerun surfaces.

Fields:
- `targetKind`
- `stepId` or `stepOrdinal` for step-scoped targets
- `filename`
- `contentType`
- `sizeBytes`
- `artifactId`
- optional preview status
- optional download action

Validation:
- Preview grouping is target-aware.
- Preview failure does not remove metadata or download actions.
- Filenames are display labels only and never determine target binding.
