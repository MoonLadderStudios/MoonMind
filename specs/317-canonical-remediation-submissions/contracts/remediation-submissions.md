# Contract: Canonical Remediation Submissions

**Traceability**: Jira issue `MM-617`; feature path `specs/317-canonical-remediation-submissions`.

## Create Task With Remediation Metadata

Intent: submit a normal task-shaped execution that carries canonical remediation metadata.

Request shape:

```json
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "task": {
      "instructions": "Investigate the target execution.",
      "runtime": { "mode": "codex" },
      "remediation": {
        "target": {
          "workflowId": "mm:target-workflow",
          "runId": "optional-current-target-run",
          "taskRunIds": ["optional-target-task-run"]
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

Required behavior:
- Preserve `task.remediation` in normalized task parameters.
- Resolve and persist `target.runId` when omitted.
- Reject invalid target, policy, authority, nested, or task-run selections before workflow start.
- Do not create dependency prerequisites for the remediation relationship.

## Create Remediation Convenience Request

Intent: create a remediation task for a path-selected target workflow while expanding into the same task-shaped create contract.

Path:

```http
POST /api/executions/{workflow_id}/remediation
```

Required behavior:
- The path workflow ID becomes `task.remediation.target.workflowId`.
- Top-level remediation fields override nested task remediation fields for the selected target.
- Malformed remediation payloads fail with a structured request error before service invocation.

## List Remediation Relationships

Intent: read compact direction-specific relationship records.

Path:

```http
GET /api/executions/{workflow_id}/remediations?direction=inbound
GET /api/executions/{workflow_id}/remediations?direction=outbound
```

Response shape:

```json
{
  "direction": "inbound",
  "items": [
    {
      "remediationWorkflowId": "mm:remediation",
      "remediationRunId": "run-remediation",
      "targetWorkflowId": "mm:target",
      "targetRunId": "run-target",
      "mode": "snapshot_then_follow",
      "authorityMode": "approval_gated",
      "status": "created",
      "activeLockScope": "target_execution",
      "activeLockHolder": "mm:remediation",
      "latestActionSummary": "optional compact action summary",
      "resolution": "optional outcome",
      "contextArtifactRef": "optional-artifact-ref",
      "approvalState": null,
      "createdAt": "2026-05-08T00:00:00Z",
      "updatedAt": "2026-05-08T00:00:00Z"
    }
  ]
}
```

Required behavior:
- `inbound` lists remediations targeting the selected execution.
- `outbound` lists targets remediated by the selected execution.
- Unknown directions fail with a structured validation error.
- Responses expose artifact refs and compact metadata only, not raw evidence bodies, storage paths, presigned URLs, or secrets.
