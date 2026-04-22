# Contract: Remediation Create Links

## Submit Shape

Task-shaped create requests may include:

```json
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "task": {
      "instructions": "Investigate the target task.",
      "runtime": { "mode": "codex" },
      "remediation": {
        "target": {
          "workflowId": "mm:target-workflow",
          "runId": "optional-current-target-run"
        },
        "mode": "snapshot_then_follow",
        "authorityMode": "observe_only",
        "trigger": { "type": "manual" }
      }
    }
  }
}
```

The normalized execution parameters preserve:

```json
{
  "task": {
    "remediation": {
      "target": {
        "workflowId": "mm:target-workflow",
        "runId": "resolved-target-run"
      },
      "mode": "snapshot_then_follow",
      "authorityMode": "observe_only",
      "trigger": { "type": "manual" }
    }
  }
}
```

## Service Lookup Contract

The service exposes:

- `list_remediation_targets(remediation_workflow_id)` for outbound links.
- `list_remediations_for_target(target_workflow_id)` for inbound links.

Both return durable link rows from `execution_remediation_links`.

Link rows include nullable compact status/action fields for later read-model updates:

- `active_lock_scope`
- `active_lock_holder`
- `latest_action_summary`
- `outcome`

## Convenience Route

`POST /api/executions/{workflowId}/remediation` accepts a remediation request body and expands it into the same task-shaped create contract as `POST /api/executions`.

The route sets:

```json
{
  "task": {
    "remediation": {
      "target": {
        "workflowId": "mm:target-workflow"
      }
    }
  }
}
```

from the path workflow ID. It must not introduce a second durable payload shape.

## Error Contract

Invalid remediation create requests fail with `TemporalExecutionValidationError` before workflow start and before any remediation link is committed.

Invalid requests include:

- missing or malformed `target.workflowId`
- target run IDs supplied as workflow IDs
- missing, unauthorized, or non-`MoonMind.Run` targets
- mismatched target `runId`
- malformed `target.taskRunIds`
- unsupported `authorityMode`
- unsupported `actionPolicyRef`
- nested remediation targets
