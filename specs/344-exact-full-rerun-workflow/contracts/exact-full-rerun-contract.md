# Contract: Exact Full Rerun

## Mission Control Interaction

User-visible action:

- Label: `Rerun`
- Available when execution actions expose `canRerun = true`.
- Unavailable reason should remain visible when `canRerun = false`.
- Selecting `Rerun` for exact full rerun MUST submit the rerun request directly from the task detail surface and MUST NOT navigate to or render task authoring.

Out of scope:

- `Edit task` and edit-for-rerun remain the editable retry path.
- Failed-step `Resume` remains the progress-preserving retry path.

## Request Shape

Exact full rerun request:

```json
{
  "updateName": "RequestRerun"
}
```

Allowed metadata:

- Idempotency metadata may be included when supported by the existing update route.

Forbidden exact-rerun mutation fields:

- `parametersPatch`
- `inputArtifactRef`
- `planArtifactRef`
- Any submitted task body edits
- Any resume checkpoint or preserved-progress payload

## Created Execution Semantics

The created execution MUST include recovery provenance equivalent to:

```json
{
  "task": {
    "recovery": {
      "kind": "exact_full_rerun",
      "sourceWorkflowId": "<source workflow id>",
      "sourceRunId": "<source run id>"
    }
  }
}
```

The created execution MUST:

- Reuse the original task input snapshot unchanged.
- Start from the beginning.
- Run the normal full execution path.
- Exclude completed progress, preserved step outputs, resume source metadata, and resume checkpoint refs.

## Error Semantics

If exact rerun cannot safely reuse the original task input snapshot, the system MUST fail closed with an explicit unavailable/degraded reason. The existing `original_task_input_snapshot_missing` reason remains valid for missing source snapshots.

If source workflow/run identity is missing, the system MUST reject exact rerun rather than creating unpinned provenance.
