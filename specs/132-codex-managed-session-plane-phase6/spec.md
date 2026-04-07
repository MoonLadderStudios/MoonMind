# Feature Specification: codex-managed-session-plane-phase6

**Feature Branch**: `132-codex-managed-session-plane-phase6`
**Created**: 2026-04-06
**Status**: Draft
**Input**: User description: "Implement Phase 6 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Persist Supervised Session State (Priority: P1)

The agent-runtime fleet needs a durable session-level record for each task-scoped Codex session so container identity, runtime metadata, and artifact refs survive beyond a single activity invocation.

**Why this priority**: Phase 6 exists to move from a one-shot launch/controller path to a session-level supervision model that can survive worker restarts and drive continuity views later.

**Independent Test**: Launch a managed session through the controller and verify MoonMind persists a durable session record containing session identity, task run id, container id, runtime id, image ref, and initial status.

**Acceptance Scenarios**:

1. **Given** `agent_runtime.launch_session` launches a Codex session container, **When** the launch completes, **Then** MoonMind persists a durable session record keyed by `session_id` with the launched `container_id`, `task_run_id`, `runtime_id`, and `image_ref`.
2. **Given** a later control action returns an updated typed handle, **When** MoonMind records the transition, **Then** the durable session record reflects the latest `session_epoch`, `thread_id`, status, and error metadata without losing prior artifact refs.
3. **Given** a session summary is requested after launch, **When** no artifacts have been published yet, **Then** MoonMind still returns the durable session state and explicit `None` refs rather than fabricating continuity data from container-local state.

---

### User Story 2 - Supervise Session Logs And Diagnostics (Priority: P1)

MoonMind needs a session-level supervisor that watches the managed session workspace, publishes stdout/stderr/diagnostics artifacts, and tracks log offsets independently from per-run process supervision.

**Why this priority**: Logs and diagnostics must stay artifact-first even when the Codex session container persists across steps, and this phase is where the long-lived session path gains the same operational guarantees as managed runs.

**Independent Test**: Start supervision for a persisted session record with append-only stdout/stderr spool files, append output, finalize supervision, and verify stdout/stderr/diagnostics artifact refs plus `last_log_at` and `last_log_offset` are persisted.

**Acceptance Scenarios**:

1. **Given** an active managed session record and writable spool files, **When** the supervisor observes appended output, **Then** it updates `last_log_at` and `last_log_offset` as bounded durable metadata.
2. **Given** supervision is finalized for a session, **When** artifact publication completes, **Then** the session record stores `stdout_artifact_ref`, `stderr_artifact_ref`, and `diagnostics_ref`.
3. **Given** `agent_runtime.fetch_session_summary` or `agent_runtime.publish_session_artifacts` is called, **When** MoonMind serves the request, **Then** the response is sourced from the durable supervised session record and artifact refs rather than a container-private cache.

---

### User Story 3 - Reconcile Active Sessions After Worker Restart (Priority: P2)

The agent-runtime worker needs to reconcile active supervised sessions on startup so an existing container can be reattached to supervision, or the session can degrade gracefully if the container is gone.

**Why this priority**: The core Phase 6 exit criterion is restart safety for the session architecture.

**Independent Test**: Persist an active session record, simulate worker startup reconciliation with one existing container and one missing container, and verify MoonMind reattaches supervision for the existing container while marking the missing one degraded.

**Acceptance Scenarios**:

1. **Given** an active durable session record whose container still exists, **When** worker startup reconciliation runs, **Then** the session supervisor reattaches to the session and continues tracking output.
2. **Given** an active durable session record whose container no longer exists, **When** reconciliation runs, **Then** MoonMind marks the session record as degraded or terminal with an explicit error summary instead of silently discarding it.
3. **Given** the worker bootstrap builds the `agent_runtime` fleet, **When** startup completes, **Then** both managed-run reconciliation and managed-session reconciliation have executed before activity handlers begin servicing requests.

### Edge Cases

- Session spool files do not exist yet at launch time but appear after the first control action.
- A session is terminated before any stdout or stderr bytes are written.
- Reconciliation finds a persisted active session record with malformed offsets or missing metadata.
- Artifact publication runs after the container is already gone and must use only persisted spool content plus durable metadata.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST persist a durable session-level supervision record for each task-scoped Codex managed session.
- **FR-002**: The durable session record MUST include at least `session_id`, `session_epoch`, `task_run_id`, `container_id`, `image_ref`, `runtime_id`, `status`, `stdout_artifact_ref`, `stderr_artifact_ref`, `diagnostics_ref`, `last_log_at`, and `last_log_offset`.
- **FR-003**: MoonMind MUST supervise managed-session stdout/stderr output independently from the existing per-run process supervisor.
- **FR-004**: Session supervision MUST publish stdout, stderr, and diagnostics as artifacts and persist their refs in the durable session record.
- **FR-005**: `agent_runtime.fetch_session_summary` and `agent_runtime.publish_session_artifacts` MUST return continuity refs sourced from the durable session record.
- **FR-006**: Worker startup MUST reconcile active durable session records by reattaching supervision when the container still exists.
- **FR-007**: Worker startup MUST degrade gracefully when a persisted active session container is missing or cannot be reattached.
- **FR-008**: The Phase 6 implementation MUST preserve the existing typed managed-session activity contracts and MUST NOT route the new session path through `ManagedRuntimeLauncher`.

### Key Entities

- **Codex Managed Session Record**: The durable session-level supervision record keyed by `session_id`.
- **Codex Managed Session Supervisor**: The background session observer that tracks spool progress, publishes artifacts, and records diagnostics.
- **Session Spool Files**: The append-only stdout/stderr files under the managed session artifact spool path that provide restart-safe session observability input.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Launch-time tests verify a durable session record is written with the required Phase 6 metadata.
- **SC-002**: Supervision tests verify stdout/stderr/diagnostics artifacts and log offsets are persisted from session spool files.
- **SC-003**: Summary/publication tests verify session continuity responses come from durable record state.
- **SC-004**: Worker startup tests verify session reconciliation reattaches or degrades gracefully on restart.
