# Contract: Run Observability Event

Phase 1 standardizes one MoonMind-owned event contract for both live and historical observability.

## Canonical shape

```json
{
  "runId": "task-run-123",
  "sequence": 17,
  "timestamp": "2026-04-08T01:23:45Z",
  "stream": "session",
  "kind": "reset_boundary_published",
  "text": "Epoch boundary reached. Session sess-1 is now on epoch 2 thread thread-2.",
  "offset": null,
  "sessionId": "sess-1",
  "sessionEpoch": 2,
  "containerId": "ctr-1",
  "threadId": "thread-2",
  "turnId": null,
  "activeTurnId": null,
  "metadata": {
    "resetBoundaryRef": "sess-1/observability/session.reset_boundary.epoch-2.json"
  }
}
```

## Contract rules

- One shared `sequence` namespace spans stdout, stderr, system, and session rows.
- The payload is MoonMind-normalized and must not expose provider-native event envelopes as the browser contract.
- Live transport and historical retrieval use the same event shape.
- Existing live-log consumers remain readable because the canonical event shape preserves the current stream/text/timestamp/offset semantics while adding formal kind and session metadata.
- Ended runs should prefer the durable `observability.events.jsonl` artifact before falling back to spool or merged-artifact synthesis.
