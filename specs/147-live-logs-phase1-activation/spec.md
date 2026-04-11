# Feature Specification: Live Logs Phase 1 Activation

**Feature Branch**: `147-live-logs-phase1-activation`  
**Created**: 2026-04-10  
**Status**: Draft  
**Input**: User description: "$speckit-orchestrate Implement Phase 1 using test-driven development from the Live Logs plan: make Codex managed-session runs produce an active observability record early, publish live events while the turn is still in flight, and keep the existing summary + merged-tail + SSE viewer working. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mission Control attaches before the Codex turn finishes (Priority: P1)

Mission Control operators need a real managed-run observability record as soon as a Codex managed session is ready so the existing Live Logs summary, history, and SSE routes can attach while the turn is still executing.

**Why this priority**: The router and frontend already know how to read an active capable record. The main blocker is that the Codex adapter does not persist one until after the turn, summary fetch, and artifact publication are already done.

**Independent Test**: Start a Codex managed-session run with a `send_turn` stub that pauses before completion and confirm the managed-run store already contains a `running` record with workspace and session metadata plus `liveStreamCapable: true`.

**Acceptance Scenarios**:

1. **Given** a task-scoped Codex managed session has been resolved and the adapter is about to send a turn, **When** the adapter persists the initial managed-run record, **Then** the record is already visible in `running` state with `workspacePath`, `sessionId`, `sessionEpoch`, `containerId`, `threadId`, and `liveStreamCapable: true`.
2. **Given** that active record exists before the turn finishes, **When** Mission Control requests `/api/task-runs/{id}/observability-summary`, **Then** the summary reports live streaming as available instead of waiting for terminal artifact publication.
3. **Given** the run is still active, **When** Mission Control attaches to `/api/task-runs/{id}/logs/stream`, **Then** the route does not reject the run for missing live-stream capability.

---

### User Story 2 - Active Codex turns publish observable session events (Priority: P1)

Mission Control operators need live session-plane events to appear in the same task-run observability stream while the Codex turn is in flight so the existing Live Logs viewer shows visible activity before completion.

**Why this priority**: Early record persistence is not enough if the active run still produces no observable rows until the final summary and artifact snapshot are published.

**Independent Test**: Execute a managed-session run through the real controller/supervisor path or adapter test doubles, emit `session_started`, `turn_started`, and `turn_completed`, and confirm the task-run spool or durable publication path preserves those rows in sequence order.

**Acceptance Scenarios**:

1. **Given** the managed-session controller already emits normalized session events through the session supervisor, **When** a Codex turn starts and completes, **Then** the task-run observability stream contains at least `session_started`, `turn_started`, and `turn_completed` rows in one run-global sequence.
2. **Given** the run later publishes summary and checkpoint artifacts, **When** the final managed-run record is updated, **Then** it retains the durable `observabilityEventsRef` and runtime artifact refs without dropping the earlier live capability or session snapshot fields.
3. **Given** event publication fails for one session row, **When** runtime control still succeeds, **Then** the run continues and artifact publication is not blocked by the observability failure.

---

### User Story 3 - Failure paths stay truthful for in-flight observability (Priority: P2)

Operators need failed or aborted turns to leave behind an accurate managed-run record instead of a stale `running` entry once the adapter encounters a send-turn failure.

**Why this priority**: Persisting the record earlier introduces a new failure window. That window must end in a truthful terminal state rather than leaving Mission Control attached to a run that already failed.

**Independent Test**: Force `send_turn` to return a failed status after the early record has been written and confirm the managed-run store moves the record to a terminal failure state with the same workspace and session metadata.

**Acceptance Scenarios**:

1. **Given** the adapter persisted a `running` record before `_send_turn(...)`, **When** the managed turn later fails, **Then** the managed-run record is updated to a terminal non-success state instead of remaining `running`.
2. **Given** the failed run already advertised live capability, **When** the run becomes terminal, **Then** observability summary truthfully reports live streaming as ended.

### Edge Cases

- A Codex turn returns a failed status before any summary or artifact publication; the early record must still be cleaned up into a truthful terminal state.
- Session events may exist in the task-run spool before a durable observability journal exists; active history retrieval must keep degrading through spool-based reads.
- A run may have session metadata in the managed-session store but not yet in final task-run artifact metadata; summary and stream routes must still have enough bounded context to operate.
- Observability publication failures must remain non-fatal for runtime control and artifact publication.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `CodexSessionAdapter.start()` MUST persist a managed-run record in `running` state before awaiting the managed `send_turn` completion path.
- **FR-002**: The early managed-run record MUST set `liveStreamCapable` to `true` for Codex managed-session runs with a writable workspace-backed spool path.
- **FR-003**: The early managed-run record MUST include `workspacePath`, `sessionId`, `sessionEpoch`, `containerId`, `threadId`, and `activeTurnId` when available from the bounded session snapshot.
- **FR-004**: Codex managed-session lifecycle events emitted during launch and turn execution MUST remain visible in the task-run observability stream while the run is active.
- **FR-005**: The final managed-run record MUST retain durable runtime artifact refs and `observabilityEventsRef` after artifact publication without regressing the live-stream capability contract for active runs.
- **FR-006**: If the Codex managed turn fails after the early record is written, the adapter MUST update the managed-run record to a truthful terminal failure state.
- **FR-007**: This phase MUST ship production runtime code changes under `moonmind/` and automated validation tests under `tests/`; docs-only edits are insufficient.

### Key Entities *(include if feature involves data)*

- **Early Managed Run Record**: The first persisted `ManagedRunRecord` for a Codex managed-session turn, written before turn completion so Mission Control can attach.
- **Task-Run Observability Stream**: The workspace-backed spool and durable event history used by summary, structured history, merged fallback, and SSE routes.
- **Session Snapshot Fields**: The bounded `sessionId`, `sessionEpoch`, `containerId`, `threadId`, and `activeTurnId` values mirrored onto task-run observability records.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Adapter tests prove a `running` managed-run record exists before `send_turn` completes and that the record advertises `liveStreamCapable: true`.
- **SC-002**: Adapter or controller boundary tests prove `session_started`, `turn_started`, and `turn_completed` are published into the task-run observability stream for Codex managed-session runs.
- **SC-003**: Router-facing tests continue to show active capable runs report `supportsLiveStreaming: true` and terminal runs report live streaming as ended.
- **SC-004**: `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` passes for the final implementation.
