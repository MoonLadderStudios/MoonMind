# Data Model: Persist Authoritative Task Snapshots

## Task Input Snapshot

- `snapshotVersion`: Required integer version for the snapshot payload.
- `source.kind`: Required source classification such as `create`, `edit`, or `rerun`.
- `source.sourceWorkflowId` / `source.sourceRunId`: Optional source identity for rerun-derived snapshots.
- `draft.taskShape`: Required classification of the authored task shape.
- `draft.repository`: Optional authored repository selection.
- `draft.targetRuntime`: Optional authored runtime selection.
- `draft.requiredCapabilities`: Authored capability selection.
- `draft.task`: Required task-shaped authored payload, including objective text, steps, ordering, preset metadata, dependencies, and structured inputs.
- `attachmentRefs`: Bounded attachment ref metadata associated with the snapshot.
- `lineage`: Reserved compact provenance metadata.
- `excluded`: Explicit non-editable creation-time controls.

Validation rules:
- An authoritative reconstruction descriptor requires an artifact ref.
- Missing authoritative snapshot refs disable edit/rerun actions.
- Snapshot payloads remain artifact-backed and are not embedded in workflow history.

## Task Input Snapshot Descriptor

- `available`: Whether an authoritative snapshot artifact ref exists.
- `artifactRef`: Snapshot artifact ref when available.
- `snapshotVersion`: Snapshot version when available.
- `sourceKind`: Snapshot source kind when available.
- `reconstructionMode`: `authoritative`, `degraded_read_only`, or `unavailable`.
- `disabledReasons`: Operator-facing reason map when reconstruction is degraded or unavailable.
- `fallbackEvidenceRefs`: Non-authoritative refs useful for diagnostics only.

State transitions:
- Missing snapshot plus fallback evidence -> `degraded_read_only`.
- Missing snapshot without fallback evidence -> `unavailable`.
- Present snapshot artifact ref -> `authoritative`.

## Execution Action Capabilities

- `canEditForRerun`: Enabled only for eligible terminal `MoonMind.Run` executions with an authoritative task input snapshot and task editing enabled.
- `canRerun`: Enabled only for eligible terminal `MoonMind.Run` executions with an authoritative task input snapshot and task editing enabled.
- `canResumeFromFailedStep`: Enabled only for failed `MoonMind.Run` executions with an authoritative task input snapshot and a resume checkpoint.
- `disabledReasons`: Uses `original_task_input_snapshot_missing` when snapshot absence blocks edit, rerun, update inputs, or failed-step resume.
