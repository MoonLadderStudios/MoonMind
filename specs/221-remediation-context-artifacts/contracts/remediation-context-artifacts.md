# Contract: Remediation Context Artifacts

## Builder Input

```json
{
  "remediationWorkflowId": "mm:remediation-workflow",
  "principal": "service:remediation-context"
}
```

`remediationWorkflowId` must identify a `MoonMind.Run` execution with a persisted remediation link.

## Artifact Link

The builder creates a Temporal artifact linked to the remediation execution:

```json
{
  "namespace": "default",
  "workflow_id": "mm:remediation-workflow",
  "run_id": "remediation-run",
  "link_type": "remediation.context",
  "label": "reports/remediation_context.json"
}
```

The generated artifact metadata includes:

```json
{
  "artifact_type": "remediation.context",
  "name": "reports/remediation_context.json",
  "schemaVersion": "v1"
}
```

## Payload Shape

```json
{
  "schemaVersion": "v1",
  "remediationWorkflowId": "mm:remediation-workflow",
  "generatedAt": "2026-04-21T00:00:00Z",
  "target": {
    "workflowId": "mm:target-workflow",
    "runId": "target-run",
    "title": "Target",
    "state": "failed",
    "closeStatus": "failed"
  },
  "selectedSteps": [
    {
      "logicalStepId": "run-tests",
      "attempt": 1,
      "taskRunId": "tr_123"
    }
  ],
  "evidence": {
    "targetArtifactRefs": [
      { "artifact_id": "art_123" }
    ],
    "taskRuns": [
      { "taskRunId": "tr_123" }
    ]
  },
  "liveFollow": {
    "mode": "snapshot_then_follow",
    "supported": false,
    "taskRunId": "tr_123",
    "resumeCursor": null
  },
  "policies": {
    "authorityMode": "observe_only",
    "actionPolicyRef": null,
    "evidencePolicy": { "tailLines": 2000 },
    "approvalPolicy": null,
    "lockPolicy": null
  }
}
```

The payload must not include artifact bytes, log bodies, storage keys, presigned URLs, absolute local filesystem paths, or raw credential fields.
