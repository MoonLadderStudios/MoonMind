# Contract: Live Logs Phase 6 Compatibility

## Runtime Config Contract

Dashboard config continues to expose:

- `features.logStreamingEnabled`
- `features.liveLogsSessionTimelineEnabled`
- `features.liveLogsSessionTimelineRollout`

Frontend compatibility rules:

1. If rollout is missing, fall back to the boolean gate.
2. If rollout is present, it becomes the primary eligibility input.
3. Missing or false boolean values must not crash older or newer payload combinations.

## Observability Event Compatibility Contract

Frontend event normalization must accept:

- canonical camelCase session fields from task-run history and SSE
- legacy snake_case session fields used by existing browser tests and older compatibility paths
- minimal events that contain only:
  - `sequence`
  - `timestamp`
  - `stream`
  - `text`

The normalized frontend event model is the single input to timeline-row mapping and session-snapshot derivation.

## Fallback Ordering Contract

Initial Live Logs content is resolved in this order:

1. `observability-summary`
2. `observability/events`
3. `logs/merged` when structured history is unavailable or empty
4. optional SSE live follow when the run is active and stream-capable
