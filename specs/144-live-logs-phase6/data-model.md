# Data Model: Live Logs Phase 6 Compatibility and Cleanup

## Timeline Eligibility

Determines whether the task-detail page renders:

- the session-aware timeline viewer, or
- the legacy line viewer

Inputs:

- `liveLogsSessionTimelineEnabled`
- `liveLogsSessionTimelineRollout`
- current run `targetRuntime`
- presence of a task-run-backed observability surface

Rules:

1. Missing or disabled rollout config degrades to the legacy line viewer.
2. `codex_managed` enables the timeline only for Codex managed runs.
3. `all_managed` enables the timeline for any managed run with task-run observability.
4. Older boot payloads that expose only the boolean remain readable through safe fallback logic.

## Compatibility-Normalized Observability Event

Frontend-owned normalized shape used before mapping into `TimelineRow`.

Fields:

- `sequence`
- `timestamp`
- `stream`
- `text`
- `kind`
- `offset`
- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `turnId`
- `activeTurnId`
- `metadata`

Accepted source aliases:

- camelCase: `sessionId`, `sessionEpoch`, `containerId`, `threadId`, `turnId`, `activeTurnId`
- snake_case: `session_id`, `session_epoch`, `container_id`, `thread_id`, `turn_id`, `active_turn_id`

Compatibility rule:

- Older minimal payloads may omit all session fields and `kind`.
- Those payloads still normalize successfully if `sequence`, `timestamp`, `stream`, and `text` exist.

## Merged Fallback Trigger

The frontend requests `/logs/merged` when either condition is true:

1. `/observability/events` is unavailable or errors, or
2. `/observability/events` succeeds but returns zero normalized rows

The merged fallback remains compatibility-only and does not replace structured history when structured rows exist.
