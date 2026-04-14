# Feature Specification: Codex Managed Session Phase 0 and Phase 1

**Feature Branch**: `141-codex-managed-session-phase0-1`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 and Phase 1 using test-driven development for the Codex managed session plane rollout. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Align the canonical doc with the production path (Priority: P1)

MoonMind engineers need the managed-session plane doc to describe the current near-term production truth so recovery and operator work do not rely on contradictory assumptions about Temporal, artifacts, and the JSON-backed supervision store.

**Why this priority**: Phase 1 workflow/API changes are unsafe if the canonical doc still implies a production publication/recovery path that the code does not actually use.

**Independent Test**: Read the canonical doc and confirm it explicitly distinguishes audit/operator truth, operational recovery index, and disposable container-local state while naming the controller/supervisor path as the production artifact publisher.

**Acceptance Scenarios**:

1. **Given** the current implementation uses artifacts and bounded workflow metadata for operator truth and a `ManagedSessionStore` record for recovery/reconciliation, **When** the canonical doc is reviewed, **Then** those roles are described explicitly without claiming the JSON record is operator/audit truth.
2. **Given** the transitional in-container `fetch_session_summary()` and `publish_session_artifacts()` path returns empty publication refs today, **When** the doc describes production publication, **Then** the controller/supervisor path is identified as the production publisher and the in-container path is described as fallback-only or non-production.

---

### User Story 2 - Expose the workflow’s canonical typed control surface (Priority: P1)

MoonMind workflow callers need the `MoonMind.AgentSession` workflow to expose the document’s canonical control-plane vocabulary through typed Updates instead of a generic mutating `control_action` signal.

**Why this priority**: The current workflow surface still permits ambiguous mutations and leaves `interrupt_turn` stranded below the workflow boundary, which blocks deterministic validation and contract parity.

**Independent Test**: Exercise the workflow update validators and caller wiring to confirm typed updates exist for send, interrupt, steer, clear, cancel, and terminate actions, and that invalid requests fail fast before activity execution.

**Acceptance Scenarios**:

1. **Given** a task-scoped managed session workflow starts, **When** initialization happens, **Then** handler-visible binding state is available from `@workflow.init` before `run()` progresses.
2. **Given** callers need to mutate session state, **When** they interact with `MoonMind.AgentSession`, **Then** the public mutation surface is `SendFollowUp`, `InterruptTurn`, `SteerTurn`, `ClearSession`, `CancelSession`, and `TerminateSession`, while `attach_runtime_handles` remains a signal.
3. **Given** runtime handles are missing, the session epoch is stale, the active turn is absent for interrupt/steer, or the workflow is terminating, **When** the corresponding update validator runs, **Then** the request is rejected deterministically before activity execution.
4. **Given** an active turn exists, **When** `InterruptTurn` is invoked, **Then** the workflow calls the existing `agent_runtime.interrupt_turn` activity surface and updates workflow-owned session state to reflect the interruption.

### Edge Cases

- A caller invokes a mutating update before runtime handles are attached.
- A caller uses an old `sessionEpoch` after a clear/reset boundary has advanced the session.
- `InterruptTurn` or `SteerTurn` is invoked when there is no active turn.
- `ClearSession` is invoked while a clear operation is already in progress.
- Any mutating update is invoked after cancel or terminate has started.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The canonical `docs/ManagedAgents/CodexCliManagedSessions.md` doc MUST describe artifacts plus bounded workflow metadata as operator/audit truth and `ManagedSessionStore` as the operational recovery/reconciliation index.
- **FR-002**: The canonical doc MUST identify the managed-session controller/supervisor path as the production artifact publisher and MUST not present the transitional in-container summary/publication helpers as the production path.
- **FR-003**: `MoonMind.AgentSession` MUST initialize handler-visible workflow binding state via `@workflow.init`.
- **FR-004**: `MoonMind.AgentSession` MUST replace the generic mutating `control_action` signal with typed Updates for `SendFollowUp`, `InterruptTurn`, `SteerTurn`, `ClearSession`, `CancelSession`, and `TerminateSession`.
- **FR-005**: `attach_runtime_handles` MAY remain as a fire-and-forget signal for runtime-handle propagation, but other state mutations MUST go through typed Updates.
- **FR-006**: The workflow MUST reject invalid update requests at the workflow boundary for missing runtime handles, stale `sessionEpoch`, missing active turn for interrupt/steer, clear-while-clearing, and any mutator after cancel/terminate has started.
- **FR-007**: `InterruptTurn` MUST call the existing `agent_runtime.interrupt_turn` activity surface and update the workflow snapshot to reflect the interruption.
- **FR-008**: Parent workflow, adapter, and API/router callers that control `MoonMind.AgentSession` MUST target the typed update names instead of the removed generic mutating signal.
- **FR-009**: The Phase 0 and Phase 1 slice MUST include production runtime code changes and automated validation tests.

### Key Entities *(include if feature involves data)*

- **Managed Session Truth Surfaces**: the split between operator/audit truth, operational recovery index, and disposable cache state.
- **Agent Session Workflow Input/Binding**: the task-scoped session identity that must be visible to handlers from workflow initialization onward.
- **Typed Session Updates**: the explicit mutation operations exposed by `MoonMind.AgentSession`.
- **Update Validation Context**: runtime handles, current epoch, active turn identity, and workflow termination status used to reject invalid control requests.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The canonical managed-session plane doc names the current production publication and recovery roles without ambiguity.
- **SC-002**: Automated tests verify the workflow no longer depends on the generic mutating `control_action` signal and instead exposes the typed update names required for Phase 1.
- **SC-003**: Automated tests verify invalid session mutations are rejected deterministically at the workflow boundary for missing handles, stale epochs, missing active turns, duplicate clear, and terminating states.
- **SC-004**: Automated tests verify `InterruptTurn` reaches the existing activity surface and updates workflow-owned session state.
