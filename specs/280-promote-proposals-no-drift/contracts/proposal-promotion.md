# Contract: Proposal Promotion

## Endpoint

`POST /api/proposals/{proposal_id}/promote`

## Accepted Request Controls

```json
{
  "priority": 0,
  "maxAttempts": 3,
  "note": "optional reviewer note",
  "runtimeMode": "codex"
}
```

## Rejected Request Controls

```json
{
  "taskCreateRequestOverride": {
    "type": "task",
    "payload": {}
  }
}
```

Promotion must reject full task payload replacement before creating an execution.

## Promotion Semantics

1. Load the stored proposal.
2. Validate the stored `taskCreateRequest.payload` through the canonical task contract.
3. Reject unresolved `type: "preset"` submitted steps.
4. Preserve `task.authoredPresets` and `task.steps[].source` in the final payload.
5. Apply only bounded promotion controls.
6. Create the execution from the final validated payload.
