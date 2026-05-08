# Contract: Remediation Evidence Context and Tools

## Context Artifact Contract

Artifact metadata:

```json
{
  "artifact_type": "remediation.context",
  "name": "reports/remediation_context.json",
  "schemaVersion": "v1",
  "targetWorkflowId": "mm:target",
  "targetRunId": "run-target"
}
```

Payload shape:

```json
{
  "schemaVersion": "v1",
  "remediationWorkflowId": "mm:remediation",
  "generatedAt": "2026-05-08T00:00:00Z",
  "target": {
    "workflowId": "mm:target",
    "runId": "run-target",
    "title": "Failed target task",
    "summary": "Needs remediation",
    "state": "failed",
    "closeStatus": "FAILED"
  },
  "selectedSteps": [
    {
      "logicalStepId": "run-tests",
      "attempt": 1,
      "taskRunId": "task-run-1",
      "status": "failed",
      "summary": "Integration test failed"
    }
  ],
  "evidence": {
    "targetArtifactRefs": [
      { "artifact_id": "art_run_summary", "kind": "run_summary" }
    ],
    "taskRuns": [
      {
        "taskRunId": "task-run-1",
        "observabilitySummaryRef": { "artifact_id": "art_observability" },
        "stdoutRef": { "artifact_id": "art_stdout" },
        "stderrRef": { "artifact_id": "art_stderr" },
        "mergedLogsRef": { "artifact_id": "art_merged" },
        "diagnosticsRef": { "artifact_id": "art_diagnostics" },
        "providerSnapshotRef": { "artifact_id": "art_provider" }
      }
    ],
    "availability": [
      {
        "class": "live_follow",
        "status": "unavailable",
        "reason": "target is terminal",
        "fallback": "merged_logs"
      }
    ]
  },
  "liveFollow": {
    "status": "unavailable",
    "mode": "snapshot_then_follow",
    "supported": false,
    "taskRunId": "task-run-1",
    "resumeCursor": null,
    "reason": "target is terminal",
    "fallbacks": ["merged_logs", "diagnostics"]
  },
  "policies": {
    "authorityMode": "approval_gated",
    "actionPolicyRef": "admin_healer_default",
    "evidencePolicy": {
      "includeStepLedger": true,
      "includeDiagnostics": true,
      "tailLines": 2000
    },
    "approvalPolicy": {
      "mode": "risk_gated",
      "autoAllowedRisk": "medium"
    },
    "lockPolicy": {
      "scope": "target_execution",
      "mode": "exclusive"
    }
  },
  "boundedness": {
    "maxTailLines": 2000,
    "maxTaskRunIds": 20,
    "rawLogBodiesIncluded": false,
    "artifactContentsIncluded": false
  }
}
```

Forbidden payload content:
- Presigned storage URLs.
- Storage backend keys.
- Absolute local filesystem paths.
- Raw secret values or secret-bearing config bundles.
- Unbounded log bodies or full diagnostics.

## Typed Tool Surface

These are internal/service contracts. Transport may be Temporal activity, adapter method, or MCP-style tool, but the inputs and outputs stay bounded.

### `remediation.get_context`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation"
}
```

Output:

```json
{
  "context": "<remediation.context payload>"
}
```

Errors:
- `context_missing`: no linked context artifact.
- `context_invalid`: linked artifact is not valid remediation context JSON.
- `permission_denied`: requester cannot read this remediation evidence.

### `remediation.read_target_artifact`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "artifactRef": { "artifact_id": "art_stdout" },
  "readMode": "preview"
}
```

Output:

```json
{
  "artifactRef": { "artifact_id": "art_stdout" },
  "contentType": "text/plain",
  "payloadRef": "server-mediated-response"
}
```

Rules:
- The artifact ID must appear in the linked context.
- The read goes through normal artifact authorization.
- The tool never returns backend storage coordinates.

### `remediation.read_target_logs`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "taskRunId": "task-run-1",
  "stream": "merged",
  "cursor": null,
  "tailLines": 200
}
```

Output:

```json
{
  "taskRunId": "task-run-1",
  "stream": "merged",
  "lines": ["bounded log line"],
  "nextCursor": "cursor-2"
}
```

Rules:
- `taskRunId` must appear in the linked context.
- `stream` is one of `stdout`, `stderr`, `merged`, or `diagnostics`.
- `tailLines` is capped by the context evidence policy.

### `remediation.follow_target_logs`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "taskRunId": "task-run-1",
  "fromSequence": 3842
}
```

Output:

```json
{
  "taskRunId": "task-run-1",
  "events": [
    {
      "sequence": 3843,
      "stream": "stdout",
      "text": "bounded live line",
      "timestamp": "2026-05-08T00:00:00Z"
    }
  ],
  "resumeCursor": { "sequence": 3843 }
}
```

Rules:
- Context live-follow state must be active/supported and policy-allowed.
- The requested task run must match the context live-follow task run.
- Durable logs and artifacts remain available if the stream disconnects.

### `remediation.prepare_action_request`

Input:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "actionKind": "session.interrupt_turn"
}
```

Output:

```json
{
  "remediationWorkflowId": "mm:remediation",
  "actionKind": "session.interrupt_turn",
  "target": {
    "workflowId": "mm:target",
    "pinnedRunId": "run-target",
    "currentRunId": "run-target",
    "state": "running",
    "closeStatus": null,
    "targetRunChanged": false
  }
}
```

Rules:
- Must reread target health before side-effecting action execution.
- Does not execute the action.
- If `targetRunChanged` is true, downstream action authority must evaluate whether the action is still safe.

## Mission Control Presentation Contract

For each remediation relationship, Mission Control should expose:
- Context artifact link when `contextArtifactRef` is present.
- Explicit missing/degraded message when `contextArtifactRef` is absent or evidence availability is degraded.
- Live observation state when present, with durable artifact/log fallback messaging.
- Links or labeled entries for decision log, action request/result, verification, target logs, diagnostics, and summaries when those artifacts are present.

Operator-facing labels must not expose raw storage paths, backend keys, or secret-bearing details.
