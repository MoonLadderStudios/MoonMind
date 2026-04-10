# Research: Live Logs Session Timeline

## Decision: Add a dedicated session-timeline rollout setting

### Rationale

The boot payload currently exposes `logStreamingEnabled` through direct environment parsing. Phase 0 needs a separate rollout boundary for the session-aware timeline contract so operators can enable the timeline independently from the existing spool/SSE transport.

### Alternatives considered

- Reuse `logStreamingEnabled`: rejected because the existing flag describes transport availability, not timeline semantics or rollout scope.
- Hardcode the timeline as always on: rejected because the plan explicitly requires staged rollout scopes.

## Decision: Use one canonical `RunObservabilityEvent` model internally

### Rationale

The code already carries session-aware fields on `LiveLogChunk`, but the model name and persistence flow still imply chunk-only transport rather than a canonical MoonMind observability contract. A single `RunObservabilityEvent` model aligns the code with the canonical docs and keeps one contract across live and historical reads.

### Alternatives considered

- Keep `LiveLogChunk` as the canonical model forever: rejected because the remaining migration work is explicitly timeline-oriented, not chunk-oriented.
- Maintain parallel `LiveLogChunk` and `RunObservabilityEvent` models with translation layers: rejected because the repo compatibility policy prefers deleting old-only internal assumptions rather than keeping a second internal contract.

## Decision: Persist structured observability history as an artifact-backed JSONL file

### Rationale

The shared spool file already serializes live observability rows as JSON lines. Persisting that content to an `observability.events.jsonl` artifact at publication/finalization time gives ended runs a durable structured history source without adding a new database or holding every event in workflow history.

### Alternatives considered

- Keep relying on spool files under the workspace only: rejected because the plan wants artifact-backed reconstruction for ended runs.
- Persist structured rows only inside `diagnostics.json`: rejected because diagnostics should remain a compact bundle, not the primary timeline-history container.
- Add a database-backed observability table in this phase: rejected because Phase 1 only needs durable reconstruction, not a new storage substrate.

## Decision: Persist the latest session snapshot directly on `ManagedRunRecord`

### Rationale

The summary router already builds `sessionSnapshot` through a separate session-store lookup. Storing the latest bounded session identity on the managed-run record reduces coupling, makes ended-run summaries self-contained, and gives future timeline/history readers a single durable summary source.

### Alternatives considered

- Keep all session summary data in the separate session store only: rejected because Phase 1 explicitly calls for managed-run record support for session snapshot fields.
- Persist only `observability_events_ref` and derive the current session snapshot every time from the event journal: rejected because summary surfaces need a compact durable snapshot without replaying the full journal.
