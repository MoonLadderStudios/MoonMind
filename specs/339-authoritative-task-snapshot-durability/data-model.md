# Data Model: Authoritative Task Snapshot Durability

## Authoritative Task Input Snapshot

Represents the durable authored input for a `MoonMind.Run` execution.

Fields:
- `snapshotVersion`: Schema version for snapshot readers and validators.
- `source.kind`: One of `create`, `edit`, `rerun`, or another explicit supported source kind.
- `source.sourceWorkflowId`: Present for snapshots derived from another execution.
- `source.sourceRunId`: Present for snapshots derived from another execution.
- `draft.taskShape`: Compact shape such as `multi_step`, `artifact_backed`, `template_derived`, `skill_only`, or `inline_instructions`.
- `draft.repository`: Authored repository selection.
- `draft.targetRuntime`: Authored runtime selection.
- `draft.requiredCapabilities`: Authored or compiled capability list.
- `draft.task`: Complete authored task payload, including objective text, step text, step identity/order, runtime/publish selections, repository/branch data, dependencies, preset metadata, pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- `largeContentRefs`: References to large authored content that cannot be safely inlined.
- `attachmentRefs`: Compact list of objective-scoped and step-scoped attachment refs with target binding metadata.
- `lineage`: Optional relationship metadata for derived snapshots.
- `excluded`: Explicit exclusions from editable reconstruction, such as schedule-only creation controls.

Validation rules:
- Snapshot version must be present and supported.
- `draft.task` must be non-empty for task-shaped submissions.
- Attachment refs must include enough target metadata to distinguish objective and step bindings.
- Snapshot reads must not require live preset catalog lookup.
- Missing required authored fields must produce explicit degraded or rejected recovery behavior.

## Execution Snapshot Descriptor

Operator-facing summary exposed on execution detail responses.

Fields:
- `available`: Whether an authoritative snapshot is available.
- `artifactRef`: Snapshot artifact ref when available.
- `snapshotVersion`: Snapshot schema version when available.
- `sourceKind`: Snapshot source kind.
- `reconstructionMode`: `authoritative`, `degraded_read_only`, or `unavailable`.
- `disabledReasons`: Reason map for unavailable draft or recovery behavior.
- `fallbackEvidenceRefs`: Diagnostic-only refs that cannot replace the authoritative snapshot.

Validation rules:
- Missing snapshots must not enable edit/rerun actions.
- Fallback refs may support diagnostics only; they must not silently reconstruct editable state.

## Recovery Action

Represents user intent to recover from a source execution.

Kinds:
- Exact full rerun: reuses the original snapshot unchanged and imports no completed progress.
- Edited full retry: creates a new execution and its own snapshot while preserving source evidence unchanged.
- Failed-step Resume: reuses the original snapshot unchanged and imports only checkpointed completed progress.

Validation rules:
- Recovery intent must be explicit.
- Resume must reject task input edits.
- Resume must validate checkpoint source workflow/run identity and task snapshot identity.
- Edited full retry must not mutate the source snapshot.

## Degraded Reconstruction State

Represents an execution whose authored task cannot be faithfully reconstructed.

Fields:
- `reason`: Machine-readable reason such as `original_task_input_snapshot_missing` or attachment binding reconstruction failure.
- `affectedActions`: Recovery actions blocked or restricted by degradation.
- `fallbackEvidenceRefs`: Optional diagnostic refs.

Validation rules:
- Attachment-aware missing or invalid snapshots must be degraded explicitly.
- The system must not silently drop, retarget, or synthesize attachments during recovery.
