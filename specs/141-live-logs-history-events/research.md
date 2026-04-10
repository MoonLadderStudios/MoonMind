# Research: Live Logs History Events

## Decision 1: Reuse the existing `/observability/events` route instead of adding a new endpoint

- **Decision**: Keep the existing task-run historical route and extend its query contract rather than introducing a second Phase 3-only endpoint.
- **Rationale**: The router already has the correct path and the frontend already consumes it. Phase 3 is contract completion, not route proliferation.
- **Alternatives considered**:
  - Add a new history endpoint and leave the current route frozen. Rejected because it would create avoidable compatibility and rollout churn immediately before Phase 4.

## Decision 2: Filter after canonical event normalization

- **Decision**: Apply `since`, stream, and kind filters after the route has normalized durable journal, spool, or artifact-backed rows into the canonical `RunObservabilityEvent` shape.
- **Rationale**: This keeps filtering consistent across all history sources and avoids source-specific query behavior.
- **Alternatives considered**:
  - Filter journal rows, spool rows, and artifact synthesis independently before normalization. Rejected because each source would need duplicate logic and would drift more easily.

## Decision 3: Keep durable-source priority explicit and additive

- **Decision**: Historical loading continues to prefer `observability.events.jsonl`, then spool history, then artifact-backed synthesis.
- **Rationale**: The event journal is the authoritative structured source from Phase 1, while spool and artifact synthesis are compatibility fallbacks for older or partial runs.
- **Alternatives considered**:
  - Merge journal and spool rows together. Rejected because it risks duplicate or out-of-order historical playback.

## Decision 4: Treat summary and SSE as compatibility surfaces on the same event model

- **Decision**: Keep `/observability-summary` and `/logs/stream` on the existing routes, but verify and harden them against the same canonical event/session snapshot contract used by historical retrieval.
- **Rationale**: Operators need one coherent mental model across summary, history, and live follow. The routes already exist; the missing work is truthfulness and parity.
- **Alternatives considered**:
  - Defer summary and SSE validation entirely to Phase 4. Rejected because Phase 3 explicitly calls for tests covering all three backend surfaces together.
