# Contract: Live Logs Phase 7 Hardening and Rollback

## 1. Backend router metrics

### 1.1 `/api/task-runs/{id}/observability-summary`

- MUST emit `livelogs.summary.latency` through the shared metrics emitter when the route reaches summary shaping logic.
- MUST keep response payload unchanged.

### 1.2 `/api/task-runs/{id}/observability/events`

- MUST emit `livelogs.history.latency` and `livelogs.history.source` on successful history reconstruction.
- MUST tag successful history metrics with `source=journal`, `source=spool`, or `source=artifacts`.
- MUST emit `livelogs.history.error` on failures that occur after authorization while loading history.
- MUST keep the existing response payload unchanged.

### 1.3 `/api/task-runs/{id}/logs/stream`

- MUST preserve `livelogs.stream.connect`, `livelogs.stream.disconnect`, and `livelogs.stream.error`.
- MUST not let metrics failures change stream semantics.

## 2. Frontend rollback behavior

- Dashboard runtime config exposes `features.liveLogsStructuredHistoryEnabled`.
- When the flag is `false`, the task-detail page:
  - still fetches `/observability-summary`,
  - does not request `/observability/events`,
  - uses `/logs/merged` as the initial historical surface,
  - still allows live SSE follow when supported.

## 3. Authorization

- `/api/task-runs/{id}/observability/events` MUST remain protected by the same owner/superuser rules as the other task-run observability routes.
