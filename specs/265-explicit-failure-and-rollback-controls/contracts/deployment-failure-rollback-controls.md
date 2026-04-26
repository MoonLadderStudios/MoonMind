# Contract: Deployment Failure and Rollback Controls

## Tool Output Additions

`deployment.update_compose_stack` keeps the existing `ToolResult.outputs` shape and may add compact failure metadata when status is not `SUCCEEDED`:

```json
{
  "status": "FAILED",
  "stack": "moonmind",
  "requestedImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
  "beforeStateArtifactRef": "art:sha256:before",
  "afterStateArtifactRef": "art:sha256:after",
  "commandLogArtifactRef": "art:sha256:commands",
  "verificationArtifactRef": "art:sha256:verification",
  "failure": {
    "class": "verification_failure",
    "reason": "Deployment verification did not prove desired state.",
    "retryable": false
  },
  "audit": {
    "workflowId": "workflow-123",
    "taskId": "task-456",
    "operator": "admin@example.com",
    "operatorRole": "admin",
    "finalStatus": "FAILED"
  }
}
```

Failure metadata must not contain raw command output, credentials, tokens, environment dumps, or unredacted artifact content.

## Deployment Stack State Additions

`GET /api/v1/operations/deployment/stacks/{stack}` may include bounded recent actions:

```json
{
  "stack": "moonmind",
  "recentActions": [
    {
      "id": "depupd_recent",
      "kind": "failure",
      "status": "FAILED",
      "requestedImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
      "operator": "admin@example.com",
      "reason": "Routine release",
      "startedAt": "2026-04-26T00:00:00Z",
      "completedAt": "2026-04-26T00:05:00Z",
      "runDetailUrl": "/tasks/depupd_recent",
      "logsArtifactUrl": "/api/artifacts/logs",
      "rawCommandLogPermitted": false,
      "beforeSummary": "ghcr.io/moonladderstudios/moonmind:stable",
      "afterSummary": "verification failed",
      "rollbackEligibility": {
        "eligible": true,
        "targetImage": {
          "repository": "ghcr.io/moonladderstudios/moonmind",
          "reference": "stable"
        },
        "sourceActionId": "depupd_recent",
        "evidenceRef": "art:sha256:before"
      }
    }
  ]
}
```

When rollback is not safe, `rollbackEligibility.eligible` is false and `reason` explains why. The response must omit or disable raw command-log URLs unless operational-admin policy permits them.

## Rollback Submission

Rollback submits to the same typed update endpoint:

`POST /api/v1/operations/deployment/update`

```json
{
  "stack": "moonmind",
  "image": {
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "reference": "stable"
  },
  "mode": "changed_services",
  "removeOrphans": true,
  "wait": true,
  "runSmokeCheck": false,
  "pauseWork": false,
  "pruneOldImages": false,
  "reason": "Rollback after failed update depupd_recent",
  "confirmation": "Rollback to ghcr.io/moonladderstudios/moonmind:stable confirmed",
  "operationKind": "rollback",
  "rollbackSourceActionId": "depupd_recent"
}
```

The endpoint must preserve the same admin, policy, allowlist, queue, lock, artifact, and verification behavior as forward updates. Unsupported rollback metadata or unsafe targets fail closed.
