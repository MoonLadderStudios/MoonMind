# Contract: Remediation Mission Control Surfaces

## Read Remediation Links

Mission Control needs a bounded read surface for task detail.

```http
GET /api/executions/{workflow_id}/remediations?direction=inbound
GET /api/executions/{workflow_id}/remediations?direction=outbound
```

`inbound` returns remediation tasks targeting `{workflow_id}`. `outbound` returns the target execution for a remediation task.

Response shape:

```json
{
  "items": [
    {
      "remediationWorkflowId": "mm:remediation",
      "remediationRunId": "run-remediation",
      "targetWorkflowId": "mm:target",
      "targetRunId": "run-target",
      "mode": "snapshot_then_follow",
      "authorityMode": "approval_gated",
      "status": "awaiting_approval",
      "activeLockScope": "target_execution",
      "activeLockHolder": "mm:remediation",
      "latestActionSummary": "Proposed session interrupt",
      "resolution": null,
      "contextArtifactRef": "art-context",
      "createdAt": "2026-04-22T00:00:00Z",
      "updatedAt": "2026-04-22T00:01:00Z"
    }
  ],
  "direction": "inbound"
}
```

Rules:
- Response items are compact read-model data sourced from persisted remediation links and artifacts.
- `remediationWorkflowId`, `remediationRunId`, `targetWorkflowId`, `targetRunId`, `mode`, `authorityMode`, `status`, `createdAt`, and `updatedAt` come from the persisted remediation link/read model.
- `activeLockScope`, `activeLockHolder`, `latestActionSummary`, `resolution`, and `contextArtifactRef` are derived from bounded remediation execution state, control/action-event metadata, and artifact metadata. If an existing bounded source is unavailable, the fields are returned as `null` or omitted; implementation must not add a persistent table solely for these summaries.
- The route must not scan raw logs or artifact bodies during task-detail rendering.
- Missing or unauthorized executions return existing task-detail authorization semantics.

## Create Remediation From Target

Existing route:

```http
POST /api/executions/{workflow_id}/remediation
```

Mission Control sends target-scoped choices such as:

```json
{
  "task": {
    "instructions": "Investigate the target execution using bounded evidence.",
    "runtime": { "mode": "codex" },
    "remediation": {
      "target": {
        "runId": "run-target",
        "stepSelectors": [{ "logicalStepId": "run-tests", "attempt": 1 }],
        "taskRunIds": ["tr_123"]
      },
      "mode": "snapshot_then_follow",
      "authorityMode": "observe_only",
      "evidencePolicy": {
        "includeStepLedger": true,
        "includeDiagnostics": true,
        "tailLines": 2000
      },
      "actionPolicyRef": "admin_healer_default",
      "approvalPolicy": { "mode": "risk_gated" },
      "trigger": { "type": "manual" }
    }
  }
}
```

The server expands the path workflow ID into `task.remediation.target.workflowId` and normalizes to the canonical `initialParameters.task.remediation` payload.

## Remediation Evidence Presentation

Task detail groups artifact refs with remediation artifact types:

- `remediation.context`
- `remediation.plan`
- `remediation.decision_log`
- `remediation.action_request`
- `remediation.action_result`
- `remediation.verification`
- `remediation.summary`

The UI uses existing artifact preview/download routes. It must not render storage keys, local paths, presigned URLs, or raw log bodies as remediation evidence metadata.

## Approval Decision

If no existing control-event route can record decisions, add a narrow route:

```http
POST /api/executions/{remediation_workflow_id}/remediation/approvals/{request_id}
```

`{remediation_workflow_id}` identifies the remediation task execution that owns the action request. Target-scoped screens must submit the selected remediation task workflow ID rather than the target workflow ID, because one target execution can have multiple remediation tasks and pending requests.

Request:

```json
{
  "decision": "approved",
  "comment": "Preconditions and blast radius reviewed."
}
```

Allowed decisions:
- `approved`
- `rejected`

Rules:
- The route requires current-operator approval permission.
- The route writes an audit/control event before returning success.
- Duplicate decisions must be idempotent or fail with a structured conflict.
- The route never executes the remediation action directly; it records operator approval state for the remediation action boundary.
