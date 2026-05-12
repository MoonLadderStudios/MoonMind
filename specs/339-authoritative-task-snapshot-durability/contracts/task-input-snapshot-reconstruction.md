# Contract: Task Input Snapshot Reconstruction

## Scope

This contract defines MM-639 behavior for `MoonMind.Run` task-shaped submissions and recovery actions.

## Snapshot Persistence

When a `MoonMind.Run` task is submitted through a covered entrypoint, the system must persist one authoritative task input snapshot before edit/rerun/Resume actions depend on the execution.

Covered entrypoints:
- Task-shaped create submission through the API.
- Direct `MoonMind.Run` create submission with task parameters.
- Jira-Orchestrate child `MoonMind.Run` creation.
- Exact full rerun creation.
- Edited full retry creation.

Required persisted content:
- Objective text and objective-scoped attachment refs.
- Step text, identity, order, and step-scoped attachment refs.
- Runtime and publish selections.
- Repository and single authored branch selection.
- Dependency declarations.
- Preset application metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.

## Execution Detail Response

Execution detail responses must expose `taskInputSnapshot` with:

```json
{
  "available": true,
  "artifactRef": "art_snapshot_123",
  "snapshotVersion": 1,
  "sourceKind": "create",
  "reconstructionMode": "authoritative",
  "disabledReasons": {},
  "fallbackEvidenceRefs": []
}
```

When unavailable, `available` must be `false`, `reconstructionMode` must be `degraded_read_only` or `unavailable`, and disabled reasons must identify why editable reconstruction is unavailable.

## Recovery Action Rules

Exact full rerun:
- Reuses the original task input snapshot unchanged.
- Starts from the beginning.
- Imports no completed execution progress, preserved steps, or Resume checkpoint state.

Edited full retry:
- Opens from the original snapshot for authoring edits.
- Creates a new execution with its own authoritative snapshot.
- Does not mutate the source execution snapshot or evidence.

Failed-step Resume:
- Reuses the original task input snapshot unchanged.
- Rejects task, runtime, attachment, publish, branch, preset, or dependency edits.
- Requires checkpoint evidence whose task input snapshot ref matches the source execution snapshot ref.
- Imports only completed progress represented by validated checkpoint evidence.

## Degraded Attachment-Aware Reconstruction

If an attachment-aware execution lacks a reconstructible authoritative snapshot, the system must:
- Disable editable reconstruction actions that require the snapshot.
- Surface `original_task_input_snapshot_missing` or a more specific degraded reason.
- Preserve diagnostic fallback refs as diagnostic-only evidence.
- Never silently drop, retarget, or synthesize attachment state.
