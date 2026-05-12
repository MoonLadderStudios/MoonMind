# Contract: Editable Full Retry Workflow

Traceability: MM-644.

## Execution Detail Action Contract

The execution detail payload exposes Edit task eligibility through action capabilities.

```json
{
  "workflowId": "mm:failed-source",
  "workflowType": "MoonMind.Run",
  "state": "failed",
  "taskInputSnapshot": {
    "available": true,
    "artifactRef": "artifact://snapshot/source",
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

Rules:
- `canEditForRerun` is true only for eligible `MoonMind.Run` executions with readable authoritative task input snapshot evidence.
- If unavailable, `actions.disabledReasons.canEditForRerun` uses a bounded operator-readable code such as `original_task_input_snapshot_missing`, `snapshot_unauthorized`, `snapshot_unreadable`, or `state_not_eligible`.
- Task Detail links Edit task to `/tasks/new?rerunExecutionId=<workflowId>&mode=edit`.

## Create Page Route Contract

Route:

```text
/tasks/new?rerunExecutionId=<sourceWorkflowId>&mode=edit
```

Rules:
- The route resolves to page mode `rerun` and intent `edit-for-rerun`.
- The page loads the source execution detail and requires `actions.canEditForRerun === true`.
- The page hydrates the authoring draft from `taskInputSnapshot.artifactRef` when reconstruction mode is authoritative.
- The page presents copy that the edited task will create a new run and the original run will remain unchanged.
- The page permits normal authoring edits and normal validation.

## Edited Full Retry Submission Contract

Endpoint:

```text
POST /api/executions/{sourceWorkflowId}/update
```

Changed edited full retry payload:

```json
{
  "updateName": "RequestRerun",
  "inputArtifactRef": "artifact://input/edited-full-retry",
  "parametersPatch": {
    "repository": "MoonLadderStudios/MoonMind",
    "task": {
      "instructions": "Edited task instructions",
      "recovery": {
        "kind": "edited_full_retry",
        "sourceWorkflowId": "mm:failed-source",
        "sourceRunId": "source-run-id"
      }
    }
  }
}
```

Exact rerun payload remains mutation-free:

```json
{
  "updateName": "RequestRerun"
}
```

Rules:
- Exact rerun and edited full retry must remain distinguishable by whether the submitted authoring payload changed and by resulting provenance.
- Accepted edited full retry creates a new execution for terminal source executions.
- The new execution starts from the beginning and writes its own authoritative task input snapshot.
- Resume fields are forbidden carryover for edited full retry: `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, `completedSteps`, task `resume`, and prior task `recovery` unless replaced by `edited_full_retry` provenance.
- Source execution snapshot, ledger, artifacts, checkpoints, and terminal state remain unchanged.
