# Research: Live Logs Session Timeline UI

## Inputs reviewed

- `docs/ManagedAgents/LiveLogs.md`
- `docs/ManagedAgents/CodexCliManagedSessions.md`
- `docs/ManagedAgents/LiveLogs.md`
- `specs/141-live-logs-history-events/*`
- `frontend/src/entrypoints/task-detail.tsx`
- `frontend/src/entrypoints/task-detail.test.tsx`

## Findings

### 1. The frontend already consumes the right backend surfaces

The current Live Logs panel already fetches summary, structured history, merged fallback, and SSE in roughly the right order. The main gap is that the rendered experience still behaves like a line-oriented viewer with minimal row differentiation.

### 2. Session-aware data is available but underused in the UI

The current schemas already parse `sessionSnapshot` and `RunObservabilityEvent` session fields, and the existing tests already cover reset-boundary rendering. The remaining Phase 4 work is to turn those raw fields into a cohesive timeline UI with a stronger header and row system.

### 3. Viewer hardening is still missing

The current panel renders every row with direct mapping inside a scroll container and keeps all presentation styles inline. That conflicts with the desired-state architecture, which calls for `react-virtuoso` and `anser`.

### 4. Rollout should stay feature-flagged

The backend already exposes `liveLogsSessionTimelineEnabled` and `liveLogsSessionTimelineRollout`, but the current frontend does not use that flag to preserve a legacy line-view mode. Phase 4 should consume the flag rather than assuming universal enablement.

## Decisions

### Decision 1: Keep one Live Logs panel and switch behavior with the feature flag

- **Chosen**: Preserve the existing panel lifecycle and gate the richer timeline renderer behind `liveLogsSessionTimelineEnabled`.
- **Rationale**: Matches the rollout plan and allows gradual enablement without creating a second route or page.

### Decision 2: Prefer structured history even when merged fallback exists

- **Chosen**: Keep the summary -> structured history -> merged fallback order explicit in the UI.
- **Rationale**: Aligns the frontend with the backend Phase 3 contract and avoids parsing merged text as the primary model when structured events exist.

### Decision 3: Treat `session_reset_boundary` as the only hard banner requirement in this slice

- **Chosen**: Render reset boundaries as banners and give other session/approval/publication rows distinct non-banner timeline treatments.
- **Rationale**: Matches the desired-state contract while keeping the first implementation pass bounded.

### Decision 4: Use ANSI rendering only for output rows

- **Chosen**: Parse ANSI fragments for `stdout` and `stderr`; leave `system` and `session` rows as ordinary text/timeline rows.
- **Rationale**: Those non-output rows are MoonMind-originated annotations and do not need terminal-style escape handling.
