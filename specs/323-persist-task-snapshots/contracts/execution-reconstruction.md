# Contract: Execution Reconstruction And Task Snapshot Actions

## Execution Detail Response

Execution detail responses expose task reconstruction state through `taskInputSnapshot`:

```json
{
  "taskInputSnapshot": {
    "available": true,
    "artifactRef": "art_snapshot_1",
    "snapshotVersion": 1,
    "sourceKind": "create",
    "reconstructionMode": "authoritative",
    "disabledReasons": {},
    "fallbackEvidenceRefs": []
  },
  "actions": {
    "canEditForRerun": true,
    "canRerun": true,
    "canResumeFromFailedStep": false,
    "disabledReasons": {}
  }
}
```

When the original task input snapshot is missing, edit and rerun actions are disabled even if execution parameters contain reconstructable-looking task text:

```json
{
  "taskInputSnapshot": {
    "available": false,
    "artifactRef": null,
    "snapshotVersion": null,
    "sourceKind": "unknown",
    "reconstructionMode": "degraded_read_only",
    "disabledReasons": {
      "draft": "original_task_input_snapshot_missing"
    },
    "fallbackEvidenceRefs": ["artifact://input/source"]
  },
  "actions": {
    "canEditForRerun": false,
    "canRerun": false,
    "disabledReasons": {
      "canEditForRerun": "original_task_input_snapshot_missing",
      "canRerun": "original_task_input_snapshot_missing"
    }
  }
}
```

## Policy Rules

- `taskInputSnapshot.reconstructionMode=authoritative` requires `taskInputSnapshot.artifactRef`.
- `canEditForRerun` and `canRerun` require an authoritative task input snapshot.
- `canResumeFromFailedStep` requires an authoritative task input snapshot and a resume checkpoint.
- Parameter payloads, input artifacts, plan artifacts, logs, and projections are diagnostic evidence only when the original snapshot is missing.
