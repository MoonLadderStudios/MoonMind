# Contract: Task Dashboard Session Timeline Rollout Flag

Phase 0 adds a dedicated runtime-config surface for the session-aware timeline rollout.

## Runtime config fields

- `features.liveLogsSessionTimelineEnabled`
- `features.liveLogsSessionTimelineRollout`

## Semantics

- `liveLogsSessionTimelineEnabled` is `false` only when rollout is `off`.
- `liveLogsSessionTimelineRollout` is one of:
  - `off`
  - `internal`
  - `codex_managed`
  - `all_managed`
- Existing `features.logStreamingEnabled` remains present and continues to describe live transport availability, not timeline rollout.

## Consumer expectations

- The UI can hide or show timeline-specific behavior based on the new rollout fields without inferring session-timeline state from the older log-streaming toggle.
- Backend rollout checks can reuse the same enumerated scope value used by the boot payload.
