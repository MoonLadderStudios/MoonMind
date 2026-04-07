# Feature Specification: codex-managed-session-plane-phase9

**Feature Branch**: `135-codex-managed-session-plane-phase9`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Implement Phase 9 of the Codex Managed Session Plane MVP plan using test-driven development. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read a durable session continuity projection (Priority: P1)

Operators need one server-side session read model for a task-scoped Codex session so Mission Control can render continuity from persisted artifacts and bounded session metadata instead of from live container state.

**Why this priority**: Phase 9 exists to make session continuity a first-class API surface before UI work begins.

**Independent Test**: Call `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}` for a managed session with persisted continuity artifacts and verify the response includes the current session identity, latest continuity refs, and grouped artifact metadata without contacting a live session container.

**Acceptance Scenarios**:

1. **Given** a durable Codex managed-session record exists for `task_run_id` and `session_id`, **When** the session projection endpoint is requested, **Then** the response includes `task_run_id`, `session_id`, the latest `session_epoch`, grouped artifact metadata, and the latest continuity refs required to build the session view.
2. **Given** the managed session container is already gone, **When** the session projection endpoint is requested, **Then** MoonMind still returns the projection from durable artifact metadata and the managed-session record without querying live container state.
3. **Given** the latest summary, checkpoint, or control-event refs are missing individually, **When** the projection endpoint is requested, **Then** MoonMind omits only the missing ref fields while still returning the remaining grouped artifacts and continuity metadata.

---

### User Story 2 - Group continuity artifacts server-side (Priority: P1)

Operators need the backend to group continuity artifacts into a stable read model so the UI does not have to infer grouping or "latest" semantics client-side.

**Why this priority**: Server-defined grouping is the core Phase 9 outcome and the prerequisite for a simple continuity panel in Phase 11.

**Independent Test**: Seed artifact metadata for session summary, step checkpoint, control-event, reset-boundary, stdout, stderr, and diagnostics artifacts, then verify the endpoint returns those artifacts in stable server-defined groups with matching latest refs.

**Acceptance Scenarios**:

1. **Given** a session record stores `runtime.stdout`, `runtime.stderr`, `runtime.diagnostics`, `session.summary`, `session.step_checkpoint`, `session.control_event`, and `session.reset_boundary` refs, **When** the projection endpoint is requested, **Then** MoonMind returns those artifacts grouped by continuity purpose instead of as one flat unordered list.
2. **Given** the latest summary/checkpoint/control refs point at concrete persisted artifacts, **When** the grouped projection is built, **Then** the matching artifact metadata appears in the corresponding groups and the latest refs resolve to those same artifacts.
3. **Given** a clear/reset has advanced the durable `session_epoch`, **When** the grouped projection is built, **Then** the response reports the new epoch and includes the control/reset artifacts needed to render the boundary explicitly.

---

### User Story 3 - Enforce projection ownership and missing-session behavior (Priority: P2)

Operators need the new projection endpoint to follow the same task-run access rules and clear error semantics as the rest of the task-run API surface.

**Why this priority**: The endpoint becomes part of Mission Control and cannot bypass existing ownership or missing-resource rules.

**Independent Test**: Request the endpoint as the task owner, as a non-owner, and for a missing session, then verify success, `403`, and structured `404` behavior respectively.

**Acceptance Scenarios**:

1. **Given** the requesting user owns the task run, **When** the session projection endpoint is requested, **Then** MoonMind returns `200` with the session projection.
2. **Given** the requesting user does not own the task run and is not a superuser, **When** the session projection endpoint is requested, **Then** MoonMind returns `403` without leaking session metadata.
3. **Given** no durable session record matches the requested `task_run_id` and `session_id`, **When** the session projection endpoint is requested, **Then** MoonMind returns `404` with a stable `session_projection_not_found` error code.

### Edge Cases

- The durable managed-session record exists, but only runtime log artifacts are present and no continuity artifacts have been published yet.
- The durable record contains continuity refs whose artifacts were soft-deleted or are otherwise unreadable; the projection must not claim those artifacts are available.
- The requested `session_id` exists for a different `task_run_id`; MoonMind must not cross-wire projections across tasks.
- The latest control event exists without a paired reset-boundary artifact, or vice versa.
- Artifact metadata exists for one session epoch while the durable record has already advanced to a later epoch.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST expose `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}` as the minimal server-side session continuity projection endpoint for task-scoped Codex managed sessions.
- **FR-002**: The session projection response MUST include `task_run_id`, `session_id`, and the latest durable `session_epoch` from the managed-session record.
- **FR-003**: The session projection response MUST include grouped artifact metadata for the latest continuity and runtime artifact refs persisted on the managed-session record.
- **FR-004**: The session projection response MUST include `latest_summary_ref`, `latest_checkpoint_ref`, and `latest_control_event_ref` when those refs exist durably.
- **FR-005**: The session projection response MUST preserve reset visibility by surfacing the latest reset-boundary artifact in the grouped artifact output when that artifact exists durably.
- **FR-006**: The projection implementation MUST resolve artifact metadata from persisted artifacts and bounded session metadata, and MUST NOT require a live container or live session-controller query to build the response.
- **FR-007**: The endpoint MUST enforce the same task-run ownership rules as the existing task-run observability APIs.
- **FR-008**: When no durable managed-session record matches the requested `task_run_id` and `session_id`, the endpoint MUST return `404` with a stable `session_projection_not_found` error code.
- **FR-009**: The Phase 9 implementation MUST remain container-first by adding only a read-model API over persisted artifacts and durable session state; it MUST NOT move Codex execution back into the main worker process.

### Key Entities

- **Artifact Session Projection**: Server-side read model for one task-scoped managed session, containing identity, latest continuity refs, and grouped artifact metadata.
- **Artifact Session Group**: Named server-defined grouping of session artifacts, such as runtime logs/diagnostics or continuity/control artifacts, returned as part of the projection.
- **Managed Session Projection Source**: The combination of the durable managed-session record and persisted artifact metadata used to build the projection without live container access.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests verify `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}` returns a valid session projection with grouped artifacts and the latest continuity refs for a persisted managed session.
- **SC-002**: Tests verify the projection still succeeds when the live session container is absent because the response is built from durable state only.
- **SC-003**: Tests verify the endpoint rejects cross-owner access and returns `session_projection_not_found` for missing or mismatched session/task pairs.
- **SC-004**: Scope validation confirms the Phase 9 diff includes production runtime/API code changes and test coverage for the new projection endpoint.
