# Contract: Canonical Remediation Submissions

## Task-Shaped Create Input

`POST /api/executions` accepts a task-shaped payload with nested remediation intent:

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
          "runId": "optional-current-target-run",
          "taskRunIds": ["tr_01example"]
        },
        "mode": "snapshot_then_follow",
        "authorityMode": "observe_only",
        "actionPolicyRef": "admin_healer_default",
        "trigger": { "type": "manual" }
      }
    }
  }
}
```

## Normalized Durable Payload

The created execution stores remediation data under:

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

`target.runId` is resolved and persisted when omitted.

## Relationship Lookup

The service exposes:

- `list_remediation_targets(remediation_workflow_id)` for remediation-to-target lookup.
- `list_remediations_for_target(target_workflow_id)` for target-to-remediation lookup.

Each relationship includes pinned run identity and compact status/action/outcome fields.

## Convenience Route

`POST /api/executions/{workflowId}/remediation` accepts a remediation request body and expands it into the same task-shaped create contract as `POST /api/executions`.

The path `workflowId` becomes:

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

## Error Contract

Invalid remediation submissions fail before workflow start and before relationship persistence.

Invalid submissions include:

- missing or malformed `target.workflowId`
- run IDs supplied as workflow IDs
- self-targeting
- missing, invisible, unauthorized, or non-`MoonMind.Run` targets
- mismatched supplied `target.runId`
- malformed `target.taskRunIds`
- unsupported `authorityMode`
- unsupported or incompatible `actionPolicyRef`
- nested remediation targets
