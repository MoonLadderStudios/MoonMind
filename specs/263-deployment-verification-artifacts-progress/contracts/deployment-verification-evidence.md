# Contract: Deployment Verification Evidence

The `deployment.update_compose_stack` tool result keeps the existing public shape and adds compact audit/progress metadata.

## Output Fields

Required existing fields:
- `status`: `SUCCEEDED`, `FAILED`, or `PARTIALLY_VERIFIED`
- `stack`
- `requestedImage`
- `updatedServices`
- `runningServices`
- `beforeStateArtifactRef`
- `afterStateArtifactRef`
- `commandLogArtifactRef`
- `verificationArtifactRef`

Additional metadata fields:
- `resolvedDigest`: optional digest evidence
- `audit`: compact deployment audit metadata

## Progress Payload

`ToolResult.progress` contains:

```json
{
  "percent": 100,
  "state": "SUCCEEDED",
  "message": "Deployment update succeeded.",
  "events": [
    {"state": "VALIDATING", "message": "Validating deployment update input."},
    {"state": "CAPTURING_BEFORE_STATE", "message": "Capturing current deployment state."}
  ]
}
```

Progress events must not include raw command output, environment dumps, auth headers, tokens, or command logs.
