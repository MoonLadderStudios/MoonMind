# Feature Specification: Live Logs Phase 7 Hardening and Rollback

**Feature Branch**: `145-live-logs-phase7`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 7 using test-driven development from the Live Logs Session-Aware Implementation Plan. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Observe Live Logs surface health operationally (Priority: P1)

MoonMind operators need the Live Logs summary, structured-history, and live-stream surfaces to emit their own health metrics so regressions can be detected before the session-aware timeline becomes the default managed-run experience.

**Why this priority**: Phase 7 explicitly calls for operational hardening. Without metrics on the main read surfaces, the team cannot validate latency, disconnect rate, or history-read behavior during rollout.

**Independent Test**: Request `/observability-summary`, `/observability/events`, and `/logs/stream` in automated tests and verify the router emits the expected StatsD metrics for latency, connect/disconnect, and error paths.

**Acceptance Scenarios**:

1. **Given** a task run summary request succeeds, **When** MoonMind serves `/observability-summary`, **Then** it records a summary latency metric tagged for the Live Logs surface.
2. **Given** structured observability history is loaded from the journal, spool, or artifact fallback, **When** MoonMind serves `/observability/events`, **Then** it records journal/history latency and source metrics for that request.
3. **Given** an SSE client connects and later disconnects or errors, **When** MoonMind serves `/logs/stream`, **Then** it records connect, disconnect, and error metrics without breaking the stream response contract.

---

### User Story 2 - Protect structured history with the same ownership rules as other observability surfaces (Priority: P1)

Operators need the structured history endpoint to enforce the same owner-based authorization as summary and continuity routes so the new historical timeline surface is safe to expose broadly.

**Why this priority**: Phase 7 requires validating auth and ownership checks for the new observability surfaces. `/observability/events` is the newest Phase 3+ route and needs explicit owner-access regression coverage.

**Independent Test**: Exercise `/api/task-runs/{id}/observability/events` as the owning user and as a different user, then verify the route allows the owner and rejects cross-owner access with `403`.

**Acceptance Scenarios**:

1. **Given** a non-superuser owns the workflow bound to a task run, **When** they request `/observability/events`, **Then** the structured history loads successfully.
2. **Given** a different non-superuser requests the same run, **When** they request `/observability/events`, **Then** MoonMind rejects access with `403`.
3. **Given** the structured history request is denied, **When** MoonMind responds, **Then** it does not emit a false success metric for that request.

---

### User Story 3 - Roll back the structured-history timeline path without breaking Live Logs (Priority: P1)

MoonMind operators need a runtime-config switch that disables structured-history loading in the browser and returns Live Logs to the merged-tail path so the team can roll back the new historical timeline path without removing the overall Live Logs panel.

**Why this priority**: Phase 7 requires a defined rollback behavior. The existing timeline flag controls the richer viewer, but it does not provide a dedicated kill switch for the `/observability/events` path once the timeline UI is already enabled.

**Independent Test**: Render the task detail page with the new rollback flag disabled and confirm the browser skips `/observability/events`, loads `/logs/merged`, and keeps the existing Live Logs lifecycle intact.

**Acceptance Scenarios**:

1. **Given** the structured-history rollout switch is disabled, **When** an operator opens Live Logs, **Then** the browser skips `/observability/events` and loads the merged-tail fallback directly.
2. **Given** the structured-history rollout switch is enabled, **When** an operator opens Live Logs, **Then** the browser still prefers `/observability/events` before `/logs/merged`.
3. **Given** the structured-history rollback switch is disabled while the session timeline viewer remains enabled, **When** Live Logs renders, **Then** the panel still works with historical merged content and optional live SSE follow for active runs.

### Edge Cases

- A structured-history request may fail before any journal is read; latency/error metrics must still be emitted without assuming a history source.
- A task run may have no persisted event journal and no spool, causing artifact synthesis to serve `/observability/events`; source-tag metrics must still identify the fallback path truthfully.
- Owner authorization may fail before summary/history loading; metrics must not label the request as a successful history read.
- The browser may disable structured history while the session-aware timeline viewer remains eligible; Live Logs must still render merged text and live updates without trying the history endpoint first.
- Metrics emission itself may fail or be disabled; router behavior must remain best-effort and continue serving responses.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `/api/task-runs/{id}/observability-summary` MUST emit a Live Logs summary latency metric for every completed request path that reaches the router business logic.
- **FR-002**: `/api/task-runs/{id}/observability/events` MUST emit latency metrics and a history-source metric that distinguishes at least `journal`, `spool`, and `artifacts` when one of those sources is used.
- **FR-003**: `/api/task-runs/{id}/observability/events` MUST emit an error metric for failed history reads after the request passes authorization and enters the route handler.
- **FR-004**: `/api/task-runs/{id}/logs/stream` MUST continue to emit connect, disconnect, and error metrics for SSE activity.
- **FR-005**: `/api/task-runs/{id}/observability/events` MUST enforce the same owner-based access rules as the other task-run observability routes.
- **FR-006**: The dashboard runtime config MUST expose a dedicated rollback flag for structured-history usage in Live Logs.
- **FR-007**: The task-detail frontend MUST skip `/observability/events` and use `/logs/merged` directly when the structured-history rollback flag is disabled.
- **FR-008**: The task-detail frontend MUST preserve the current summary -> history/tail -> SSE lifecycle while honoring the structured-history rollback flag.
- **FR-009**: The Phase 7 slice MUST ship production runtime/frontend code changes plus automated validation tests; docs-only edits are insufficient.

### Key Entities *(include if feature involves data)*

- **Live Logs Router Metrics**: Best-effort StatsD events emitted for summary latency, history latency/source, and SSE connect/disconnect/error activity.
- **History Source Tag**: The normalized label describing whether `/observability/events` reconstructed history from the journal, shared spool, or artifact fallback.
- **Structured History Rollback Flag**: Runtime-config feature data that tells the frontend whether it should request `/observability/events` or fall back to merged history directly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove `/observability-summary` and `/observability/events` emit the expected latency/source/error metrics and do not mislabel denied requests as successful history reads.
- **SC-002**: Unit tests prove owner access and cross-owner rejection for `/observability/events`.
- **SC-003**: Browser tests prove disabling structured history skips `/observability/events` and falls back to merged history while preserving Live Logs behavior.
- **SC-004**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_task_dashboard_view_model.py`, and scope validation pass.
