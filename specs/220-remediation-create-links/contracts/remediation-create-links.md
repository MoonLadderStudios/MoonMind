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

## Error Contract

Invalid remediation create requests fail with `TemporalExecutionValidationError` before workflow start and before any remediation link is committed.
