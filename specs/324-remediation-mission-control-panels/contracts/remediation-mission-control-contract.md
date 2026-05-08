# Contract: Remediation Mission Control Panels

## API Surfaces

### Create Remediation

`POST /api/executions/{workflow_id}/remediation`

Request body shape:

```json
{
  "repository": "MoonLadderStudios/MoonMind",
  "instructions": "Investigate and remediate target execution mm:target using bounded evidence.",
  "remediation": {
    "mode": "snapshot_then_follow",
    "authorityMode": "approval_gated",
    "target": {
      "runId": "run-id",
      "taskRunIds": ["task-run-id"]
    },
    "actionPolicyRef": "admin_healer_default",
    "evidencePolicy": {
      "includeStepLedger": true,
      "includeDiagnostics": true,
      "tailLines": 2000,
      "allowLiveFollow": true
    },
    "trigger": { "type": "manual" }
  }
}
```

Contract rules:
- The route injects `target.workflowId` from `{workflow_id}`.
- The backend persists the canonical nested `task.remediation` object.
- Invalid or invisible targets return a structured validation error suitable for Mission Control display.

### List Remediation Links

`GET /api/executions/{workflow_id}/remediations?direction=inbound|outbound`

Response shape:

```json
{
  "direction": "inbound",
  "items": [
    {
      "remediationWorkflowId": "mm:remediation",
      "remediationRunId": "remediation-run",
      "targetWorkflowId": "mm:target",
      "targetRunId": "target-run",
      "selectedSteps": ["step-1"],
      "currentTargetState": "failed",
      "mode": "snapshot_then_follow",
      "authorityMode": "approval_gated",
      "status": "awaiting_approval",
      "activeLockScope": "target_execution",
      "activeLockHolder": "mm:remediation",
      "allowedActions": ["session.interrupt_turn"],
      "latestActionSummary": "Proposed session interrupt",
      "resolution": null,
      "contextArtifactRef": "artifact-context",
      "evidenceDegraded": false,
      "unavailableEvidenceClasses": [],
      "liveFollow": {
        "status": "active",
        "supported": true,
        "taskRunId": "task-run-id",
        "resumeCursor": { "sequence": 42 },
        "reconnectState": "connected",
        "epoch": "epoch-1",
        "fallbacks": ["merged_logs"],
        "reason": null
      },
      "approvalState": {
        "requestId": "mm:remediation:approval",
        "actionKind": "session.interrupt_turn",
        "riskTier": "high",
        "preconditions": "Target run is still active.",
        "blastRadius": "One managed session.",
        "decision": "pending",
        "decisionActor": null,
        "decisionAt": null,
        "canDecide": true,
        "auditRef": "audit-ref"
      },
      "createdAt": "2026-05-08T00:00:00Z",
      "updatedAt": "2026-05-08T00:00:01Z"
    }
  ]
}
```

Contract rules:
- Inbound panels use the same item shape as outbound panels.
- Fields unavailable from backend sources may be `null` or empty arrays, but Mission Control must render a bounded degraded or unavailable state.
- No raw storage paths, presigned URLs, secrets, or unbounded log content may appear in this response.

### Record Approval Decision

`POST /api/executions/{workflow_id}/remediation/approvals/{request_id}`

Request:

```json
{
  "decision": "approved",
  "comment": "Reviewed blast radius."
}
```

Response:

```json
{
  "accepted": true,
  "workflowId": "mm:remediation",
  "requestId": "mm:remediation:approval",
  "decision": "approved"
}
```

Contract rules:
- Only `approved` and `rejected` are accepted.
- Non-pending or unauthorized approval requests return a structured validation error.
- The decision must be visible in subsequent remediation link or audit state.

## UI Contract

Mission Control task detail must expose:
- A remediation creation control on every specified eligible surface.
- A target-side `Remediation Tasks` panel.
- A remediation-side `Remediation Target` panel.
- A `Remediation Evidence` panel for artifact-backed evidence.
- A live observation region that labels live follow as observation and shows cursor, reconnect, epoch, and durable fallback state.
- Approval controls only for pending, decidable handoffs.
- Read-only approval metadata when the operator cannot decide.
- Bounded degraded states for missing target links, partial evidence, unavailable live follow, lock conflict, precondition failure, and failed remediator final summary.
