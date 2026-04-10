# Feature Specification: codex-managed-session-plane-phase5

**Feature Branch**: `132-codex-managed-session-plane-phase5`
**Created**: 2026-04-06
**Status**: Draft
**Input**: User description: "Implement Phase 5 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run A Managed Codex Step Through The Session Adapter (Priority: P1)

The agent-run workflow needs a dedicated Codex session adapter so one managed Codex step can create or attach to the task-scoped session container, send the step instructions through the remote session control surface, and collect a typed result without falling back to the worker-local managed-runtime launcher path.

**Why this priority**: Phase 5 is only complete when a real managed Codex step executes through the managed-session plane. Without this slice, Phases 2 through 4 remain disconnected infrastructure.

**Independent Test**: Execute the adapter with a managed Codex request that includes a task-scoped managed-session binding and verify it launches or reuses the session, sends one turn through the session controller, persists a managed run result, and returns a canonical `AgentRunHandle`.

**Acceptance Scenarios**:

1. **Given** a managed Codex request with no launched container yet, **When** `CodexSessionAdapter.start()` runs, **Then** it launches the task-scoped session container, signals the session workflow with the runtime handles, sends the step instructions through `send_turn`, and returns a canonical managed run handle.
2. **Given** a managed Codex request whose task-scoped session already has container and thread handles, **When** `CodexSessionAdapter.start()` runs, **Then** it reuses the existing session via the remote session control surface instead of launching a new session container.
3. **Given** a managed Codex request bound to a task-scoped session, **When** the adapter executes the step, **Then** no code path routes through `ManagedRuntimeLauncher.launch()` or a worker-local Codex subprocess loop.

---

### User Story 2 - Keep Session Control First-Class At The Adapter Boundary (Priority: P1)

The Codex session adapter needs explicit clear/reset, interrupt/cancel, summary, and termination methods so workflow-level code can keep talking in session-control actions while the execution loop remains inside the session container.

**Why this priority**: The adapter is the stable boundary MoonMind intends to keep after the session image changes. It must expose the session vocabulary directly instead of burying it in workflow glue.

**Independent Test**: Invoke the adapter’s clear, interrupt, summary, and terminate methods against a mocked task-scoped session and confirm each one delegates to the session control surface, updates the task-scoped session workflow state, and preserves the typed managed-session contracts.

**Acceptance Scenarios**:

1. **Given** a launched task-scoped session, **When** `CodexSessionAdapter.clear_session()` is invoked, **Then** it issues `clear_session` against the remote session container, advances the logical thread boundary, and signals the `MoonMind.AgentSession` workflow with the new epoch/thread handles.
2. **Given** an in-flight turn in a launched task-scoped session, **When** `CodexSessionAdapter.cancel()` or `interrupt_turn()` is invoked, **Then** it delegates to the remote session control surface and records a canonical canceled/interrupted run outcome.
3. **Given** a launched task-scoped session, **When** `CodexSessionAdapter.fetch_session_summary()` or `terminate_session()` is invoked, **Then** it delegates through the remote session control surface and updates the `MoonMind.AgentSession` workflow state instead of reading local worker process state.

---

### User Story 3 - Route Managed Codex Steps Through The Adapter In `MoonMind.AgentRun` (Priority: P2)

The managed-runtime branch of `MoonMind.AgentRun` needs to choose the Codex session adapter whenever a managed Codex step has a task-scoped session binding, so the workflow uses the session plane transparently while keeping the existing managed-runtime workflow shape.

**Why this priority**: The adapter is not useful unless the real workflow path uses it by default for task-scoped managed Codex steps.

**Independent Test**: Run `MoonMind.AgentRun` with a managed Codex request containing `managedSession` and verify it instantiates `CodexSessionAdapter`, not `ManagedAgentAdapter`, and publishes a canonical `AgentRunResult` from the adapter-backed execution path.

**Acceptance Scenarios**:

1. **Given** `MoonMind.AgentRun` receives a managed Codex request with `managedSession`, **When** it starts the step, **Then** it instantiates `CodexSessionAdapter` and does not instantiate `ManagedAgentAdapter`.
2. **Given** `MoonMind.AgentRun` uses `CodexSessionAdapter`, **When** the step completes, **Then** the workflow still publishes a canonical `AgentRunResult` and preserves managed-session metadata on the result.
3. **Given** `MoonMind.AgentRun` receives a managed non-session runtime request, **When** it starts the step, **Then** it continues using the existing `ManagedAgentAdapter` path unchanged.

### Edge Cases

- The task-scoped session workflow exists but has no runtime handles yet.
- The session workflow has handles, but `session_status` reports the container is no longer usable.
- The adapter is asked to clear or terminate a session before any container was launched.
- Session summary or artifact publication returns empty refs; the adapter must degrade honestly and keep the canonical result valid.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST implement a `CodexSessionAdapter` that satisfies the `AgentAdapter` lifecycle for managed Codex steps bound to a task-scoped managed session.
- **FR-002**: `CodexSessionAdapter.start()` MUST create or attach to a task-scoped remote Codex session container through the session control surface and MUST NOT invoke a worker-local managed-runtime launcher/process loop.
- **FR-003**: `CodexSessionAdapter` MUST resolve the managed provider profile and shape launch-time environment overrides for the session container using the same provider-profile inputs used by managed runtimes.
- **FR-004**: `CodexSessionAdapter` MUST synchronize launched container/thread/turn handles back into `MoonMind.AgentSession` through workflow signals or queries rather than storing them only in adapter-local memory.
- **FR-005**: `CodexSessionAdapter` MUST provide explicit adapter methods for send/start, interrupt/cancel, clear/reset, session-summary retrieval, and termination against the remote session control surface.
- **FR-006**: `CodexSessionAdapter` MUST persist canonical managed run status/result data so `MoonMind.AgentRun` can read step completion through the adapter boundary without reconstructing provider-native payloads in workflow code.
- **FR-007**: `MoonMind.AgentRun` MUST instantiate `CodexSessionAdapter` for managed Codex requests that include `managedSession`, and MUST keep the existing `ManagedAgentAdapter` path for managed requests without a task-scoped session binding.
- **FR-008**: The Phase 5 implementation MUST preserve the Phase 3 typed session activity contracts and the Phase 4 Docker-backed session controller as the only control path.
- **FR-009**: The adapter MUST keep the image reference and launch path deployment-configurable so later switching from the current MoonMind image to a dedicated Codex image remains a packaging change rather than an orchestration redesign.

### Key Entities

- **Codex Session Adapter**: The workflow-side adapter that maps the `AgentAdapter` lifecycle and explicit session-control methods onto the managed-session activity surface.
- **Session Execution Record**: The persisted canonical run/result state for one managed Codex step executed through the task-scoped session plane.
- **Task-Scoped Session Snapshot**: The `MoonMind.AgentSession` workflow-owned view of the current container, thread, active turn, and epoch state used by the adapter to create or attach to the session.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Focused adapter tests prove a managed Codex step launches or reuses a task-scoped session container and executes one turn through the remote session control surface.
- **SC-002**: Focused adapter tests prove clear/reset, interrupt/cancel, summary, and terminate calls all delegate through the remote session control surface and update the task-scoped session workflow state.
- **SC-003**: Workflow-boundary tests prove `MoonMind.AgentRun` uses `CodexSessionAdapter` for managed Codex requests with `managedSession` and continues to use `ManagedAgentAdapter` otherwise.
- **SC-004**: The final diff passes runtime scope validation with production runtime file changes and validation test changes.
